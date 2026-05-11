"""Single-table tenant storage for the inbound voice MVP.

The live voice worker resolves a tenant by the dialed DID and reads the
runtime prompt directly from `tenants.system_prompt`. There is no
extra configuration table on the call path.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional
from uuid import UUID

from backend.db.connection import get_connection
from backend.utils.logging import get_logger

logger = get_logger("backend.db.tenants")


TENANT_SELECT = """
    id,
    name,
    phone_number,
    system_prompt,
    welcome_message,
    languages,
    voice,
    is_active,
    created_at
"""

SUPPORTED_LANGUAGE_CODES = {"en-IN", "hi-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN"}
LANGUAGE_PRESET_TO_CODE = {
    "english": "en-IN",
    "hindi": "hi-IN",
    "hinglish": "hi-IN",
    "tamil": "ta-IN",
    "telugu": "te-IN",
    "kannada": "kn-IN",
    "malayalam": "ml-IN",
}


def ensure_tenants_schema() -> None:
    """Create or upgrade the single tenants table required by the MVP."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tenants (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name TEXT NOT NULL,
                    phone_number TEXT NOT NULL UNIQUE,
                    system_prompt TEXT NOT NULL DEFAULT '',
                    welcome_message TEXT NOT NULL DEFAULT '',
                    languages TEXT NOT NULL DEFAULT 'multilingual',
                    voice TEXT NOT NULL DEFAULT 'kavya',
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            # Existing EasyPanel databases may have an older tenants table.
            # Add the MVP columns in-place and ignore legacy columns.
            for statement in (
                "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS system_prompt TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS welcome_message TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS languages TEXT NOT NULL DEFAULT 'multilingual'",
                "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS voice TEXT NOT NULL DEFAULT 'kavya'",
                "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
                "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            ):
                cur.execute(statement)
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tenants_phone_digits
                ON tenants ((regexp_replace(phone_number, '[^0-9]', '', 'g')))
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tenants_active_phone_digits
                ON tenants ((regexp_replace(phone_number, '[^0-9]', '', 'g')))
                WHERE is_active = TRUE
                """
            )
    logger.info("tenants.schema.ready")


def seed_default_tenant_from_env() -> Optional[dict]:
    """Seed or update the demo tenant from EasyPanel environment variables."""
    phone_number = (os.environ.get("DEFAULT_PHONE_NUMBER") or "").strip()
    if not phone_number:
        logger.info("tenants.seed.skipped", extra={"reason": "DEFAULT_PHONE_NUMBER_missing"})
        return None

    name = (os.environ.get("DEFAULT_BUSINESS_NAME") or "Demo Business").strip()
    system_prompt = (
        os.environ.get("DEFAULT_SYSTEM_PROMPT")
        or "You are a helpful AI receptionist. Answer inbound calls politely and collect caller details when needed."
    ).strip()
    welcome_message = (
        os.environ.get("DEFAULT_WELCOME_MESSAGE")
        or f"Hello, thank you for calling {name}. How may I help you today?"
    ).strip()
    languages = (os.environ.get("DEFAULT_LANGUAGES") or "multilingual").strip()
    voice = (os.environ.get("DEFAULT_VOICE") or "kavya").strip()
    normalized_phone = _normalize_phone_for_storage(phone_number)
    phone_digits = _phone_digits(normalized_phone)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM tenants
                WHERE regexp_replace(phone_number, '[^0-9]', '', 'g') = %s
                LIMIT 1
                """,
                (phone_digits,),
            )
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    """
                    UPDATE tenants
                    SET name = COALESCE(NULLIF(name, ''), %s),
                        phone_number = %s,
                        system_prompt = COALESCE(NULLIF(system_prompt, ''), %s),
                        welcome_message = COALESCE(NULLIF(welcome_message, ''), %s),
                        languages = COALESCE(NULLIF(languages, ''), %s),
                        voice = COALESCE(NULLIF(voice, ''), %s),
                        is_active = TRUE
                    WHERE id = %s
                    """,
                    (name, normalized_phone, system_prompt, welcome_message, languages, voice, str(existing[0])),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO tenants (name, phone_number, system_prompt, welcome_message, languages, voice, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                    """,
                    (name, normalized_phone, system_prompt, welcome_message, languages, voice),
                )

    tenant = get_tenant_by_did(normalized_phone)
    logger.info(
        "tenants.seed.ready",
        extra={"tenant_id": str((tenant or {}).get("id") or ""), "phone_number_masked": _mask_phone(normalized_phone)},
    )
    return tenant


def get_tenant_by_did(phone_number: str) -> Optional[dict]:
    """Look up an active tenant by dialed DID using normalized digits."""
    normalized = _phone_digits(phone_number)
    if not normalized:
        return None
    storage_digits = _phone_digits(_normalize_phone_for_storage(phone_number)) or normalized

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {TENANT_SELECT}
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
            return _tenant_row(row) if row else None


def get_tenant_by_id(tenant_id: UUID) -> Optional[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {TENANT_SELECT}
                FROM tenants
                WHERE id = %s
                LIMIT 1
                """,
                (str(tenant_id),),
            )
            row = cur.fetchone()
            return _tenant_row(row) if row else None


