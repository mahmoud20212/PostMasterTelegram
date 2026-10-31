"""Binary-safe database backup/explicit restore; never manipulates Docker volumes."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def compose_prefix(root=ROOT):
    return ["docker", "compose", "--project-name", "onepost-production", "--env-file", str(root / ".env.production"), "-f", str(root / "docker-compose.production.yml")]


def backup(root=ROOT):
    directory = root / ".production/backups"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    name = datetime.now(timezone.utc).strftime("onepost-%Y%m%dT%H%M%S%fZ.pgdump")
    path = directory / name
    partial = path.with_suffix(".pgdump.partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        subprocess.run(compose_prefix(root) + ["exec", "-T", "db", "pg_dump", "-U", "postgres", "-d", "onepost", "-Fc", "--no-owner"],
            stdout=stream, check=True)
    with partial.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise ValueError("Backup validation failed; partial file retained for inspection")
    if path.exists():
        raise FileExistsError(path)
    partial.rename(path)
    return path


def restore(path, confirmation, root=ROOT):
    if confirmation != "onepost":
        raise ValueError("Restore requires --confirm-database onepost")
    path = Path(path).resolve(strict=True)
    with path.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise ValueError("Not a PostgreSQL custom-format backup")
    result = subprocess.run(compose_prefix(root) + ["--profile", "live", "ps", "--status", "running", "--format", "json"],
        capture_output=True, text=True, check=True)
    # Compose versions emit either one array or newline-delimited objects.
    output = result.stdout.strip()
    services = json.loads(output) if output.startswith("[") else [json.loads(line) for line in output.splitlines() if line]
    if any(service["Service"] not in {"db", "redis"} for service in services):
        raise ValueError("Stop application, proxy and certificate services before restoring; keep db/redis running")
    with path.open("rb") as stream:
        subprocess.run(compose_prefix(root) + ["exec", "-T", "db", "pg_restore", "-U", "postgres", "-d", "onepost",
            "--clean", "--if-exists", "--no-owner", "--role=onepost", "--exit-on-error"], stdin=stream, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("backup")
    command = commands.add_parser("restore")
    command.add_argument("file")
    command.add_argument("--confirm-database", required=True)
    args = parser.parse_args()
    try:
        if args.action == "backup":
            print(f"Backup saved: {backup()}")
        else:
            restore(args.file, args.confirm_database)
            print("Restore completed. Restore its matching session key before starting the application.")
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
