"""Copy SQLite using the backup API, never edit the original or overwrite output."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3


def backup(source, destination):
    source, destination = Path(source).resolve(strict=True), Path(destination).resolve()
    if source == destination:
        raise ValueError("Source and destination must differ")
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
            with closing(sqlite3.connect(destination)) as writer:
                reader.backup(writer)
                if writer.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("Snapshot integrity check failed")
    except Exception:
        # Leave the failed output for operator inspection; never overwrite it.
        raise RuntimeError("Snapshot failed; do not use its output") from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    backup(args.source, args.destination)
    print("Consistent snapshot created. Treat it as sensitive; no records printed.")


if __name__ == "__main__":
    main()
