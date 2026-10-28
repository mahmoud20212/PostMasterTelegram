import os
import subprocess
import sys
from unittest.mock import patch

import dotenv
import telethon
from django.test import SimpleTestCase
from staging.launch import install_guards


class StagingSafetyTests(SimpleTestCase):
    def test_missing_opt_in_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError):
            install_guards()

    def test_external_broker_is_rejected(self):
        with patch.dict(os.environ, {"ONEPOST_STAGING": "1", "REDIS_URL": "redis://external:6379"}, clear=True), self.assertRaises(RuntimeError):
            install_guards()

    def test_credentials_are_rejected_without_printing_values(self):
        for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_API_ID", "TELEGRAM_SESSION_ENCRYPTION_KEY", "AWS_ACCESS_KEY_ID"):
            with self.subTest(key=key), patch.dict(os.environ, {"ONEPOST_STAGING": "1", "REDIS_URL": "redis://staging-redis:6379/0", key: "synthetic-value"}, clear=True):
                with self.assertRaises(RuntimeError) as caught:
                    install_guards()
                self.assertNotIn("synthetic-value", str(caught.exception))

    def test_client_disabled_and_dotenv_loading_disabled(self):
        with patch.dict(os.environ, {"ONEPOST_STAGING": "1", "REDIS_URL": "redis://staging-redis:6379/0"}, clear=True), \
                patch.object(telethon, "TelegramClient"), patch.object(dotenv, "load_dotenv"):
            install_guards()
            self.assertFalse(dotenv.load_dotenv())
            with self.assertRaisesRegex(RuntimeError, "disabled in local staging"):
                telethon.TelegramClient(None, 0, "")

    def test_engine_import_captures_disabled_client_in_fresh_process(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith(("AWS_", "TELEGRAM_"))}
        env.update(ONEPOST_STAGING="1", REDIS_URL="redis://staging-redis:6379/0", DJANGO_SETTINGS_MODULE="config.test_settings")
        code = """
from staging.launch import install_guards
install_guards()
import django
django.setup()
from publishing.services.engine import TelegramClient
try:
    TelegramClient(None, 0, '')
except RuntimeError as error:
    assert 'disabled in local staging' in str(error)
else:
    raise AssertionError('guard missing')
"""
        result = subprocess.run([sys.executable, "-B", "-c", code], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