def get_tenant_by_slug(slug: str) -> Optional[dict]:
    """Compatibility lookup for dashboard workspace slugs.

    The simplified table does not store a slug. It derives one from `name`
    so existing URLs and cookies can keep using workspace slugs.
    """
    clean_slug = _slugify(slug)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {TENANT_SELECT} FROM tenants WHERE is_active = TRUE")
            for row in cur.fetchall():
                tenant = _tenant_row(row)
                if tenant["slug"] == clean_slug:
                    return tenant
    return None


def list_tenants(limit: int = 100) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {TENANT_SELECT}
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
    slug: str | None = None,
    phone_number: str,
    is_active: bool = True,
    system_prompt: str = "",
    welcome_message: str = "",
    languages: str = "multilingual",
    voice: str = "kavya",
) -> UUID:
    normalized_phone = _normalize_phone_for_storage(phone_number)
    _raise_if_did_exists(normalized_phone)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tenants (name, phone_number, system_prompt, welcome_message, languages, voice, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    name.strip(),
                    normalized_phone,
                    system_prompt or "",
                    welcome_message or f"Hello, thank you for calling {name.strip()}. How may I help you today?",
                    languages or "multilingual",
                    voice or "kavya",
                    is_active,
                ),
            )
            (tenant_id,) = cur.fetchone()
            return tenant_id


def update_tenant(tenant_id: UUID, updates: dict[str, Any]) -> bool:
    allowed = {
        "name",
        "phone_number",
        "system_prompt",
        "welcome_message",
        "languages",
        "voice",
        "is_active",
    }
    aliases = {
        "agent_instructions": "system_prompt",
        "first_line": "welcome_message",
        "tts_voice": "voice",
        "tts_language": "languages",
        "lang_preset": "languages",
        "business_name": "name",
        "business_phone": "phone_number",
    }
    clean: dict[str, Any] = {}
    for key, value in updates.items():
        column = aliases.get(key, key)
        if column not in allowed:
            continue
        if column == "phone_number" and value:
            clean[column] = _normalize_phone_for_storage(str(value))
        elif value is not None:
            clean[column] = value
    if not clean:
        return False

    if "phone_number" in clean:
        _raise_if_did_exists(clean["phone_number"], exclude_tenant_id=tenant_id)

    set_clause = ", ".join(f"{column} = %s" for column in clean)
    params = list(clean.values()) + [str(tenant_id)]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tenants SET {set_clause} WHERE id = %s", params)
            return cur.rowcount > 0


def get_runtime_config(tenant_id: UUID) -> Optional[dict]:
    """Return the agent runtime settings derived from one tenants row."""
    tenant = get_tenant_by_id(tenant_id)
    return _runtime_config(tenant) if tenant else None


