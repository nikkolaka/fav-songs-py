#!/usr/bin/env python3
"""Backup, check and restore the listen database around a deployment.

Deliberately stdlib-only and run from the host that owns the bind mount: the deploy
needs this to work when the container is broken, which rules out running it inside the
container.

Backups use SQLite's online backup API rather than copying the file. A `cp` of a live
WAL database can capture a torn page or miss committed transactions still in the WAL;
the backup API takes a consistent snapshot without blocking the app or requiring
downtime, and produces a standalone file with no sidecars.

    dbtool.py backup  --db PATH --into DIR [--keep N]   # prints the backup path
    dbtool.py check   --db PATH                         # prints a JSON summary
    dbtool.py restore --backup PATH --db PATH
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# What must survive an update no matter what. Listens can be re-accumulated and the
# discovery archive can be re-swept, but a lost token means every user re-authorises,
# and a lost user row means their history is orphaned.
VITAL_TABLES = ("users", "tokens")

COUNTED_TABLES = VITAL_TABLES + ("listens", "play_counts", "discovery_archive", "sessions")


def log(message: str) -> None:
    print(message, file=sys.stderr)


def scalar(conn: sqlite3.Connection, sql: str):
    """Run a query that may reference a table this schema version doesn't have."""
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def summarise(path: str) -> dict:
    """Row counts and an integrity verdict. Raises if the file is unreadable.

    Has to cope with *any* schema version: the backup taken on the very first deploy is
    of a database this build has never migrated, so nothing here may assume a table
    exists -- including `meta`, which older versions don't have.
    """
    conn = None
    try:
        # Read-only, so a check can never be the thing that breaks the file.
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {t: scalar(conn, f"SELECT COUNT(*) FROM {t}") for t in COUNTED_TABLES}
        version = scalar(conn, "SELECT value FROM meta WHERE key = 'schema_version'")
    except sqlite3.Error as exc:
        raise SystemExit(f"cannot read {path}: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()

    return {
        "integrity": integrity,
        "schema_version": int(version) if version is not None else None,
        "counts": counts,
    }


def cmd_backup(args: argparse.Namespace) -> None:
    source = Path(args.db)
    if not source.exists():
        # First ever deploy: nothing to protect yet.
        log(f"no database at {source}; nothing to back up")
        print("")
        return

    into = Path(args.into)
    into.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = into / f"favsongs-{stamp}.db"

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
        # The copy inherits WAL mode from the source, which leaves -wal/-shm beside it.
        # A backup has to be one self-contained file: sidecars get separated from it,
        # and a stale one left next to a backup makes the backup itself unreadable.
        dst.execute("PRAGMA journal_mode = DELETE")
    finally:
        dst.close()
        src.close()

    for sidecar in (f"{target}-wal", f"{target}-shm"):
        Path(sidecar).unlink(missing_ok=True)

    summary = summarise(str(target))
    if summary["integrity"] != "ok":
        target.unlink(missing_ok=True)
        raise SystemExit(f"backup failed its integrity check: {summary['integrity']}")

    (into / f"favsongs-{stamp}.json").write_text(json.dumps(summary, indent=2))
    log(f"backed up to {target} ({summary['counts']})")

    # Rotate, keeping the newest N pairs.
    backups = sorted(into.glob("favsongs-*.db"), reverse=True)
    for stale in backups[args.keep :]:
        stale.unlink(missing_ok=True)
        stale.with_suffix(".json").unlink(missing_ok=True)
        log(f"pruned {stale.name}")

    print(target)


def cmd_check(args: argparse.Namespace) -> None:
    summary = summarise(args.db)
    print(json.dumps(summary))
    if summary["integrity"] != "ok":
        raise SystemExit(f"integrity check failed: {summary['integrity']}")


def cmd_restore(args: argparse.Namespace) -> None:
    backup, db = Path(args.backup), Path(args.db)
    if not backup.exists():
        raise SystemExit(f"no backup at {backup}")

    summary = summarise(str(backup))
    if summary["integrity"] != "ok":
        raise SystemExit(f"refusing to restore a corrupt backup: {summary['integrity']}")

    # The sidecars belong to the database being replaced. Leaving them would let SQLite
    # replay a newer WAL over an older file, which is worse than either alone.
    for sidecar in (f"{db}-wal", f"{db}-shm"):
        Path(sidecar).unlink(missing_ok=True)

    shutil.copy2(backup, db)
    os.sync()
    log(f"restored {db} from {backup.name} ({summary['counts']})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup")
    backup.add_argument("--db", required=True)
    backup.add_argument("--into", required=True)
    backup.add_argument("--keep", type=int, default=10)
    backup.set_defaults(func=cmd_backup)

    check = sub.add_parser("check")
    check.add_argument("--db", required=True)
    check.set_defaults(func=cmd_check)

    restore = sub.add_parser("restore")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--db", required=True)
    restore.set_defaults(func=cmd_restore)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
