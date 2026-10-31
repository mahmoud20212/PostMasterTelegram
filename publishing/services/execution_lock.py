"""Process-lifetime exclusion, independent of scheduling lease expiry.

PostgreSQL uses a dedicated, unpooled session advisory lock, not a transaction
held across Telegram I/O. SQLite development uses an OS lock on the local host.
Never unlink lock files: replacing an inode would break exclusion.
"""
import hashlib
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from django.db import connections


@contextmanager
def execution_lock(kind, identity):
    connection = connections["default"]
    name = f"onepost:{kind}:{identity}"
    if connection.vendor == "postgresql":
        config = deepcopy(connection.settings_dict)
        config.get("OPTIONS", {}).pop("pool", None)
        config["OPTIONS"].setdefault("connect_timeout", 3)
        guard = connection.__class__(config, alias="publishing_execution_guard")
        key = int.from_bytes(hashlib.blake2b(name.encode(), digest_size=8).digest(), "big", signed=True)
        try:
            with guard.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
                acquired = cursor.fetchone()[0]
            yield acquired
        finally:
            # Closing this dedicated session releases the lock after a crash too.
            guard.close()
        return
    if connection.vendor != "sqlite":
        raise RuntimeError("Publishing supports PostgreSQL and local SQLite only")
    database = str(connection.settings_dict["NAME"])
    digest = hashlib.sha256(f"{database}:{name}".encode()).hexdigest()
    directory = Path(tempfile.gettempdir()) / "onepost-execution-locks"
    directory.mkdir(exist_ok=True)
    with (directory / digest).open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (BlockingIOError, OSError):
            pass
        try:
            yield acquired
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)
