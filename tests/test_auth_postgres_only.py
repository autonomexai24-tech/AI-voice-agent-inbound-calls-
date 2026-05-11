from __future__ import annotations

import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

import ui_server


TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


class AuthCursor:
    def __init__(self, *, tenant=None, user=None, other_workspace=False) -> None:
        self.tenant = tenant
        self.user = user
        self.other_workspace = other_workspace
        self.query = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=()) -> None:
        self.query = " ".join(str(query).lower().split())

    def fetchone(self):
        if "from tenants" in self.query and "where lower(slug)" in self.query:
            return self.tenant
        if "from users" in self.query and "tenant_id = %s" in self.query:
            return self.user
        if "join tenants" in self.query:
            return ("other-workspace",) if self.other_workspace else None
        return None

    def fetchall(self):
        return []


class AuthConnection:
    def __init__(self, cursor: AuthCursor) -> None:
        self.cursor_obj = cursor

    def cursor(self):
        return self.cursor_obj


def tenant_row(tenant_id=TENANT_A, slug="autonomex-ai", active=True):
    return (tenant_id, "Autonomex AI", slug, "+917676808950", active)


def user_row(tenant_id=TENANT_A):
    return ("harsh@example.com", "stored-hash", tenant_id)


def connection_factory(cursor: AuthCursor):
    @contextmanager
    def _fake_connection():
        yield AuthConnection(cursor)

    return _fake_connection


