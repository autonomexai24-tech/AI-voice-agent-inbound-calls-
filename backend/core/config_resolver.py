"""Simple runtime config resolution from the single `tenants` table."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Optional

from backend.db import tenants as tenant_repo
from backend.db.connection import is_postgres_enabled
from backend.utils.formatting import mask_phone
from backend.utils.logging import get_logger

logger = get_logger("backend.core.config_resolver")


SAFE_FALLBACK_FIRST_LINE = "We are unable to load this number's configuration right now. Please call again later."

_DEFAULT_CONFIG: dict[str, Any] = {
    "agent_instructions": "",
    "first_line": SAFE_FALLBACK_FIRST_LINE,
    "stt_min_endpointing_delay": 0.2,
    "llm_model": "gpt-4o-mini",
    "llm_provider": "openai",
    "tts_voice": "kavya",
    "tts_language": "hi-IN",
    "tts_provider": "sarvam",
    "stt_provider": "sarvam",
    "stt_language": "unknown",
    "lang_preset": "multilingual",
    "max_turns": 25,
}


@dataclass(frozen=True)
class ResolvedRuntimeConfig:
    config: dict[str, Any]
    source: str
    tenant_id: Optional[str] = None
    did: Optional[str] = None
    fallback_reason: Optional[str] = None


def resolve_runtime_config(
    *,
    caller_phone: Optional[str] = None,
    did: Optional[str] = None,
) -> ResolvedRuntimeConfig:
    """Synchronous wrapper used by scripts/tests."""
    _ = caller_phone
    if not is_postgres_enabled():
        return _fallback_result("postgres_disabled", did=did)
    if not did:
        logger.warning("tenant.resolve.skipped", extra={"fallback_reason": "did_missing"})
        return _fallback_result("did_missing")
    try:
        tenant = tenant_repo.get_tenant_by_did(did)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "tenant.resolve.error",
            extra={"did_masked": mask_phone(did), "error_type": type(exc).__name__},
        )
        return _fallback_result("postgres_error", did=did)
    return _result_from_tenant(tenant, did=did)


async def get_tenant_by_did(did: str) -> Optional[dict]:
    """Resolve a tenant by inbound DID without blocking the event loop."""
    return await asyncio.to_thread(tenant_repo.get_tenant_by_did, did)


async def resolve_runtime_config_async(
    *,
    caller_phone: Optional[str] = None,
    did: Optional[str] = None,
) -> ResolvedRuntimeConfig:
    """Resolve tenant prompt/voice/language for an inbound call."""
    _ = caller_phone
    if not is_postgres_enabled():
        return _fallback_result("postgres_disabled", did=did)
    if not did:
        logger.warning("tenant.resolve.skipped", extra={"fallback_reason": "did_missing"})
        return _fallback_result("did_missing")
    try:
        tenant = await get_tenant_by_did(did)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "tenant.resolve.error",
            extra={"did_masked": mask_phone(did), "error_type": type(exc).__name__},
        )
        return _fallback_result("postgres_error", did=did)
    return _result_from_tenant(tenant, did=did)


def get_config_source_status() -> dict[str, Any]:
    return {
        "postgres_enabled": is_postgres_enabled(),
        "json_source": "disabled",
        "effective_priority": ["tenants_table", "safe_fallback"],
    }


def _result_from_tenant(tenant: Optional[dict], *, did: str) -> ResolvedRuntimeConfig:
    if tenant is None:
        logger.warning(
            "tenant.resolve.not_found",
            extra={"did_masked": mask_phone(did), "fallback_reason": "tenant_not_found"},
        )
        return _fallback_result("tenant_not_found", did=did)

    tenant_id = str(tenant["id"])
    config = dict(_DEFAULT_CONFIG)
    config.update(
        {
            "agent_instructions": tenant.get("system_prompt") or tenant.get("agent_instructions") or "",
            "first_line": tenant.get("welcome_message") or tenant.get("first_line") or SAFE_FALLBACK_FIRST_LINE,
            "tts_voice": tenant.get("voice") or tenant.get("tts_voice") or "kavya",
            "tts_language": tenant.get("tts_language") or "hi-IN",
            "stt_language": tenant.get("stt_language") or "unknown",
            "lang_preset": tenant.get("languages") or tenant.get("lang_preset") or "multilingual",
            "llm_model": os.environ.get("DEFAULT_LLM_MODEL", "gpt-4o-mini"),
            "stt_min_endpointing_delay": float(os.environ.get("DEFAULT_ENDPOINTING_DELAY", "0.5")),
            "business_name": tenant.get("name"),
            "business_phone": tenant.get("phone_number"),
            "_tenant_id": tenant_id,
            "_tenant_name": tenant.get("name"),
            "_tenant_slug": tenant.get("slug"),
            "_config_source": "postgres.tenants",
            "_did": did,
        }
    )
    logger.info(
        "tenant.loaded",
        extra={
            "tenant_id": tenant_id,
            "tenant_name": tenant.get("name"),
            "did_masked": mask_phone(did),
            "languages": tenant.get("languages"),
            "voice": tenant.get("voice"),
        },
    )
    logger.info(
        "prompt.loaded",
        extra={
            "tenant_id": tenant_id,
            "prompt_chars": len(config["agent_instructions"]),
            "welcome_chars": len(config["first_line"]),
        },
    )
    return ResolvedRuntimeConfig(config=config, source="postgres.tenants", tenant_id=tenant_id, did=did)


def _fallback_result(reason: str, *, did: Optional[str] = None) -> ResolvedRuntimeConfig:
    config = dict(_DEFAULT_CONFIG)
    config["_config_source"] = "safe_fallback"
    config["_config_fallback_reason"] = reason
    if did:
        config["_did"] = did
    return ResolvedRuntimeConfig(
        config=config,
        source="safe_fallback",
        did=did,
        fallback_reason=reason,
    )
