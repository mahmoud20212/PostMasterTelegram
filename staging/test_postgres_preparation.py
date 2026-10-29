from datetime import datetime, timezone
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest import skipUnless
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time as clock

from django.contrib.auth.models import Group, Permission, User as AdminUser
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections, close_old_connections, transaction, IntegrityError
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from config.database import database_config
from publishing.models import PublishingRun
from users.models import User
from staging.migration_tools import (
    LosslessEncoder, TransferError, assert_pristine_target, digest, load_records,
    snapshot, transfer_models, verify_sequences,
)
from staging.sqlite_snapshot import backup
from publishing.test_session_safety import SessionFixtures
from publishing.services import engine, runs


class DatabaseConfigTests(SimpleTestCase):
    def test_sqlite_remains_default_and_ignores_pg_credentials_without_opt_in(self):
        self.assertEqual(database_config({"POSTGRES_PASSWORD": "synthetic"}, Path("/app")),
                         {"ENGINE": "django.db.backends.sqlite3", "NAME": Path("/app/db.sqlite3")})

    def test_postgres_requires_complete_explicit_configuration(self):
        with self.assertRaises(ValueError):
            database_config({"DATABASE_BACKEND": "postgresql"}, Path("/app"))
        config = database_config(dict(DATABASE_BACKEND="postgresql", POSTGRES_DB="local",
                    POSTGRES_USER="local", POSTGRES_PASSWORD="synthetic", POSTGRES_HOST="isolated"), Path("/app"))
        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["CONN_MAX_AGE"], 0)

    def test_command_refuses_non_staging_settings_before_connecting(self):
        with self.assertRaises(CommandError):
            call_command("pg_transfer", apply=True)

    def test_snapshot_does_not_overwrite_source_or_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "source.sqlite3", Path(directory) / "backup.sqlite3"
            with closing(sqlite3.connect(source)) as db:
                with db:
                    db.execute("CREATE TABLE fixture (id INTEGER PRIMARY KEY, value TEXT)")
                    db.execute("INSERT INTO fixture VALUES (9, 'synthetic')")
            original = source.read_bytes()
            backup(source, target)
            self.assertEqual(source.read_bytes(), original)
            with closing(sqlite3.connect(target)) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM fixture").fetchone()[0], 1)
            with self.assertRaises(FileExistsError):
                backup(source, target)
            with self.assertRaises(ValueError):
                backup(source, source)

    def test_encoder_keeps_all_datetime_microseconds(self):
        now = datetime(2026, 1, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)
        self.assertIn("123456", json.dumps(now, cls=LosslessEncoder))


class TransferSerializationTests(TestCase):
    def test_auth_through_pks_and_all_fields_round_trip(self):
        admin = AdminUser.objects.create(username="synthetic", password="!", is_staff=True)
        group = Group.objects.create(name="synthetic")
        group.permissions.add(Permission.objects.first())
        admin.groups.add(group)
        admin.user_permissions.add(Permission.objects.last())
        user = User.objects.create(id=501, telegram_user_id=900000001)
        from telegram_accounts.models import TelegramAccount
        account = TelegramAccount.objects.create(user=user, phone_number="+10000000501", encrypted_session="SYNTHETIC")
        PublishingRun.objects.create(user=user, account=account, task_claimed=True)
        records, counts = snapshot("default")
        self.assertEqual(counts["auth.user_groups"], 1)
        self.assertEqual(counts["auth.group_permissions"], 1)
        self.assertTrue(any(m._meta.auto_created for m in transfer_models()))
        User.objects.filter(pk=user.pk).update(first_name="changed")
        load_records(records, "default")
        actual, actual_counts = snapshot("default")
        self.assertEqual(counts, actual_counts)
        self.assertEqual(digest(records), digest(actual))
        self.assertEqual(admin.groups.get().pk, group.pk)

    def test_nonempty_target_is_refused(self):
        User.objects.create(telegram_user_id=55)
        with self.assertRaises(TransferError):
            assert_pristine_target("default")

    @skipUnless(connection.vendor == "postgresql", "requires isolated PostgreSQL staging")
    def test_postgres_sequence_reset_and_unique_active_run(self):
        from django.core.management.color import no_style
        from telegram_accounts.models import TelegramAccount
        user = User.objects.create(id=9001, telegram_user_id=900000001)
        account = TelegramAccount.objects.create(user=user, phone_number="+10000000501")
        PublishingRun.objects.create(user=user, account=account)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PublishingRun.objects.create(user=user, account=account)
        with connection.cursor() as cursor:
            for sql in connection.ops.sequence_reset_sql(no_style(), transfer_models()):
                cursor.execute(sql)
        verify_sequences("default")
        self.assertGreater(User.objects.create(telegram_user_id=900000002).pk, user.pk)


@skipUnless(connection.vendor == "postgresql", "requires real PostgreSQL lock semantics")
class PostgreSQLFencingTests(SessionFixtures, TransactionTestCase):
    def stale_write_after_restart(self, stale_operation, table, *, legacy=False):
        old = self.start()
        if legacy:
            runs.finish_run(101)
            User.objects.filter(pk=self.user.pk).update(is_publishing=True)
        ready = Event()
        pids = []

        def stale():
            close_old_connections()
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.append(cursor.fetchone()[0])
                ready.set()
                stale_operation(old)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    key = "id" if table == "users_user" else "user_id"
                    cursor.execute(f'SELECT id FROM "{table}" WHERE "{key}"=%s FOR UPDATE', [self.user.pk])
                future = executor.submit(stale)
                self.assertTrue(ready.wait(3))
                limit = clock.monotonic() + 3
                blocked = False
                while clock.monotonic() < limit:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_stat_clear_snapshot()")
                        cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", [pids[0]])
                        blocked = cursor.fetchone()[0] == "Lock"
                    if blocked:
                        break
                    clock.sleep(0.01)
                self.assertTrue(blocked, "stale SQL statement did not reach lock wait")
                runs.finish_run(101)
                new = self.start()
            future.result(timeout=5)
        self.assertTrue(runs.run_is_active.func(101, new.pk))
        return new

    def test_stale_finish_cannot_stop_new_run_after_waiting_for_lock(self):
        self.stale_write_after_restart(lambda run: runs.finish_run(101, run.pk), "users_user")

    def test_legacy_recovery_cannot_invalidate_concurrent_new_run(self):
        self.stale_write_after_restart(lambda run: runs.recover_legacy_user(101), "users_user", legacy=True)

    def test_stale_cursor_cannot_overwrite_new_run_after_waiting_for_lock(self):
        self.stale_write_after_restart(
            lambda run: engine.advance_cycle_indices.func(telegram_user_id=101, run_id=run.pk,
                                                         message_index=99, group_index=99),
            # All ownership-dependent writes now serialize on the user FIRST.
            "users_user",
        )
        from publishing.models import PublishingSettings
        self.assertEqual(PublishingSettings.objects.get(user=self.user).current_message_index, 0)
