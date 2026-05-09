"""Tenant resolution + config loading (Phase 2 scaffolding).

Thin wrapper over `backend.db.tenants`. The voice agent will call
`resolve_from_did()` at call start to:
  1. Map the dialed Vobiz DID to a tenant id.
  2. Load the tenant_config row (prompt, voice, language, hours, etc.).

Phase 2 rule: thin. This service does not cache, does not reach out to
external providers, and does not retry — the PG pool already handles
connection concerns, and the tenant lookup is a single indexed query.

Not yet wired to agent.py. Phase 3 will replace the current
`get_live_config()` + `config.json` cascade with this.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from backend.db import tenants as tenant_repo
from backend.utils.logging import get_logger

logger = get_logger("backend.services.tenant")


class TenantNotConfiguredError(LookupError):
    """Raised when a DID does not map to any configured tenant."""


class TenantService:
    """Facade for tenant resolution and config access."""

    def resolve_from_did(self, phone_number: str) -> dict:
        """Return `{tenant, config}` for an inbound call's DID.

        Raises TenantNotConfiguredError if no tenant matches the DID, per
        the error handling policy documented in WORKFLOW.md §10.
        """
        tenant = tenant_repo.get_tenant_by_did(phone_number)
        if tenant is None:
            logger.warning(
                "tenant.resolve.not_found",
                extra={"phone_number_masked": _mask_phone(phone_number)},
            )
            raise TenantNotConfiguredError(phone_number)

        config = tenant_repo.get_tenant_config(tenant["id"])
        if config is None:
            # The DID is known but has no config row. This is a
            # provisioning bug; surface it clearly rather than falling
            # back to defaults that might leak another tenant's prompt.
            logger.error(
                "tenant.config.missing",
                extra={"tenant_id": str(tenant["id"])},
            )
            raise TenantNotConfiguredError(phone_number)

        return {"tenant": tenant, "config": config}

    def get_by_id(self, tenant_id: UUID) -> Optional[dict]:
        return tenant_repo.get_tenant_by_id(tenant_id)

    def update_config(self, tenant_id: UUID, updates: dict) -> None:
        tenant_repo.update_tenant_config(tenant_id, updates)


def _mask_phone(phone: str) -> str:
    """Mask all but the last 2 digits. Used in logs (EXECUTION.md §9)."""
    digits = [c for c in phone if c.isdigit()]
    if len(digits) <= 2:
        return "XX"
    masked = "X" * (len(digits) - 2) + "".join(digits[-2:])
    return f"+{masked}" if phone.startswith("+") else masked
