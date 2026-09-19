#!/usr/bin/env python3
"""
Migration script: Legacy Note Ownership Migration.
Safely migrates legacy notes with session_id = NULL to owner_type = 'legacy_unclaimed',
binding them to an explicit migration owner namespace.

Supports idempotent execution, --dry-run, and --rollback.
Preserves all rendered HTML, tags, backlinks, and canonical blocks.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from app import database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_legacy_notes")


def run_migration(owner_id: str = "migration-2026-09-19", dry_run: bool = False) -> int:
    """Migrates legacy NULL session_id notes to legacy_unclaimed status."""
    database.init_db()
    unclaimed = database.list_legacy_unclaimed_notes()
    logger.info(f"Found {len(unclaimed)} legacy note candidates for migration.")

    if dry_run:
        logger.info("[DRY-RUN] Would update the following legacy notes to owner_type='legacy_unclaimed':")
        for n in unclaimed:
            logger.info(f"  - id={n['id']} title='{n['title']}' current_session={n.get('session_id')} current_owner={n.get('owner_id')}")
        return len(unclaimed)

    count = database.migrate_legacy_notes_batch(owner_id=owner_id)
    logger.info(f"Successfully migrated {count} legacy notes to owner_type='legacy_unclaimed' owner_id='{owner_id}'.")
    return count


def run_rollback(owner_id: str = "migration-2026-09-19", dry_run: bool = False) -> int:
    """Rolls back notes tagged with owner_id back to default session ownership."""
    database.init_db()
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM notes WHERE owner_id = ? AND session_id IS NULL", (owner_id,))
    rows = cursor.fetchall()
    conn.close()
    logger.info(f"Found {len(rows)} notes tagged with owner_id='{owner_id}' for rollback.")

    if dry_run:
        logger.info("[DRY-RUN] Would roll back the following notes:")
        for r in rows:
            logger.info(f"  - id={r['id']} title='{r['title']}'")
        return len(rows)

    count = database.rollback_legacy_notes_migration(owner_id=owner_id)
    logger.info(f"Successfully rolled back {count} notes.")
    return count


def main():
    parser = argparse.ArgumentParser(description="Migrate legacy NULL session_id notes to legacy_unclaimed.")
    parser.add_argument("--owner-id", default="migration-2026-09-19", help="Owner identity for legacy records")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing to database")
    parser.add_argument("--rollback", action="store_true", help="Revert migration for specified owner-id")
    args = parser.parse_args()

    if args.rollback:
        run_rollback(owner_id=args.owner_id, dry_run=args.dry_run)
    else:
        run_migration(owner_id=args.owner_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
