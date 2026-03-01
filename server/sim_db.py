"""PostgreSQL storage for simulation steps.

Uses the same connection as the tongue project:
  postgresql://predator@localhost:5432/tongue

Table: sim_steps (id, game_id, type, content, verified)
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://predator@localhost:5432/tongue",
)

_conn = None


def get_conn():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(DB_URL)
    try:
        _conn.cursor().execute("SELECT 1")
    except Exception:
        _conn = psycopg2.connect(DB_URL)
    return _conn


def init_db():
    """Create sim_steps table if it doesn't exist."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sim_steps (
                id       SERIAL PRIMARY KEY,
                game_id  TEXT    NOT NULL,
                type     TEXT    NOT NULL,
                content  TEXT    NOT NULL,
                verified BOOLEAN NOT NULL DEFAULT FALSE
            )
        """)
    conn.commit()


def clear_simulations():
    """Delete all simulation rows."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sim_steps")
    conn.commit()


def insert_rows(rows: list[tuple[str, str, str]]):
    """Bulk insert rows as (game_id, type, content) tuples."""
    if not rows:
        return
    conn = get_conn()
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO sim_steps (game_id, type, content) VALUES (%s, %s, %s)",
            rows,
        )
    conn.commit()


def get_all_steps(game_id: str = None) -> list[dict]:
    """Return all steps, optionally filtered by game_id."""
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if game_id:
            cur.execute(
                "SELECT * FROM sim_steps WHERE game_id = %s ORDER BY id",
                (game_id,),
            )
        else:
            cur.execute("SELECT * FROM sim_steps ORDER BY id")
        return [dict(r) for r in cur.fetchall()]


def get_game_ids() -> list[str]:
    """Return distinct game_ids ordered by first occurrence."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT game_id FROM sim_steps ORDER BY game_id"
        )
        return [r[0] for r in cur.fetchall()]


def update_step(step_id: int, content: str):
    """Update the content of a single step."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE sim_steps SET content = %s WHERE id = %s",
            (content, step_id),
        )
    conn.commit()


def clear_unverified_simulations():
    """Delete only non-verified simulation rows."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sim_steps WHERE verified = FALSE")
    conn.commit()


def get_verified_steps_by_game() -> dict:
    """Return {game_id: [step_dicts]} for all games that have verified steps."""
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT * FROM sim_steps
            WHERE game_id IN (SELECT DISTINCT game_id FROM sim_steps WHERE verified = TRUE)
              AND verified = TRUE
            ORDER BY id
        """)
        rows = [dict(r) for r in cur.fetchall()]
    result = {}
    for row in rows:
        result.setdefault(row['game_id'], []).append(row)
    return result


def delete_step(step_id: int):
    """Delete a single step row."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sim_steps WHERE id = %s", (step_id,))
    conn.commit()


def set_verified(step_id: int, verified: bool):
    """Toggle the verified flag on a single step."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE sim_steps SET verified = %s WHERE id = %s",
            (verified, step_id),
        )
    conn.commit()
