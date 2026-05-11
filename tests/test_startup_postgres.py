from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.core import startup


def _app_config():
    return SimpleNamespace(
        postgres=SimpleNamespace(enabled=True),
        observability=SimpleNamespace(environment="test"),
    )


class StartupPostgresTests(unittest.TestCase):
    def test_postgres_startup_fails_fast_when_pool_init_fails(self):
        with patch.object(startup, "load_app_config", return_value=_app_config()), patch.object(
            startup, "is_postgres_enabled", return_value=True
        ), patch.object(startup, "init_pool", side_effect=RuntimeError("postgres boom")):
            with self.assertRaises(RuntimeError):
                startup.run_startup_checks("test-api")

        status = startup.get_startup_status()
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["last_error"], "postgres boom")
        self.assertFalse(status["postgres_initialized"])

    def test_postgres_startup_order_is_pool_schema_seed(self):
        events: list[str] = []

        def init_pool():
            events.append("init_pool")

        def ensure_schema():
            events.append("ensure_tenants_schema")

        def seed_tenant():
            events.append("seed_default_tenant_from_env")
            return {"id": "11111111-1111-1111-1111-111111111111", "name": "Demo"}

        with patch.object(startup, "load_app_config", return_value=_app_config()), patch.object(
            startup, "is_postgres_enabled", return_value=True
        ), patch.object(startup, "init_pool", side_effect=init_pool), patch(
            "backend.db.tenants.ensure_tenants_schema", side_effect=ensure_schema
        ), patch(
            "backend.db.tenants.seed_default_tenant_from_env", side_effect=seed_tenant
        ):
            startup.run_startup_checks("test-api")

        self.assertEqual(events, ["init_pool", "ensure_tenants_schema", "seed_default_tenant_from_env"])
        self.assertEqual(startup.get_startup_status()["status"], "ok")

    def test_postgres_startup_fails_when_seed_fails(self):
        with patch.object(startup, "load_app_config", return_value=_app_config()), patch.object(
            startup, "is_postgres_enabled", return_value=True
        ), patch.object(startup, "init_pool"), patch("backend.db.tenants.ensure_tenants_schema"), patch(
            "backend.db.tenants.seed_default_tenant_from_env",
            side_effect=RuntimeError("DEFAULT_PHONE_NUMBER must be set"),
        ):
            with self.assertRaises(RuntimeError):
                startup.run_startup_checks("test-api")

        status = startup.get_startup_status()
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["last_error"], "DEFAULT_PHONE_NUMBER must be set")
        self.assertFalse(status["postgres_initialized"])


if __name__ == "__main__":
    unittest.main()
