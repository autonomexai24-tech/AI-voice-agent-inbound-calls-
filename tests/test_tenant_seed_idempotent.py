from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.db import tenants


class _FakeCursor:
    def __init__(self, *, existing=None, inserted=None):
        self.existing = existing
        self.inserted = inserted
        self.statements: list[str] = []
        self.last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.last_sql = " ".join(str(sql).split())
        self.statements.append(self.last_sql)

    def fetchone(self):
        if "SELECT id, slug FROM tenants" in self.last_sql:
            return self.existing
        if "INSERT INTO tenants" in self.last_sql:
            return self.inserted
        return None

    def fetchall(self):
        return []


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self._cursor


class TenantSeedIdempotencyTests(unittest.TestCase):
    def test_seed_skips_insert_when_slug_already_exists(self):
        cursor = _FakeCursor(existing=("tenant-1", "autonomex-ai"))
        env = {
            "DEFAULT_BUSINESS_NAME": "Autonomex AI",
            "DEFAULT_PHONE_NUMBER": "+917676808950",
            "DEFAULT_SYSTEM_PROMPT": "Tenant prompt",
            "DEFAULT_WELCOME_MESSAGE": "Tenant greeting",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            tenants, "get_connection", return_value=_FakeConnection(cursor)
        ), patch.object(
            tenants,
            "get_tenant_by_did",
            return_value={"id": "tenant-1", "slug": "autonomex-ai"},
        ):
            result = tenants.seed_default_tenant_from_env()

        self.assertEqual(result["id"], "tenant-1")
        self.assertFalse(any(statement.startswith("INSERT INTO tenants") for statement in cursor.statements))
        self.assertTrue(any("UPDATE tenants SET is_active = FALSE" in statement for statement in cursor.statements))

    def test_seed_insert_uses_conflict_safe_statement(self):
        cursor = _FakeCursor(existing=None, inserted=("tenant-2",))
        env = {
            "DEFAULT_BUSINESS_NAME": "Autonomex AI",
            "DEFAULT_PHONE_NUMBER": "+917676808950",
            "DEFAULT_SYSTEM_PROMPT": "Tenant prompt",
            "DEFAULT_WELCOME_MESSAGE": "Tenant greeting",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            tenants, "get_connection", return_value=_FakeConnection(cursor)
        ), patch.object(
            tenants,
            "get_tenant_by_did",
            return_value={"id": "tenant-2", "slug": "autonomex-ai"},
        ):
            tenants.seed_default_tenant_from_env()

        insert_statements = [statement for statement in cursor.statements if statement.startswith("INSERT INTO tenants")]
        self.assertEqual(len(insert_statements), 1)
        self.assertIn("ON CONFLICT DO NOTHING", insert_statements[0])

    def test_schema_deactivates_duplicate_slugs_before_creating_unique_slug_index(self):
        cursor = _FakeCursor()
        with patch.object(tenants, "get_connection", return_value=_FakeConnection(cursor)):
            tenants.ensure_tenants_schema()

        cleanup_index = next(i for i, statement in enumerate(cursor.statements) if "WITH ranked AS" in statement)
        unique_slug_index = next(i for i, statement in enumerate(cursor.statements) if "idx_tenants_slug_lower" in statement)
        self.assertLess(cleanup_index, unique_slug_index)


if __name__ == "__main__":
    unittest.main()
