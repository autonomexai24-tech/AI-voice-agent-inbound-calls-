"""Tenant lookup and configuration access (raw SQL).

Per ARCHITECTURE.md §4:
- tenants table: id, name, slug, phone_number (Vobiz DID), created_at
- tenant_config table: per-tenant AI personality configuration

Per RULES.md: every query touching tenant-scoped data filters by tenant_id.
Here, tenant_id is the resolved identity; queries lookup BY it or BY DID.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from uuid import UUID

from psycopg2.extras import Json

from backend.db.connection import get_connection
from backend.utils.passwords import hash_password
from backend.utils.logging import get_logger

logger = get_logger("backend.db.tenants")


_TENANT_COLUMNS = "id, name, slug, phone_number, is_active, created_at"
_CONFIG_COLUMNS = (
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
)


def get_tenant_by_did(phone_number: str) -> Optional[dict]:
    """Look up a tenant by the dialed DID (Vobiz phone number).

    Returns None if no tenant is configured for this DID.
    """
    normalized = _phone_digits(phone_number)
    if not normalized:
        return None
    try:
        storage_digits = _phone_digits(_normalize_phone_for_storage(phone_number))
    except ValueError:
        storage_digits = normalized

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, slug, phone_number, is_active, created_at
                FROM tenants
                WHERE is_active = TRUE
                  AND (
                    phone_number = %s
                    OR regexp_replace(phone_number, '[^0-9]', '', 'g') = %s
                    OR regexp_replace(phone_number, '[^0-9]', '', 'g') = %s
                  )
                LIMIT 1
                """,
                (phone_number, normalized, storage_digits),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _tenant_row(row)


def get_tenant_by_id(tenant_id: UUID) -> Optional[dict]:
    """Look up a tenant by primary key."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, slug, phone_number, is_active, created_at
                FROM tenants
                WHERE id = %s
                """,
                (str(tenant_id),),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _tenant_row(row)


def list_tenants(limit: int = 100) -> list[dict]:
    """Return tenants for operator provisioning screens/tools."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_TENANT_COLUMNS}
                FROM tenants
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [_tenant_row(row) for row in cur.fetchall()]


def create_tenant(
    *,
    name: str,
    slug: str,
    phone_number: str,
    is_active: bool = True,
) -> UUID:
    """Create a tenant row and return its id."""
    normalized_phone = _normalize_phone_for_storage(phone_number)
    with get_connection() as conn:
        with conn.cursor() as cur:
            _raise_if_did_exists(cur, normalized_phone)
            cur.execute(
                """
                INSERT INTO tenants (name, slug, phone_number, is_active)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (name.strip(), _slugify(slug), normalized_phone, is_active),
            )
            (tenant_id,) = cur.fetchone()
            return tenant_id


def update_tenant(tenant_id: UUID, updates: dict[str, Any]) -> bool:
    """Apply whitelisted tenant updates. Returns True when a row changed."""
    allowed = {"name", "slug", "phone_number", "is_active"}
    clean: dict[str, Any] = {}
    for key, value in updates.items():
        if key not in allowed:
            continue
        if key == "slug" and value:
            clean[key] = _slugify(str(value))
        elif key == "phone_number" and value:
            clean[key] = _normalize_phone_for_storage(str(value))
        else:
            clean[key] = value
    if not clean:
        return False

    set_clause = ", ".join(f"{column} = %s" for column in clean)
    params = list(clean.values()) + [str(tenant_id)]
    with get_connection() as conn:
        with conn.cursor() as cur:
            if "phone_number" in clean:
                _raise_if_did_exists(cur, clean["phone_number"], exclude_tenant_id=tenant_id)
            cur.execute(
                f"""
                UPDATE tenants
                SET {set_clause}
                WHERE id = %s
                """,
                params,
            )
            return cur.rowcount > 0


def create_user(
    *,
    tenant_id: UUID,
    email: str,
    password: str,
) -> UUID:
    """Create a dashboard user for a tenant."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (tenant_id, email, password_hash)
                VALUES (%s, lower(%s), %s)
                RETURNING id
                """,
                (str(tenant_id), email.strip(), hash_password(password)),
            )
            (user_id,) = cur.fetchone()
            return user_id


