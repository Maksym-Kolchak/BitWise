"""Consistent SQLite backup; does not open or alter the original through Flask."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

parser = argparse.ArgumentParser(
    description="Create a timestamped SQLite backup before migration."
)
parser.add_argument("database", type=Path)
parser.add_argument("backup_folder", type=Path, nargs="?", default=Path("backups"))
args = parser.parse_args()
source = args.database.resolve()
if not source.is_file():
    parser.error(f"Database not found: {source}")
args.backup_folder.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
destination = args.backup_folder / f"{source.stem}-{stamp}.db"
# Read-only connection and SQLite's backup API also handle a live WAL database.
with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
    with sqlite3.connect(destination) as backup:
        original.backup(backup)
print(f"Backup created: {destination.resolve()}")
