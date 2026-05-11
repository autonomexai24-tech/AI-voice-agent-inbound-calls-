from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import agent
from app import bootstrap


class WorkerBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_start_worker_awaits_bootstrap_before_cli_entry(self):
        with patch.object(agent, "bootstrap_system", new=AsyncMock()) as bootstrap_system:
            await agent.start_worker()

        bootstrap_system.assert_awaited_once_with("voice-agent")

    async def test_bootstrap_runs_pool_schema_seed_before_worker_start(self):
        events: list[str] = []

        def init_pool():
            events.append("init_pool")

        def ensure_schema():
            events.append("ensure_tenants_schema")

        def seed_tenant():
            events.append("seed_default_tenant_from_env")
            return {"id": "11111111-1111-1111-1111-111111111111", "name": "Autonomex AI"}

        with patch.object(bootstrap, "load_app_config", return_value=SimpleNamespace()), patch.object(
            bootstrap, "is_postgres_enabled", return_value=True
        ), patch.object(bootstrap, "init_pool", side_effect=init_pool), patch.object(
            bootstrap, "ensure_tenants_schema", side_effect=ensure_schema
        ), patch.object(
            bootstrap, "seed_default_tenant_from_env", side_effect=seed_tenant
        ):
            result = await bootstrap.bootstrap_system("voice-agent-test")

        self.assertEqual(events, ["init_pool", "ensure_tenants_schema", "seed_default_tenant_from_env"])
        self.assertEqual(result["tenant"]["name"], "Autonomex AI")

    async def test_bootstrap_fails_when_postgres_disabled(self):
        with patch.object(bootstrap, "load_app_config", return_value=SimpleNamespace()), patch.object(
            bootstrap, "is_postgres_enabled", return_value=False
        ):
            with self.assertRaises(RuntimeError):
                await bootstrap.bootstrap_system("voice-agent-test")

    async def test_bootstrap_fails_when_seed_fails(self):
        with patch.object(bootstrap, "load_app_config", return_value=SimpleNamespace()), patch.object(
            bootstrap, "is_postgres_enabled", return_value=True
        ), patch.object(bootstrap, "init_pool"), patch.object(bootstrap, "ensure_tenants_schema"), patch.object(
            bootstrap, "seed_default_tenant_from_env", side_effect=RuntimeError("seed failed")
        ):
            with self.assertRaises(RuntimeError):
                await bootstrap.bootstrap_system("voice-agent-test")


if __name__ == "__main__":
    unittest.main()
