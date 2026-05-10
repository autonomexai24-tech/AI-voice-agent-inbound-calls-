"""PostgreSQL data access layer.

All functions in this package:
- Use raw SQL via psycopg2
- Require tenant_id on every query that touches tenant-scoped tables
- Use a shared psycopg2.pool.ThreadedConnectionPool (see connection.py)
- Are activated only when USE_POSTGRES=true at process start
"""

from backend.db.connection import (
    init_pool,
    close_pool,
    get_connection,
    is_postgres_enabled,
)

__all__ = [
    "init_pool",
    "close_pool",
    "get_connection",
    "is_postgres_enabled",
]
