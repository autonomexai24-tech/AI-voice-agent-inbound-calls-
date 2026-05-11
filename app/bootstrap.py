"""Production bootstrap for the LiveKit worker process.

FastAPI startup hooks do not run inside `python agent.py start`, so the
worker must initialize PostgreSQL and tenant schema before LiveKit accepts
jobs. All blocking psycopg2 work is moved off the event loop.
"""

from __future__ import annotations

import asyncio

from backend.core.config import load_app_config
from backend.db.connection import init_pool, is_postgres_enabled
from backend.db.tenants import ensure_tenants_schema, seed_default_tenant_from_env
from backend.utils.logging import get_logger

logger = get_logger("app.bootstrap")


async def bootstrap_system(service_name: str = "voice-agent") -> dict:
    """Initialize required production dependencies before starting LiveKit."""
    logger.info("bootstrap.starting", extra={"service": service_name})
    try:
        load_app_config(strict=True)
        if not is_postgres_enabled():
            raise RuntimeError("USE_POSTGRES=true is required for tenant runtime configuration")

        await asyncio.to_thread(init_pool)
        logger.info("postgres.connected", extra={"service": service_name})

        await asyncio.to_thread(ensure_tenants_schema)

        tenant = await asyncio.to_thread(seed_default_tenant_from_env)
        logger.info(
            "bootstrap.completed",
            extra={
                "service": service_name,
                "tenant_id": str((tenant or {}).get("id") or ""),
                "tenant_name": (tenant or {}).get("name"),
            },
        )
        return {"tenant": tenant}
    except Exception as exc:
        logger.exception(
            "bootstrap.failed",
            extra={"service": service_name, "error_type": type(exc).__name__},
        )
        raise