def update_runtime_config(tenant_id: UUID, updates: dict) -> None:
    """Update prompt, greeting, language, and voice fields on tenants."""
    update_tenant(tenant_id, updates)


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
    config = config or {}
    tenant_id = create_tenant(
        name=name,
        slug=slug,
        phone_number=phone_number,
        is_active=is_active,
        system_prompt=str(config.get("system_prompt") or config.get("agent_instructions") or ""),
        welcome_message=str(
            config.get("welcome_message")
            or config.get("first_line")
            or f"Hello, thank you for calling {name}. How may I help you today?"
        ),
        languages=str(config.get("languages") or config.get("tts_language") or config.get("lang_preset") or "multilingual"),
        voice=str(config.get("voice") or config.get("tts_voice") or "kavya"),
    )
    tenant = get_tenant_by_id(tenant_id)
    return {
        "tenant": tenant,
        "user": {"id": tenant_id, "email": user_email.strip().lower(), "created_at": (tenant or {}).get("created_at")},
        "config_id": tenant_id,
    }


def _tenant_row(row: tuple | None) -> dict:
    tenant = {
        "id": row[0],
        "name": row[1],
        "phone_number": row[2],
        "system_prompt": row[3] or "",
        "welcome_message": row[4] or "",
        "languages": row[5] or "multilingual",
        "voice": row[6] or "kavya",
        "is_active": bool(row[7]),
        "created_at": row[8],
    }
    tenant["slug"] = _slugify(tenant["name"])
    tenant.update(_runtime_config(tenant))
    return tenant


def _runtime_config(tenant: Optional[dict]) -> dict:
    if not tenant:
        return {}
    languages = str(tenant.get("languages") or "multilingual").strip() or "multilingual"
    language_code = _language_code(languages)
    return {
        "tenant_id": tenant.get("id"),
        "agent_instructions": tenant.get("system_prompt") or "",
        "first_line": tenant.get("welcome_message") or "",
        "tts_voice": tenant.get("voice") or "kavya",
        "tts_language": language_code,
        "stt_language": "unknown" if languages.lower() in {"multilingual", "auto", "unknown"} else language_code,
        "lang_preset": languages,
        "llm_model": os.environ.get("DEFAULT_LLM_MODEL", "gpt-4o-mini"),
        "endpointing_delay": float(os.environ.get("DEFAULT_ENDPOINTING_DELAY", "0.5")),
        "business_hours_json": None,
        "transfer_number": os.environ.get("DEFAULT_TRANSFER_NUMBER"),
        "cal_api_key": os.environ.get("CAL_API_KEY"),
        "cal_event_type_id": os.environ.get("CALCOM_EVENT_TYPE_ID") or os.environ.get("CAL_EVENT_TYPE_ID"),
        "system_prompt": tenant.get("system_prompt") or "",
        "welcome_message": tenant.get("welcome_message") or "",
        "languages": languages,
        "voice": tenant.get("voice") or "kavya",
    }


def _language_code(value: str) -> str:
    raw = str(value or "").strip()
    if raw in SUPPORTED_LANGUAGE_CODES:
        return raw
    lowered = raw.lower()
    if lowered in LANGUAGE_PRESET_TO_CODE:
        return LANGUAGE_PRESET_TO_CODE[lowered]
    for part in re.split(r"[, ]+", raw):
        if part in SUPPORTED_LANGUAGE_CODES:
            return part
    return "hi-IN"


def _raise_if_did_exists(phone_number: str, exclude_tenant_id: Optional[UUID] = None) -> None:
    digits = _phone_digits(phone_number)
    params: list[Any] = [digits]
    exclusion = ""
    if exclude_tenant_id:
        exclusion = "AND id <> %s"
        params.append(str(exclude_tenant_id))
    with get_connection() as conn:
        with conn.cursor() as cur:
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
    return slug or "tenant"


def _mask_phone(phone: str) -> str:
    digits = [c for c in str(phone or "") if c.isdigit()]
    if len(digits) <= 2:
        return "XX"
    masked = "X" * (len(digits) - 2) + "".join(digits[-2:])
    return f"+{masked}" if str(phone).startswith("+") else masked
