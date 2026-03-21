import os
import mysql.connector.pooling
from app.config import DB_CONFIG_AUTH, DB_CONFIG_TRACK

_api_secret = os.getenv("API_CONTEXT_SECRET")

_auth_pool = mysql.connector.pooling.MySQLConnectionPool(
    pool_name="olympia_auth_pool",
    pool_size=5,
    **DB_CONFIG_AUTH,
)

_track_pool = mysql.connector.pooling.MySQLConnectionPool(
    pool_name="olympia_track_pool",
    pool_size=5,
    **DB_CONFIG_TRACK,
)


def _get_cursor(pool):
    conn = pool.get_connection()
    cursor = conn.cursor(dictionary=True)
    if _api_secret:
        cursor.execute("SET @api_context = %s", (_api_secret,))
    try:
        yield cursor
        if cursor.with_rows:
            cursor.fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_auth_db():
    """FastAPI dependency — yields a cursor into olympia_auth."""
    yield from _get_cursor(_auth_pool)


def get_track_db():
    """FastAPI dependency — yields a cursor into olympia_track."""
    yield from _get_cursor(_track_pool)
