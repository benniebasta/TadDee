"""Backup and restore of a Nest data folder.

    python -m app.backup                      # back up now (keeps the newest 14)
    python -m app.backup --data D:\\nest\\data --to "D:\\Site backups\\nest"
    python -m app.backup --restore nest-20261112-233000-000.zip --to C:\\nest\\data-restored

A backup is one zip: a consistent copy of nest.db made with SQLite's backup
API (safe while the app runs) plus every job and relief file. Restore only
writes into an empty (or new) folder; point NEST_DATA_DIR at it to use it.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from datetime import datetime

KEEP = 14
PREFIX = "nest-"
DB_NAME = "nest.db"
STATE = "last_backup.json"
# never in the zip: the live database files (the copy goes in instead), logs, temp files
SKIP_NAMES = {DB_NAME, DB_NAME + "-wal", DB_NAME + "-shm", DB_NAME + "-journal", STATE}
SKIP_DIRS = {"logs", "tmp", "backups"}


def default_backup_dir() -> str:
    env = os.environ.get("NEST_BACKUP_DIR")
    if env:
        return os.path.abspath(env)
    if os.name == "nt":
        return r"D:\Site backups\nest"
    return os.path.abspath("./backups")


def default_data_dir() -> str:
    return os.path.abspath(os.environ.get("NEST_DATA_DIR") or "./data")


def _copy_db(src: str, dst: str) -> None:
    """Consistent copy through the SQLite online backup API."""
    with sqlite3.connect(src, timeout=30) as a, sqlite3.connect(dst) as b:
        a.backup(b)
    b.close()
    a.close()


def make_backup(data_dir: str | None = None, target_dir: str | None = None, keep: int = KEEP) -> dict:
    data_dir = os.path.abspath(data_dir or default_data_dir())
    target_dir = os.path.abspath(target_dir or default_backup_dir())
    db_path = os.path.join(data_dir, DB_NAME)
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"No database at {db_path}")
    os.makedirs(target_dir, exist_ok=True)
    while True:                     # nest-YYYYMMDD-HHMMSS-mmm.zip: fixed length, so names sort by time
        now = datetime.now()
        name = f"{PREFIX}{now:%Y%m%d-%H%M%S}-{now.microsecond // 1000:03d}.zip"
        final = os.path.join(target_dir, name)
        if not os.path.exists(final):
            break
        time.sleep(0.002)
    part = final + ".part"
    target_real = os.path.realpath(target_dir)
    with tempfile.TemporaryDirectory(prefix="nest-backup-") as tmp:
        db_copy = os.path.join(tmp, DB_NAME)
        _copy_db(db_path, db_copy)
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.write(db_copy, DB_NAME)
            for root, dirs, files in os.walk(data_dir):
                rel_root = os.path.relpath(root, data_dir)
                dirs[:] = sorted(d for d in dirs
                                 if not (rel_root == "." and d in SKIP_DIRS)
                                 and os.path.realpath(os.path.join(root, d)) != target_real)
                for f in sorted(files):
                    if rel_root == "." and f in SKIP_NAMES:
                        continue
                    full = os.path.join(root, f)
                    arc = os.path.relpath(full, data_dir).replace(os.sep, "/")
                    try:
                        z.write(full, arc)
                    except (FileNotFoundError, PermissionError):
                        continue                       # deleted or locked meanwhile
    os.replace(part, final)
    removed = prune(target_dir, keep)
    info = {"time": datetime.now().astimezone().replace(microsecond=0).isoformat(), "path": final,
            "size": os.path.getsize(final), "folder": target_dir, "removed": removed}
    with open(os.path.join(data_dir, STATE), "w", encoding="utf-8") as fh:
        json.dump(info, fh)
    return info


def list_backups(target_dir: str) -> list[str]:
    if not os.path.isdir(target_dir):
        return []
    names = [n for n in os.listdir(target_dir) if n.startswith(PREFIX) and n.endswith(".zip")]
    return sorted(names)          # names carry the time, so this is oldest first


def prune(target_dir: str, keep: int = KEEP) -> list[str]:
    names = list_backups(target_dir)
    old = names[:-keep] if keep > 0 else names
    for n in old:
        try:
            os.remove(os.path.join(target_dir, n))
        except OSError:
            pass
    return old


def last_backup(data_dir: str) -> dict | None:
    try:
        with open(os.path.join(data_dir, STATE), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def restore(zip_path: str, to_dir: str) -> str:
    """Unpack a backup into an empty or new folder. Returns the folder."""
    to_dir = os.path.abspath(to_dir)
    if os.path.isdir(to_dir) and os.listdir(to_dir):
        raise FileExistsError(f"{to_dir} is not empty: restore only into an empty folder")
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if DB_NAME not in names:
            raise ValueError(f"{zip_path} is not a Nest backup (no {DB_NAME} inside)")
        root = os.path.realpath(to_dir)
        for n in names:
            dest = os.path.realpath(os.path.join(to_dir, n))
            if not (dest == root or dest.startswith(root + os.sep)) or os.path.isabs(n):
                raise ValueError(f"Unsafe path in the backup: {n}")
        os.makedirs(to_dir, exist_ok=True)
        try:
            z.extractall(to_dir)
        except Exception:
            shutil.rmtree(to_dir, ignore_errors=True)
            raise
    with sqlite3.connect(os.path.join(to_dir, DB_NAME)) as c:   # fails loudly on a broken copy
        ok = c.execute("PRAGMA integrity_check").fetchone()[0]
    c.close()
    if ok != "ok":
        raise ValueError(f"The restored database is damaged: {ok}")
    return to_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.backup", description="Back up or restore Nest data.")
    ap.add_argument("--data", help="data folder (default: NEST_DATA_DIR or ./data)")
    ap.add_argument("--to", help="backup folder (default: NEST_BACKUP_DIR, D:\\Site backups\\nest on "
                                 "Windows), or the empty folder to restore into")
    ap.add_argument("--keep", type=int, default=KEEP, help="backups to keep (default 14)")
    ap.add_argument("--restore", metavar="ZIP", help="restore this backup zip into --to")
    a = ap.parse_args(argv)
    try:
        if a.restore:
            if not a.to:
                ap.error("--restore needs --to <empty folder>")
            out = restore(a.restore, a.to)
            print(f"Restored into {out}. Start Nest with NEST_DATA_DIR={out}")
        else:
            info = make_backup(a.data, a.to, a.keep)
            print(f"Backup written: {info['path']} ({info['size'] / 1e6:.1f} MB)"
                  + (f"; removed {len(info['removed'])} old" if info["removed"] else ""))
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as e:
        print(f"Backup error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
