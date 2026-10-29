"""Inspect docker-save application layers without extracting or printing contents."""
import argparse
import fnmatch
import json
from pathlib import PurePosixPath
import tarfile


FORBIDDEN = (
    ".env*", ".git*", ".aws", ".codex", ".agents", ".staging", ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".coverage*", "htmlcov",
    "*.log*", "celerybeat-schedule*", "*.pyc", "*.pyo", "*.pyd", "*.sqlite*", "*.db", "*.db-*",
    "*.sql", "*.sql.*", "*.pgdump*", "*.dump*", "*.backup*", "*.bak", "*.session*", "*.pem", "*.key",
    "credentials*", "secrets*", ".credentials*", ".secrets*", "*.zip", "*.tar", "*.tar.*", "*.tgz",
)


def forbidden_application_path(name):
    parts = PurePosixPath(name.removeprefix("./")).parts
    if not parts or parts[0] != "app":
        return False
    if "logs" in parts[1:-1]:
        return True  # An empty runtime log directory is allowed, log contents are not.
    return any(fnmatch.fnmatchcase(part.lower(), pattern) for part in parts[1:] for pattern in FORBIDDEN)


def audit_archive(filename):
    checked = 0
    with tarfile.open(filename, "r:*") as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
        if len(manifest) != 1:
            raise ValueError("Audit exactly one image at a time")
        image = manifest[0]
        config = json.load(archive.extractfile(image["Config"]))["config"]
        for item in config.get("Env") or []:
            key = item.split("=", 1)[0]
            if key.startswith(("AWS_", "TELEGRAM_")) or key in ("POSTGRES_PASSWORD", "DJANGO_SECRET_KEY", "DATABASE_URL", "REDIS_URL"):
                raise ValueError("Credential environment variable baked into image")
        if config.get("User") in (None, "", "root", "0", "0:0"):
            raise ValueError("Image defaults to root")
        # Check ALL layers, including paths removed by later layers.
        for layer in image["Layers"]:
            with tarfile.open(fileobj=archive.extractfile(layer), mode="r|*") as contents:
                for member in contents:
                    if forbidden_application_path(member.name):
                        raise ValueError("Forbidden application artifact found in an image layer")
                    if member.name.removeprefix("./").startswith("app/"):
                        checked += 1
    return len(image["Layers"]), checked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    args = parser.parse_args()
    layers, entries = audit_archive(args.archive)
    print(f"PASS: {layers} layers, {entries} application entries; no forbidden artifacts or baked credential variables; non-root default.")


if __name__ == "__main__":
    main()
