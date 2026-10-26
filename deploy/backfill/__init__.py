"""Historical batch run backfill (Plan D / Task 8).

Reads pre-Plan-B results directories from disk and inserts RunRow +
decision_records + synthesized events into the liveoption.db SQLite
schema (migration 001). Safe, idempotent, resumable.
"""
