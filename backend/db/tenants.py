"""Tenant lookup and configuration access (raw SQL).

Per ARCHITECTURE.md §4:
- tenants table: id, name, slug, phone_number (Vobiz DID), created_at
- tenant_config table: per-tenant AI personality configuration

Per RULES.md: every query touching tenant-scoped data filters by tenant_id.
Here, tenant_id is the resolved identity; queries lookup BY it or BY DID.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from backend.db.connection import get_connection
from backend.utils.logging import get_logger

logger = get_logger("backend.db.tenants")


def get_tenant_by_did(phone_number: str) -> Optional[dict]:
    """Look up a tenant by the dialed DID (Vobiz phone number).

    Returns None if no tenant is configured for this DID.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, slug, phone_number, created_at
                FROM tenants
                WHERE phone_number = %s
                """,
                (phone_number,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "id": row[0],
                "name": row[1],
                "slug": row[2],
                "phone_number": row[3],
                "created_at": row[4],
            }


def get_tenant_by_id(tenant_id: UUID) -> Optional[dict]:
    """Look up a tenant by primary key."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, slug, phone_number, created_at
                FROM tenants
                WHERE id = %s
                """,
                (str(tenant_id),),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "id": row[0],
                "name": row[1],
                "slug": row[2],
                "phone_number": row[3],
                "created_at": row[4],
            }


def get_tenant_config(tenant_id: UUID) -> Optional[dict]:
    """Load the per-tenant AI configuration row.

    Returns None if the tenant has no config row yet. Callers should treat
    that as a misconfiguration (every active tenant should have a config).
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    tenant_id,
                    agent_instructions,
                    first_line,
                    tts_voice,
                    tts_language,
                    lang_preset,
                    llm_model,
                    endpointing_delay,
                    business_hours_json,
                    transfer_number,
                    cal_api_key,
                    cal_event_type_id,
                    updated_at
                FROM tenant_config
                WHERE tenant_id = %s
                """,
                (str(tenant_id),),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "id": row[0],
                "tenant_id": row[1],
                "agent_instructions": row[2],
                "first_line": row[3],
                "tts_voice": row[4],
                "tts_language": row[5],
                "lang_preset": row[6],
                "llm_model": row[7],
                "endpointing_delay": float(row[8]) if row[8] is not None else None,
                "business_hours_json": row[9],
                "transfer_number": row[10],
                "cal_api_key": row[11],
                "cal_event_type_id": row[12],
                "updated_at": row[13],
            }


def update_tenant_config(tenant_id: UUID, updates: dict) -> None:
    """Apply a partial update to a tenant's config row.

    Only whitelisted columns are updatable. Unknown keys are ignored.
    Updates `updated_at` to NOW() implicitly via the SET clause.
    """
    allowed = {
        "agent_instructions",
        "first_line",
        "tts_voice",
        "tts_language",
        "lang_preset",
        "llm_model",
        "endpointing_delay",
        "business_hours_json",
        "transfer_number",
        "cal_api_key",
        "cal_event_type_id",
    }
    clean = {k: v for k, v in updates.items() if k in allowed}
    if not clean:
        return

    set_clause = ", ".join(f"{col} = %s" for col in clean)
    params = list(clean.values()) + [str(tenant_id)]

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE tenant_config
                SET {set_clause}, updated_at = NOW()
                WHERE tenant_id = %s
                """,
                params,
            )
