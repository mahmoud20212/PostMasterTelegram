import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.contrib.staticfiles.storage import staticfiles_storage
from django.test import Client, SimpleTestCase, override_settings

from production.backup import backup, restore
from production.runtime import SECRET_NAMES, https_origin, load_environment, public_ip
from production.setup import create_configuration
from production.tls import issue_command, signal_reload

ROOT = Path(__file__).resolve().parent.parent
FILES = dict(zip(SECRET_NAMES, ["django_secret_key", "db_password", "redis_password", "telegram_bot_token",
    "telegram_api_id", "telegram_api_hash", "session_key"]))


def configuration(root):
    return create_configuration(root, "8.8.8.8", "test@onepost.test", "123456:" + "x" * 35, "12345", "a" * 32)


def runtime_environment(root):
    return {"PUBLIC_IP": "8.8.8.8", **{name + "_FILE": str(root / ".production/secrets" / path) for name, path in FILES.items()}}


def production_settings(**changes):
    env = {"DJANGO_SECRET_KEY": "test-only-" + "x" * 64, "PUBLIC_IP": "8.8.8.8",
        "DATABASE_URL": "postgresql://onepost:test-only@db:5432/onepost"}
    env.update(changes)
    spec = importlib.util.spec_from_file_location("config.settings._production_probe", ROOT / "config/settings/production.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, env, clear=True):
        spec.loader.exec_module(module)
    return module


class ConfigurationTests(unittest.TestCase):
    def test_setup_is_independent_and_never_overwrites_local_or_production_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".env").write_text("original-local-secret", encoding="ascii")
            (root / "db.sqlite3").write_bytes(b"original-data")
            config = configuration(root)
            self.assertNotIn("original-local-secret", config.read_text())
            directory = root / ".production/secrets"
            before = {file.name: file.read_bytes() for file in directory.iterdir()}
            self.assertEqual(len(base64.urlsafe_b64decode(before["session_key"].strip())), 32)
            self.assertNotEqual(before["db_password"], before["db_admin_password"])
            with self.assertRaises(FileExistsError):
                configuration(root)
            self.assertEqual(before, {file.name: file.read_bytes() for file in directory.iterdir()})
            self.assertEqual((root / ".env").read_text(), "original-local-secret")
            self.assertEqual((root / "db.sqlite3").read_bytes(), b"original-data")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((directory / "session_key").stat().st_mode), 0o444)

    def test_invalid_bootstrap_inputs_create_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                create_configuration(root, "127.0.0.1", "test@onepost.test", "invalid", "1", "a" * 32)
            self.assertEqual(list(root.iterdir()), [])

    def test_runtime_reads_secret_files_and_uses_internal_services(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            configuration(root)
            env = load_environment(runtime_environment(root))
            self.assertIn("@db:5432/onepost", env["DATABASE_URL"])
            self.assertIn("@redis:6379/0", env["REDIS_URL"])
            self.assertEqual(env["DJANGO_SETTINGS_MODULE"], "config.settings.production")
            self.assertEqual(env["PUBLISHING_USE_BEAT"], "false")

    def test_runtime_rejects_missing_conflicting_or_invalid_secrets_without_exposing_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            configuration(root)
            for change in ({"DB_PASSWORD_FILE": str(root / "missing")},
                           {"DB_PASSWORD": "must-not-be-printed"},
                           {"TELEGRAM_SESSION_ENCRYPTION_KEY_FILE": ""}, {"PUBLIC_IP": "127.0.0.1"}):
                with self.subTest(change=list(change)):
                    env = {**runtime_environment(root), **change}
                    with self.assertRaises(ValueError) as error:
                        load_environment(env)
                    self.assertNotIn("must-not-be-printed", str(error.exception))

    def test_only_routable_addresses_are_accepted_and_ipv6_origin_is_bracketed(self):
        for value in ("", "0.0.0.0", "127.0.0.1", "192.168.1.1", "https://8.8.8.8", "8.8.8.8;bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                public_ip(value)
        self.assertEqual(https_origin(public_ip("2606:4700:4700::1111")), "https://[2606:4700:4700::1111]")

    def test_production_uses_https_correct_storage_and_console_logs(self):
        config = production_settings()
        self.assertFalse(config.DEBUG)
        self.assertTrue(config.SECURE_SSL_REDIRECT)
        self.assertTrue(config.SESSION_COOKIE_SECURE)
        self.assertTrue(config.CSRF_COOKIE_SECURE)
        self.assertEqual(config.ALLOWED_HOSTS, ["8.8.8.8"])
        self.assertEqual(config.CSRF_TRUSTED_ORIGINS, ["https://8.8.8.8"])
        self.assertEqual(config.STORAGES["staticfiles"]["BACKEND"], "whitenoise.storage.CompressedManifestStaticFilesStorage")
        self.assertEqual(set(config.LOGGING["handlers"]), {"console"})
        self.assertEqual(production_settings(PUBLIC_IP="2606:4700:4700::1111").ALLOWED_HOSTS, ["[2606:4700:4700::1111]"])

    def test_production_rejects_sqlite_missing_database_and_missing_key(self):
        for changes in ({"DATABASE_URL": "sqlite:///:memory:"}, {"DATABASE_URL": ""}, {"DJANGO_SECRET_KEY": ""}):
            with self.subTest(changes=list(changes)), self.assertRaises(ImproperlyConfigured):
                production_settings(**changes)

    def test_tls_uses_ip_webroot_shortlived_and_separates_staging(self):
        env = {"PUBLIC_IP": "8.8.8.8", "ACME_EMAIL": "test@onepost.test"}
        command = issue_command(env)
        self.assertEqual(command[command.index("--ip-address") + 1], "8.8.8.8")
        self.assertEqual(command[command.index("--preferred-profile") + 1], "shortlived")
        self.assertIn("--webroot", command)
        self.assertIn("--deploy-hook", command)
        self.assertNotIn("--staging", command)
        staged = issue_command(env, staging=True)
        self.assertEqual(staged[staged.index("--config-dir") + 1], "/etc/letsencrypt/staging")
        with self.assertRaises(ValueError):
            issue_command({**env, "PUBLIC_IP": "127.0.0.1"})

    def test_successful_certificate_signal_is_atomic_and_changes_each_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            signal_reload(root)
            first = (root / "reload").read_text()
            signal_reload(root)
            self.assertNotEqual(first, (root / "reload").read_text())
            self.assertEqual([path.name for path in root.iterdir()], ["reload"])

    def test_backup_is_binary_safe_and_restore_refuses_running_applications(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def dump(*_, **kwargs):
                kwargs["stdout"].write(b"PGDMP\x00\xff\r\n")

            with patch("production.backup.subprocess.run", side_effect=dump):
                path = backup(root)
            self.assertEqual(path.read_bytes(), b"PGDMP\x00\xff\r\n")
            with patch("production.backup.subprocess.run", return_value=SimpleNamespace(stdout='{"Service":"worker"}')) as run:
                with self.assertRaises(ValueError):
                    restore(path, "onepost", root)
                self.assertEqual(run.call_count, 1)
            with patch("production.backup.subprocess.run") as run:
                with self.assertRaises(ValueError):
                    restore(path, "wrong", root)
                run.assert_not_called()
            with patch("production.backup.subprocess.run", return_value=SimpleNamespace(stdout='[{"Service":"db"},{"Service":"redis"}]')) as run:
                restore(path, "onepost", root)
                self.assertIn("--role=onepost", run.call_args.args[0])
                self.assertIn("--exit-on-error", run.call_args.args[0])

    def test_build_context_is_allowlisted_and_scripts_have_unix_line_endings(self):
        rules = (ROOT / ".dockerignore").read_text().splitlines()
        effective = [line for line in rules if line and not line.startswith("#")]
        self.assertEqual(effective[0], "**")
        self.assertNotIn("!.env", effective)
        self.assertNotIn("!.production/**", effective)
        for rule in ("**/.env*", "**/*.session*", "**/*.sqlite3*", "**/*.pgdump", "**/*.pem", "**/*.key"):
            self.assertIn(rule, effective)
        self.assertIn("!notifications/**", effective)
        self.assertIn("!publishing/**", effective)
        self.assertIn("USER 10001:10001", (ROOT / "Dockerfile").read_text())
        shell = shutil.which("sh") or shutil.which("bash")
        if os.name == "nt" and Path("C:/Program Files/Git/bin/bash.exe").exists():
            shell = "C:/Program Files/Git/bin/bash.exe"
        for path in (ROOT / "production").glob("*.sh"):
            self.assertNotIn(b"\r\n", path.read_bytes())
            if shell:
                subprocess.run([shell, "-n", str(path)], check=True, capture_output=True)

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI is unavailable")
    def test_real_compose_validation_and_runtime_isolation_without_starting_containers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            envfile = configuration(root)
            env = dict(os.environ, PUBLIC_IP="8.8.8.8", ACME_EMAIL="test@onepost.test", ONEPOST_SECRETS_DIR=str(root / ".production/secrets"))
            result = subprocess.run(["docker", "compose", "--env-file", str(envfile), "-f", str(ROOT / "docker-compose.production.yml"),
                "--profile", "live", "--profile", "ops", "config", "--format", "json"], env=env, capture_output=True, text=True, check=True)
            config = json.loads(result.stdout)
            services = config["services"]
            self.assertEqual([name for name, service in services.items() if service.get("ports")], ["proxy"])
            self.assertEqual({port["target"] for port in services["proxy"]["ports"]}, {80, 443})
            for name in ("bot", "worker", "scheduler", "beat"):
                self.assertEqual(services[name]["profiles"], ["live"])
                self.assertEqual(services[name]["deploy"]["replicas"], 1)
                self.assertEqual(services[name]["environment"]["DJANGO_SETTINGS_MODULE"], "config.settings.production")
                self.assertEqual(services[name]["environment"]["PUBLISHING_USE_BEAT"], "false")
                self.assertEqual(services[name]["depends_on"]["initialize"]["condition"], "service_completed_successfully")
                self.assertTrue(services[name]["read_only"])
            for service in services.values():
                self.assertNotIn("latest", service["image"])
                self.assertFalse(any("docker.sock" in json.dumps(volume) for volume in service.get("volumes", [])))
            for path in (root / ".production/secrets").iterdir():
                self.assertNotIn(path.read_text().strip(), result.stdout)


class ProductionWebTests(SimpleTestCase):
    def test_actual_admin_and_manifest_assets_work_over_forwarded_https_without_database_access(self):
        config = production_settings()
        with tempfile.TemporaryDirectory() as directory, override_settings(
            DEBUG=False, STATIC_ROOT=directory, STORAGES=config.STORAGES,
            ALLOWED_HOSTS=config.ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS=config.CSRF_TRUSTED_ORIGINS,
            SECURE_SSL_REDIRECT=True, SECURE_PROXY_SSL_HEADER=config.SECURE_PROXY_SSL_HEADER,
            SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True,
        ):
            call_command("collectstatic", interactive=False, verbosity=0, stdout=io.StringIO())
            client = Client(HTTP_HOST="8.8.8.8")
            redirect = client.get("/admin/login/")
            self.assertEqual(redirect.status_code, 301)
            self.assertEqual(redirect["Location"], "https://8.8.8.8/admin/login/")
            response = client.get("/admin/login/", HTTP_X_FORWARDED_PROTO="https")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.cookies["csrftoken"]["secure"])
            css_url = staticfiles_storage.url("admin/css/base.css")
            self.assertNotEqual(css_url, "/static/admin/css/base.css")
            css = client.get(css_url, HTTP_X_FORWARDED_PROTO="https")
            self.assertEqual(css.status_code, 200)
            css.close()
