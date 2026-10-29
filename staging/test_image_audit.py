import io
import json
from pathlib import Path
import tarfile
import tempfile
from unittest import TestCase

from staging.image_audit import audit_archive, forbidden_application_path


class ImageAuditTests(TestCase):
    def test_nested_sensitive_paths_are_rejected(self):
        for name in ("app/.env", "app/publishing/backup.pgdump", "app/config/private.key",
                     "app/bot/a.session-journal", "app/users/.aws/credentials",
                     "app/old.sqlite3-wal", "app/venv/bin/python", "app/a.sql.gz"):
            with self.subTest(name=name):
                self.assertTrue(forbidden_application_path(name))
        for name in ("app/config/settings.py", "app/staging/sqlite_snapshot.py", "app/logs",
                     "app/publishing/migrations/0007_publishingrun.py", "usr/local/lib/python3.11/venv/__init__.py"):
            self.assertFalse(forbidden_application_path(name))

    def test_sensitive_file_in_earlier_layer_is_detected(self):
        def add(archive, name, data):
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "image.tar"
            layer = io.BytesIO()
            with tarfile.open(fileobj=layer, mode="w") as content:
                add(content, "app/.env", b"synthetic-only")
            with tarfile.open(output, "w") as archive:
                add(archive, "manifest.json", json.dumps([{"Config": "config.json", "Layers": ["layer.tar"]}]).encode())
                add(archive, "config.json", json.dumps({"config": {"User": "appuser", "Env": []}}).encode())
                add(archive, "layer.tar", layer.getvalue())
            with self.assertRaises(ValueError):
                audit_archive(output)
