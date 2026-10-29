"""Read-only Compose safety checks; no services or Telegram clients started."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import unittest
from unittest.mock import patch

from production import entrypoint

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("docker"), "Host-side Compose tests require Docker CLI")
class ConfigurationSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, ONEPOST_IMAGE="onepost-fresh:test", DJANGO_ALLOWED_HOSTS="localhost")
        result = subprocess.run(
            ["docker", "--context", "default", "compose", "-f", "docker-compose.production.yml",
             "--profile", "live", "config", "--format", "json"], cwd=ROOT, env=env,
            check=True, capture_output=True, text=True,
        )
        cls.config = json.loads(result.stdout)

    def test_isolation_and_storage(self):
        c = self.config
        self.assertEqual(c["name"], "onepost-fresh")
        self.assertTrue(c["networks"]["data"]["internal"])
        for name in ("fresh-postgres", "fresh-redis"):
            self.assertFalse(c["services"][name].get("ports"))
            self.assertEqual(c["services"][name]["networks"], {"data": None})
        self.assertEqual(set(c["volumes"]), {"postgres_data", "redis_data"})

    def test_app_safety(self):
        for name in ("web", "bot", "celery-worker", "celery-beat"):
            s = self.config["services"][name]
            self.assertNotIn("build", s)
            self.assertEqual(s["user"], "1000:1000")
            self.assertTrue(s["read_only"])
            self.assertFalse(s.get("volumes"))
            self.assertEqual(s["environment"]["POSTGRES_USER"], "onepost_app")
            self.assertEqual(s["environment"]["POSTGRES_HOST"], "fresh-postgres")
            self.assertEqual(s["environment"]["REDIS_URL"], "redis://fresh-redis:6379/0")
            self.assertNotIn("POSTGRES_PASSWORD", s["environment"])
            self.assertIn("healthcheck", s)
            self.assertIn("mem_limit", s)
            if name != "web":
                self.assertEqual(s["profiles"], ["live"])
        self.assertEqual(self.config["services"]["web"]["ports"][0]["host_ip"], "127.0.0.1")

    def test_live_gate_and_wrong_database_fail_closed(self):
        env = dict(DATABASE_BACKEND="postgresql", POSTGRES_HOST="fresh-postgres",
                   REDIS_URL="redis://fresh-redis:6379/0", ONEPOST_POLLING_HANDOFF="pending")
        for command in (["python", "-m", "bot.main"], ["celery", "-A", "config", "worker"]):
            with patch.dict(os.environ, env, clear=True), patch.object(Path, "read_text", return_value="dummy"), patch.object(entrypoint.os, "execvp") as execute, patch("sys.argv", ["entrypoint", *command]):
                with self.assertRaises(SystemExit):
                    entrypoint.main()
                execute.assert_not_called()
        with patch.dict(os.environ, {**env, "POSTGRES_HOST": "staging-postgres"}, clear=True):
            with self.assertRaises(SystemExit):
                entrypoint.main()


if __name__ == "__main__":
    unittest.main()
