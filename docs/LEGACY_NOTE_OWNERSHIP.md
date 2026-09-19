# Legacy Note Ownership & Migration Specification

**Version**: 1.2.0  
**Owner**: Developer 2  
**Date**: 2026-09-19  

---

## 1. Problem Statement

Historical notes created prior to multi-session runtime isolation were persisted with `session_id = NULL`. In the previous implementation, the cross-session authorization check:
```python
if existing.get("session_id") and session_id and existing["session_id"] != session_id:
    # Reject cross-session mutation
```
evaluated to falsy whenever `session_id` was `None`. As a result, legacy notes remained globally mutable by any session across the system without isolation or authorization.

---

## 2. Ownership Architecture & State Transition

To eliminate this cross-session vulnerability while preserving user read access, legacy notes transition to an explicit `owner_type = 'legacy_unclaimed'` state.

```text
[Legacy State]
  session_id = NULL
  owner_type = NULL / 'session'
        │
        ▼  scripts/migrate_legacy_note_ownership.py
[Migrated State]
  session_id = NULL
  owner_type = 'legacy_unclaimed'
  owner_id = 'migration-2026-09-19'
        │
        ├── Read operations (get, search, list) ───────► ALLOWED
        │
        ├── Update operations (update_note) ──────────► REJECTED (NOTE_UNCLAIMED)
        │
        ▼  claim_note(note_id, session_id, owner_id)
[Claimed State]
  session_id = <current_session>
  owner_type = 'session'
  owner_id = <session_id>
  claimed_at = <ISO-8601 timestamp>
        │
        ├── Same-session update ──────────────────────► ALLOWED
        └── Foreign-session update ───────────────────► REJECTED (NOTE_SESSION_MISMATCH)
```

---

## 3. Schema Definitions

The `notes` table in `app/database.py` adds three ownership metadata columns:

| Column | Type | Default | Description |
|---|---|---|---|
| `session_id` | `TEXT` | `NULL` | Bound chat session ID. `NULL` for unclaimed legacy notes. |
| `owner_type` | `TEXT` | `'session'` | Entity type: `'session'`, `'legacy_unclaimed'`, or `'user'`. |
| `owner_id` | `TEXT` | `NULL` | Owner identifier or migration namespace string. |
| `claimed_at` | `TEXT` | `NULL` | ISO-8601 timestamp when note was claimed. |

---

## 4. Operational Migration Procedure

### Executing the Migration
Run the idempotent migration command:
```bash
python3 scripts/migrate_legacy_note_ownership.py
```

### Dry-Run Verification
To inspect candidates without writing to the database:
```bash
python3 scripts/migrate_legacy_note_ownership.py --dry-run
```

### Rollback Procedure
If required, revert records tagged with the migration namespace:
```bash
python3 scripts/migrate_legacy_note_ownership.py --rollback
```

---

## 5. Invariants & Data Integrity Guarantee
1. **Zero Data Loss**: Note title, rendered HTML, tags, backlinks (`links`), and semantic blocks (`canonical_blocks`) are never mutated during migration.
2. **Idempotence**: Running the migration script multiple times produces identical state.
3. **No Foreign Overwrites**: Once claimed, a note cannot be modified by any session other than the owner session.