def ensure_tenant_config(tenant_id: UUID, config: Optional[dict[str, Any]] = None) -> UUID:
    """Create or update the one tenant_config row for a tenant."""
    clean = _clean_config(config or {})
    columns = list(_CONFIG_COLUMNS)
    values = [_config_value(clean, column) for column in columns]
    placeholders = ", ".join(["%s"] * (len(columns) + 1))
    update_clause = ", ".join(f"{column} = EXCLUDED.{column}" for column in columns)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO tenant_config (tenant_id, {", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT (tenant_id) DO UPDATE
                SET {update_clause}, updated_at = NOW()
                RETURNING id
                """,
                [str(tenant_id), *values],
            )
            (config_id,) = cur.fetchone()
            return config_id


def provision_tenant(
    *,
    name: str,
    phone_number: str,
    user_email: str,
    user_password: str,
    slug: Optional[str] = None,
    config: Optional[dict[str, Any]] = None,
    is_active: bool = True,
) -> dict:
    """Provision tenant, first user, and tenant_config in one transaction."""
    clean_name = name.strip()
    clean_slug = _slugify(slug or clean_name)
    clean_phone = _normalize_phone_for_storage(phone_number)
    clean_config = _clean_config(config or {})

    with get_connection() as conn:
        with conn.cursor() as cur:
            _raise_if_did_exists(cur, clean_phone)
            cur.execute(
                """
                INSERT INTO tenants (name, slug, phone_number, is_active)
                VALUES (%s, %s, %s, %s)
                RETURNING id, name, slug, phone_number, is_active, created_at
                """,
                (clean_name, clean_slug, clean_phone, is_active),
            )
            tenant = _tenant_row(cur.fetchone())

            cur.execute(
                """
                INSERT INTO users (tenant_id, email, password_hash)
                VALUES (%s, lower(%s), %s)
                RETURNING id, email, created_at
                """,
                (str(tenant["id"]), user_email.strip(), hash_password(user_password)),
            )
            user_row = cur.fetchone()

            columns = list(_CONFIG_COLUMNS)
            values = [_config_value(clean_config, column) for column in columns]
            placeholders = ", ".join(["%s"] * (len(columns) + 1))
            cur.execute(
                f"""
                INSERT INTO tenant_config (tenant_id, {", ".join(columns)})
                VALUES ({placeholders})
                RETURNING id
                """,
                [str(tenant["id"]), *values],
            )
            (config_id,) = cur.fetchone()

            return {
                "tenant": tenant,
                "user": {
                    "id": user_row[0],
                    "email": user_row[1],
                    "created_at": user_row[2],
                },
                "config_id": config_id,
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
    if "business_hours_json" in clean and clean["business_hours_json"] is not None:
        clean["business_hours_json"] = Json(clean["business_hours_json"])

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


def _tenant_row(row: tuple) -> dict:
    return {
        "id": row[0],
        "name": row[1],
        "slug": row[2],
        "phone_number": row[3],
        "is_active": bool(row[4]),
        "created_at": row[5],
    }


def _phone_digits(value: str) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _normalize_phone_for_storage(value: str) -> str:
    raw = str(value or "").strip()
    digits = _phone_digits(raw)
    if not digits:
        raise ValueError("phone_number_required")
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    if raw.startswith("+"):
        return f"+{digits}"
    return digits


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    if not slug:
        raise ValueError("tenant_slug_required")
    return slug


def _clean_config(config: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in config.items() if k in _CONFIG_COLUMNS}
    if "business_hours_json" in clean and clean["business_hours_json"] is not None:
        clean["business_hours_json"] = Json(clean["business_hours_json"])
    return clean


def _config_value(config: dict[str, Any], column: str) -> Any:
    defaults = {
        "agent_instructions": "",
        "first_line": "",
        "tts_voice": "kavya",
        "tts_language": "hi-IN",
        "lang_preset": "multilingual",
        "llm_model": "gpt-4o-mini",
        "endpointing_delay": 0.5,
        "business_hours_json": None,
        "transfer_number": None,
        "cal_api_key": None,
        "cal_event_type_id": None,
    }
    return config.get(column, defaults[column])


def _raise_if_did_exists(cur, phone_number: str, exclude_tenant_id: Optional[UUID] = None) -> None:
    digits = _phone_digits(phone_number)
    params: list[Any] = [digits]
    exclusion = ""
    if exclude_tenant_id:
        exclusion = "AND id <> %s"
        params.append(str(exclude_tenant_id))
    cur.execute(
        f"""
        SELECT id
        FROM tenants
        WHERE regexp_replace(phone_number, '[^0-9]', '', 'g') = %s
          {exclusion}
        LIMIT 1
        """,
        params,
    )
    if cur.fetchone():
        raise ValueError("tenant_did_already_exists")
