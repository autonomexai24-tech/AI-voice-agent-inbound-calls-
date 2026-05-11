from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.db import tenants


class _FakeCursor:
    def __init__(self, *, existing=None, inserted=None, duplicate_rows=None, existing_tables=None):
        self.existing = existing
        self.inserted = inserted
        self.duplicate_rows = duplicate_rows or []
        self.existing_tables = set(existing_tables or [])
        self.statements: list[str] = []
        self.last_sql = ""
        self.last_params = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.last_sql = " ".join(str(sql).split())
        self.last_params = params
        self.statements.append(self.last_sql)

    def fetchone(self):
        if "SELECT id, slug FROM tenants" in self.last_sql:
            return self.existing
        if "INSERT INTO tenants" in self.last_sql:
            return self.inserted
        if "SELECT to_regclass" in self.last_sql:
            table_name = str((self.last_params or [""])[0]).replace("public.", "")
            return (table_name,) if table_name in self.existing_tables else (None,)
        return None

    def fetchall(self):
        if "WITH ranked AS" in self.last_sql:
            return self.duplicate_rows
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

    def test_schema_cleans_duplicate_slugs_before_creating_unique_slug_index(self):
        cursor = _FakeCursor()
        with patch.object(tenants, "get_connection", return_value=_FakeConnection(cursor)):
            tenants.ensure_tenants_schema()

        cleanup_index = next(i for i, statement in enumerate(cursor.statements) if "WITH ranked AS" in statement)
        unique_slug_index = next(i for i, statement in enumerate(cursor.statements) if "idx_tenants_slug_lower" in statement)
        self.assertLess(cleanup_index, unique_slug_index)

    def test_cleanup_deletes_duplicate_tenant_rows_after_reassigning_children(self):
        duplicate_id = "22222222-2222-2222-2222-222222222222"
        keep_id = "11111111-1111-1111-1111-111111111111"
        cursor = _FakeCursor(
            duplicate_rows=[(duplicate_id, keep_id, "autonomex-ai")],
            existing_tables={"call_logs"},
        )
        deleted_count = tenants._delete_duplicate_tenant_slugs(cursor)

        self.assertEqual(deleted_count, 1)
        child_update_index = next(i for i, statement in enumerate(cursor.statements) if statement.startswith("UPDATE call_logs"))
        delete_index = next(i for i, statement in enumerate(cursor.statements) if statement.startswith("DELETE FROM tenants"))
        self.assertLess(child_update_index, delete_index)


if __name__ == "__main__":
    unittest.main()
