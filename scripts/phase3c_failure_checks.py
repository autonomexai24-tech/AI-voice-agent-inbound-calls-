"""Phase 3C lightweight failure-mode checks.

This script is for staging operators. It simulates safe failure paths without
placing a live call and without contacting optional third-party services.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.config_resolver import resolve_runtime_config
from backend.core.startup import run_startup_checks
from backend.integrations.sms import SMSResult
from backend.services.notification_service import NotificationService

CRITICAL_ENV = (
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "OPENAI_API_KEY",
    "SARVAM_API_KEY",
)

OPTIONAL_ENV = (
    "CAL_API_KEY",
    "CAL_EVENT_TYPE_ID",
    "FAST2SMS_API_KEY",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "S3_ENDPOINT",
)


class _FailingSMSProvider:
    name = "phase3c-failing-provider"

    async def send_sms(self, phone_number: str, message: str) -> SMSResult:
        return SMSResult(
            provider=self.name,
            phone_number=phone_number,
            status="failed",
            error_message="simulated_sms_failure",
        )


@contextmanager
def _patched_env(values: dict[str, str | None]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ.update({key: value})
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ.update({key: value})


def check_postgres_unavailable() -> dict:
    env = {key: "phase3c-dummy" for key in CRITICAL_ENV}
    env.update({"USE_POSTGRES": "true", "DATABASE_URL": None})
    with _patched_env(env):
        try:
            run_startup_checks("phase3c-postgres-unavailable", strict_config=True)
        except Exception as exc:  # noqa: BLE001
            return {"ok": True, "expected": "startup fails fast", "error_type": type(exc).__name__}
    return {"ok": False, "expected": "startup should fail fast when PostgreSQL is unavailable"}


def check_invalid_did_metadata() -> dict:
    env = {key: "phase3c-dummy" for key in CRITICAL_ENV}
    env.update({"USE_POSTGRES": "true", "DATABASE_URL": None})
    with _patched_env(env):
        resolved = resolve_runtime_config(caller_phone="unknown", did="not-a-valid-did")
    return {
        "ok": resolved.tenant_id is None,
        "source": resolved.source,
        "fallback_reason": resolved.fallback_reason,
    }


def check_missing_optional_integrations() -> dict:
    env = {key: "phase3c-dummy" for key in CRITICAL_ENV}
    env.update({key: None for key in OPTIONAL_ENV})
    env["USE_POSTGRES"] = "false"
    with _patched_env(env):
        run_startup_checks(
            "phase3c-missing-optional",
            initialize_postgres=False,
            strict_config=True,
        )
    return {"ok": True, "expected": "optional integrations do not fail startup"}


async def check_sms_failure() -> dict:
    result = await NotificationService(_FailingSMSProvider()).send_booking_confirmation_sms(
        tenant_id=None,
        call_id="phase3c-sms-failure",
        caller_name="Phase3C Test",
        caller_phone="+919876543210",
        booking_time_iso="2026-05-09T10:00:00+05:30",
        business_name="Phase3C Clinic",
    )
    return {
        "ok": result.status == "failed",
        "status": result.status,
        "error": result.error_message,
    }


async def check_calcom_failure() -> dict:
    import calendar_tools

    async def _simulated_failure(start_time, caller_name, caller_phone, notes, **kwargs):
        return {
            "success": False,
            "booking_id": None,
            "message": "simulated_calcom_failure",
        }

    original = calendar_tools._create_booking_calcom
    calendar_tools._create_booking_calcom = _simulated_failure
    try:
        result = await calendar_tools.async_create_booking(
            "2026-05-09T10:00:00+05:30",
            "Phase3C Test",
            "+919876543210",
            "Phase 3C simulated Cal.com failure",
        )
    finally:
        calendar_tools._create_booking_calcom = original

    return {
        "ok": result.get("success") is False,
        "success": result.get("success"),
        "message": str(result.get("message", ""))[:200],
    }


async def main() -> None:
    results = {
        "postgres_unavailable": check_postgres_unavailable(),
        "missing_tenant_or_invalid_did": check_invalid_did_metadata(),
        "missing_optional_integrations": check_missing_optional_integrations(),
        "sms_failure": await check_sms_failure(),
        "calcom_failure": await check_calcom_failure(),
    }
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
