"""Deterministic runtime config resolution for Phase 3B.

Priority:
1. PostgreSQL tenant_config, when USE_POSTGRES=true and a tenant is resolved.
2. Existing JSON config files, preserving the legacy lookup order.
3. Built-in environment-safe defaults that match the current voice runtime.

This module is intentionally small and side-effect free. It does not mutate
os.environ, does not cache, and never raises from PostgreSQL failures; live
calls must fall back to the legacy config path if Postgres is unavailable.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

from backend.db.connection import is_postgres_enabled
from backend.services.tenant_service import TenantNotConfiguredError, TenantService
from backend.utils.formatting import mask_phone
from backend.utils.logging import get_logger

logger = get_logger("backend.core.config_resolver")

CONFIG_FILE = "config.json"

_DEFAULT_CONFIG: dict[str, Any] = {
    "agent_instructions": "",
    "stt_min_endpointing_delay": 0.05,
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

_TENANT_CONFIG_KEY_MAP = {
    "agent_instructions": "agent_instructions",
    "first_line": "first_line",
    "tts_voice": "tts_voice",
    "tts_language": "tts_language",
    "lang_preset": "lang_preset",
    "llm_model": "llm_model",
    "endpointing_delay": "stt_min_endpointing_delay",
    "transfer_number": "transfer_number",
    "cal_api_key": "cal_api_key",
    "cal_event_type_id": "cal_event_type_id",
    "business_hours_json": "business_hours_json",
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
    """Resolve call-start config without changing the existing call flow."""
    json_config, json_source = _load_json_config(caller_phone)

    if is_postgres_enabled():
        if did:
            resolved = _try_postgres_config(did=did, json_config=json_config)
            if resolved is not None:
                return resolved
            return _json_result(
                json_config,
                json_source,
                fallback_reason="postgres_unavailable_or_unconfigured",
                did=did,
            )

        logger.info(
            "config.source.selected",
            extra={
                "source": json_source,
                "postgres_enabled": True,
                "fallback_reason": "did_missing",
            },
        )
        return _json_result(
            json_config,
            json_source,
            fallback_reason="did_missing",
        )

    logger.info(
        "config.source.selected",
        extra={"source": json_source, "postgres_enabled": False},
    )
    return _json_result(json_config, json_source)


def get_config_source_status() -> dict[str, Any]:
    """Return lightweight config-source status for `/health`.

    This does not query PostgreSQL. Tenant config is resolved only at call
    start when a DID exists.
    """
    _, json_source = _load_json_config(None)
    postgres_enabled = is_postgres_enabled()
    return {
        "postgres_enabled": postgres_enabled,
        "json_source": json_source,
        "effective_priority": (
            ["postgres_tenant_config", "json_config", "environment_defaults"]
            if postgres_enabled
            else ["json_config", "environment_defaults"]
        ),
    }


def _try_postgres_config(
    *,
    did: str,
    json_config: dict[str, Any],
) -> Optional[ResolvedRuntimeConfig]:
    try:
        resolved = TenantService().resolve_from_did(did)
    except TenantNotConfiguredError:
        logger.warning(
            "tenant.resolve.fallback",
            extra={
                "did_masked": mask_phone(did),
                "fallback_reason": "tenant_not_configured",
            },
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "tenant.resolve.error",
            extra={
                "did_masked": mask_phone(did),
                "fallback_reason": "postgres_error",
                "error_type": type(exc).__name__,
            },
        )
        return None

    tenant = resolved["tenant"]
    tenant_config = resolved["config"]
    tenant_id = str(tenant["id"])
    merged = _merge_config(json_config, _normalize_tenant_config(tenant_config))
    merged["_tenant_id"] = tenant_id
    merged["_config_source"] = "postgres"
    merged["_did"] = did

    logger.info(
        "config.source.selected",
        extra={
            "source": "postgres",
            "postgres_enabled": True,
            "tenant_id": tenant_id,
            "did_masked": mask_phone(did),
        },
    )
    return ResolvedRuntimeConfig(
        config=merged,
        source="postgres",
        tenant_id=tenant_id,
        did=did,
    )


def _load_json_config(phone_number: Optional[str]) -> tuple[dict[str, Any], str]:
    config: dict[str, Any] = {}
    source = "environment_defaults"

    for path in _legacy_config_paths(phone_number):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                config = json.load(f)
            source = path
            break
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "config.json.read_failed",
                extra={"path": path, "error_type": type(exc).__name__},
            )

    return _merge_config(config), source


def _legacy_config_paths(phone_number: Optional[str]) -> list[str]:
    paths: list[str] = []
    if phone_number and phone_number != "unknown":
        clean = phone_number.replace("+", "").replace(" ", "")
        paths.append(f"configs/{clean}.json")
    paths.extend(["configs/default.json", CONFIG_FILE])
    return paths


def _merge_config(
    json_config: dict[str, Any],
    postgres_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    merged = dict(_DEFAULT_CONFIG)
    merged.update(json_config)
    if postgres_config:
        merged.update(postgres_config)
    return merged


def _normalize_tenant_config(tenant_config: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for db_key, runtime_key in _TENANT_CONFIG_KEY_MAP.items():
        value = tenant_config.get(db_key)
        if value is not None and value != "":
            normalized[runtime_key] = value
    return normalized


def _json_result(
    config: dict[str, Any],
    source: str,
    *,
    fallback_reason: Optional[str] = None,
    did: Optional[str] = None,
) -> ResolvedRuntimeConfig:
    result = dict(config)
    result["_config_source"] = source
    if fallback_reason:
        result["_config_fallback_reason"] = fallback_reason
    if did:
        result["_did"] = did
    return ResolvedRuntimeConfig(
        config=result,
        source=source,
        did=did,
        fallback_reason=fallback_reason,
    )
