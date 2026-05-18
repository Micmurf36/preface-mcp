"""
SQLite helpers for Preface. All database access goes through here.
The schema is intentionally simple — one table, no migrations needed.
"""

import sqlite3
import os
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.environ.get("PREFACE_DB_PATH", "/data/preface.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the rules table if it doesn't exist yet."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                text        TEXT    NOT NULL,
                category    TEXT    NOT NULL DEFAULT 'general',
                created_at  TEXT    NOT NULL,
                hit_count   INTEGER NOT NULL DEFAULT 0,
                source      TEXT    NOT NULL DEFAULT 'manual'
            )
        """)
        conn.commit()


def list_rules() -> list[dict]:
    """Return all rules, sorted by category then creation date."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM rules ORDER BY category, created_at"
        ).fetchall()
        return [dict(row) for row in rows]


def get_rule(rule_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM rules WHERE id = ?", (rule_id,)
        ).fetchone()
        return dict(row) if row else None


def insert_rule(text: str, category: str, source: str = "manual") -> int:
    """Insert a new rule and return its id."""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO rules (text, category, created_at, hit_count, source) "
            "VALUES (?, ?, ?, 0, ?)",
            (text, category, now, source),
        )
        conn.commit()
        return cursor.lastrowid


def update_rule(rule_id: int, text: str, category: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE rules SET text = ?, category = ? WHERE id = ?",
            (text, category, rule_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_rule(rule_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        conn.commit()
        return cursor.rowcount > 0


def increment_hit_counts():
    """Called each time get_preface runs — helps identify unused rules over time."""
    with get_connection() as conn:
        conn.execute("UPDATE rules SET hit_count = hit_count + 1")
        conn.commit()


def get_stats() -> dict:
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
        total_chars = conn.execute(
            "SELECT COALESCE(SUM(LENGTH(text)), 0) FROM rules"
        ).fetchone()[0]
        last_updated = conn.execute(
            "SELECT MAX(created_at) FROM rules"
        ).fetchone()[0]
        return {
            "rule_count": count,
            "estimated_tokens": total_chars // 4,
            "last_updated": last_updated or "never",
        }
