"""Offline, small-database transfer helpers. Never print serialized records."""
import hashlib
import json
from datetime import datetime, time, timezone

from django.apps import apps
from django.core import serializers
from django.core.serializers.json import DjangoJSONEncoder
from django.core.management.color import no_style
from django.db import connections, transaction
from django.db.migrations.recorder import MigrationRecorder


class TransferError(Exception):
    pass


class LosslessEncoder(DjangoJSONEncoder):
    def default(self, value):
        # DjangoJSONEncoder normally trims datetimes to milliseconds.
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                value = value.astimezone(timezone.utc)
            return value.isoformat(timespec="microseconds")
        if isinstance(value, time):
            return value.isoformat(timespec="microseconds")
        return super().default(value)


def transfer_models():
    return sorted((model for model in apps.get_models(include_auto_created=True)
                   if model._meta.managed and not model._meta.proxy), key=lambda m: m._meta.label_lower)


def migration_set(alias):
    return set(MigrationRecorder(connections[alias]).applied_migrations())


def snapshot(alias):
    connection = connections[alias]
    models = transfer_models()
    known = {m._meta.db_table for m in models} | {"django_migrations"}
    if set(connection.introspection.table_names()) - known:
        raise TransferError("Unknown tables present; an explicit mapping is required")
    records = []
    counts = {}
    for model in models:
        # Transfer auto-created through tables explicitly to preserve THEIR PKs.
        # Exclude parent M2M serialization, which would recreate those links.
        data = serializers.serialize("json", model._base_manager.using(alias).order_by("pk"),
                                     fields=[f.name for f in model._meta.local_fields], cls=LosslessEncoder)
        rows = json.loads(data)
        counts[model._meta.label_lower] = len(rows)
        records.extend(rows)
    return records, counts


def digest(records):
    ordered = sorted(records, key=lambda r: (r["model"], str(r["pk"])))
    return hashlib.sha256(json.dumps(ordered, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def load_records(records, alias):
    for record in serializers.deserialize("json", json.dumps(records), using=alias):
        record.save(using=alias)


def assert_pristine_target(alias):
    for model in transfer_models():
        rows = model._base_manager.using(alias)
        if model._meta.label_lower in ("contenttypes.contenttype", "auth.permission"):
            continue
        if model._meta.label_lower == "subscriptions.subscriptionplan":
            if rows.exclude(code="free").exists() or rows.count() > 1:
                raise TransferError("Target has non-baseline plan records")
            continue
        if rows.exists():
            raise TransferError("Target has application data; refusing replacement")


def verify_sequences(alias):
    connection = connections[alias]
    from django.db.models import AutoField
    with connection.cursor() as cursor:
        for model in transfer_models():
            if not isinstance(model._meta.pk, AutoField):
                continue
            cursor.execute("SELECT pg_get_serial_sequence(%s, %s)", [model._meta.db_table, model._meta.pk.column])
            sequence = cursor.fetchone()[0]
            if not sequence:
                raise TransferError("Missing primary-key sequence")
            cursor.execute("SELECT pg_sequence_last_value(%s::regclass)", [sequence])
            last = cursor.fetchone()[0]
            table = connection.ops.quote_name(model._meta.db_table)
            pk = connection.ops.quote_name(model._meta.pk.column)
            cursor.execute(f"SELECT MAX({pk}) FROM {table}")
            maximum = cursor.fetchone()[0]
            if maximum is not None and (last is None or last < maximum):
                raise TransferError("Sequence trails imported primary keys")


def transfer(*, apply=False):
    source, target = connections["source"], connections["default"]
    if (source.vendor != "sqlite" or target.vendor != "postgresql"
            or target.settings_dict["HOST"] != "staging-postgres"
            or target.settings_dict["NAME"] != "onepost_staging"
            or "mode=ro" not in str(source.settings_dict["NAME"])):
        raise TransferError("Only the isolated read-only-source staging transfer is allowed")
    with source.cursor() as cursor:
        if cursor.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise TransferError("SQLite integrity check failed")
        if cursor.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise TransferError("SQLite foreign-key check failed")
    if migration_set("source") != migration_set("default"):
        raise TransferError("Source/target migration histories differ; use identical application/schema versions")
    records, counts = snapshot("source")
    with transaction.atomic(using="default"):
        tables = ", ".join(target.ops.quote_name(m._meta.db_table) for m in transfer_models())
        with target.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '3s'")
            cursor.execute("SET LOCAL statement_timeout = '60s'")
            # Lock before checking emptiness: a concurrent writer must not sneak
            # records in between the baseline check and replacement.
            mode = "ACCESS EXCLUSIVE" if apply else "SHARE"
            cursor.execute(f"LOCK TABLE {tables} IN {mode} MODE")
        if apply:
            assert_pristine_target("default")
            with target.cursor() as cursor:
                # No CASCADE: unknown dependencies must fail, not be discarded.
                cursor.execute(f"TRUNCATE {tables} RESTART IDENTITY")
                cursor.execute("SET CONSTRAINTS ALL DEFERRED")
            load_records(records, "default")
            with target.cursor() as cursor:
                for sql in target.ops.sequence_reset_sql(no_style(), transfer_models()):
                    cursor.execute(sql)
        target.check_constraints()
        with target.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM pg_constraint WHERE connamespace = 'public'::regnamespace AND NOT convalidated")
            if cursor.fetchone()[0]:
                raise TransferError("Unvalidated PostgreSQL constraints")
        actual, actual_counts = snapshot("default")
        if actual_counts != counts or digest(actual) != digest(records):
            raise TransferError("Row counts or field values differ; transfer not accepted")
        verify_sequences("default")
    return counts
