"""PostgreSQL data access layer (Phase 1).

All functions in this package:
- Use raw SQL via psycopg2 (no ORM, no Supabase SDK)
- Require tenant_id on every query that touches tenant-scoped tables
- Use a shared psycopg2.pool.ThreadedConnectionPool (see connection.py)
- Are activated only when USE_POSTGRES=true at process start

The existing Supabase-backed db.py at the project root remains the active
production path until the adapter migration (Phase 2) rewires callers.
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