def session_token(*, tenant_id=TENANT_A, tenant_slug="autonomex-ai", exp=None):
    payload = {
        "email": "harsh@example.com",
        "tenant_id": tenant_id,
        "tenant_name": "Autonomex AI",
        "tenant_slug": tenant_slug,
        "tenant_phone": "+917676808950",
        "exp": exp if exp is not None else int(time.time()) + 3600,
    }
    encoded = ui_server._b64encode(ui_server.json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{encoded}.{ui_server._sign_session(encoded)}"


class PostgresOnlyAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(ui_server.app)

    def test_env_credentials_do_not_authenticate_when_postgres_disabled(self):
        self.assertFalse(hasattr(ui_server, "_env_login"))
        with patch.dict(
            os.environ,
            {"DASHBOARD_EMAIL": "harsh@example.com", "DASHBOARD_PASSWORD": "admin123"},
            clear=False,
        ):
            with patch("ui_server.is_postgres_enabled", return_value=False):
                response = self.client.post(
                    "/api/auth/login",
                    json={"email": "harsh@example.com", "password": "admin123", "tenant_slug": "default"},
                )

        self.assertEqual(response.status_code, 503)
        self.assertIn("PostgreSQL", response.json()["detail"])

    def test_missing_workspace_is_distinct(self):
        with patch("ui_server.is_postgres_enabled", return_value=True):
            response = self.client.post(
                "/api/auth/login",
                json={"email": "harsh@example.com", "password": "correct"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Workspace is required", response.json()["detail"])

    def test_unknown_workspace_is_distinct(self):
        cursor = AuthCursor(tenant=None)
        with patch("ui_server.is_postgres_enabled", return_value=True), patch(
            "ui_server.get_connection", connection_factory(cursor)
        ):
            response = self.client.post(
                "/api/auth/login",
                json={"email": "harsh@example.com", "password": "correct", "tenant_slug": "missing"},
            )

        self.assertEqual(response.status_code, 404)
        self.assertIn("Workspace not found", response.json()["detail"])

    def test_wrong_workspace_is_distinct(self):
        cursor = AuthCursor(tenant=tenant_row(), user=None, other_workspace=True)
        with patch("ui_server.is_postgres_enabled", return_value=True), patch(
            "ui_server.get_connection", connection_factory(cursor)
        ):
            response = self.client.post(
                "/api/auth/login",
                json={"email": "harsh@example.com", "password": "correct", "tenant_slug": "autonomex-ai"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("different workspace", response.json()["detail"])

    def test_invalid_password_is_distinct(self):
        cursor = AuthCursor(tenant=tenant_row(), user=user_row())
        with patch("ui_server.is_postgres_enabled", return_value=True), patch(
            "ui_server.get_connection", connection_factory(cursor)
        ), patch("ui_server._verify_password", return_value=False):
            response = self.client.post(
                "/api/auth/login",
                json={"email": "harsh@example.com", "password": "wrong", "tenant_slug": "autonomex-ai"},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid password.")

    def test_successful_login_sets_session_cookie(self):
        cursor = AuthCursor(tenant=tenant_row(), user=user_row())
        with patch("ui_server.is_postgres_enabled", return_value=True), patch(
            "ui_server.get_connection", connection_factory(cursor)
        ), patch("ui_server._verify_password", return_value=True):
            response = self.client.post(
                "/api/auth/login",
                json={"email": "harsh@example.com", "password": "correct", "tenant_slug": "autonomex-ai"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["tenant_slug"], "autonomex-ai")
        self.assertIn(ui_server.SESSION_COOKIE, response.headers.get("set-cookie", ""))

    def test_signup_auto_login_returns_workspace_slug_and_sets_cookie(self):
        provisioned = {
            "tenant": {
                "id": UUID(TENANT_A),
                "name": "Autonomex AI",
                "slug": "autonomex-ai",
                "phone_number": "+917676808950",
            },
            "user": {"email": "harsh@example.com"},
        }
        with patch("ui_server.is_postgres_enabled", return_value=True), patch(
            "backend.services.tenant_service.TenantService.provision", return_value=provisioned
        ):
            response = self.client.post(
                "/api/auth/signup",
                json={
                    "name": "Harsh",
                    "company": "Autonomex AI",
                    "phone_number": "7676808950",
                    "email": "harsh@example.com",
                    "password": "correct-password",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["tenant_slug"], "autonomex-ai")
        self.assertIn(ui_server.SESSION_COOKIE, response.headers.get("set-cookie", ""))

    def test_logout_clears_session_cookie(self):
        self.client.cookies.set(ui_server.SESSION_COOKIE, session_token())
        response = self.client.post("/api/auth/logout")

        self.assertEqual(response.status_code, 200)
        self.assertIn("rapid_session", response.headers.get("set-cookie", ""))
        self.assertIn("Max-Age=0", response.headers.get("set-cookie", ""))

    def test_expired_session_is_rejected(self):
        self.client.cookies.set(ui_server.SESSION_COOKIE, session_token(exp=int(time.time()) - 10))
        with patch("ui_server.is_postgres_enabled", return_value=True):
            response = self.client.get("/api/auth/session")

        self.assertEqual(response.status_code, 401)

    def test_missing_tenant_invalidates_session(self):
        self.client.cookies.set(ui_server.SESSION_COOKIE, session_token())
        with patch("ui_server.is_postgres_enabled", return_value=True), patch(
            "backend.db.tenants.get_tenant_by_id", return_value=None
        ):
            response = self.client.get("/api/auth/session")

        self.assertEqual(response.status_code, 401)
        self.assertIn("Tenant no longer exists", response.json()["detail"])

    def test_tenant_slug_mismatch_invalidates_session(self):
        self.client.cookies.set(ui_server.SESSION_COOKIE, session_token(tenant_slug="old-slug"))
        tenant = {
            "id": UUID(TENANT_A),
            "name": "Autonomex AI",
            "slug": "autonomex-ai",
            "phone_number": "+917676808950",
            "is_active": True,
        }
        with patch("ui_server.is_postgres_enabled", return_value=True), patch(
            "backend.db.tenants.get_tenant_by_id", return_value=tenant
        ):
            response = self.client.get("/api/auth/session")

        self.assertEqual(response.status_code, 401)
        self.assertIn("Workspace session", response.json()["detail"])

    def test_sessions_keep_tenant_isolation_for_dashboard_queries(self):
        captured_tenants: list[UUID] = []

        def get_tenant_by_id(tenant_id: UUID):
            slug = "tenant-a" if str(tenant_id) == TENANT_A else "tenant-b"
            return {
                "id": tenant_id,
                "name": slug,
                "slug": slug,
                "phone_number": "+910000000000",
                "is_active": True,
            }

        def fetch_call_logs(tenant_id: UUID, limit=50, offset=0):
            captured_tenants.append(tenant_id)
            return []

        client_a = TestClient(ui_server.app)
        client_b = TestClient(ui_server.app)
        client_a.cookies.set(ui_server.SESSION_COOKIE, session_token(tenant_id=TENANT_A, tenant_slug="tenant-a"))
        client_b.cookies.set(ui_server.SESSION_COOKIE, session_token(tenant_id=TENANT_B, tenant_slug="tenant-b"))

        with patch("ui_server.is_postgres_enabled", return_value=True), patch(
            "backend.db.tenants.get_tenant_by_id", side_effect=get_tenant_by_id
        ), patch("backend.db.call_logs.fetch_call_logs", side_effect=fetch_call_logs):
            self.assertEqual(client_a.get("/api/logs").status_code, 200)
            self.assertEqual(client_b.get("/api/logs").status_code, 200)

        self.assertEqual(captured_tenants, [UUID(TENANT_A), UUID(TENANT_B)])

    def test_concurrent_logins_do_not_share_session_state(self):
        cursor = AuthCursor(tenant=tenant_row(), user=user_row())

        def login_once():
            return ui_server._authenticate("harsh@example.com", "correct", "autonomex-ai")

        with patch("ui_server.is_postgres_enabled", return_value=True), patch(
            "ui_server.get_connection", connection_factory(cursor)
        ), patch("ui_server._verify_password", return_value=True):
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(lambda _: login_once(), range(4)))

        self.assertEqual([result["tenant_id"] for result in results], [TENANT_A] * 4)


if __name__ == "__main__":
    unittest.main()
