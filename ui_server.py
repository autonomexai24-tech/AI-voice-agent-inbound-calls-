import json
import os
import base64
import hashlib
import hmac
import time
import threading
from uuid import UUID
import bcrypt
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse
from dotenv import load_dotenv
from backend.core.health import aggregate_health
from backend.core.startup import run_startup_checks
from backend.db.connection import get_connection, is_postgres_enabled
from backend.logging import RequestTracingMiddleware, configure_logging, get_logger, init_sentry, set_correlation_context
from backend.utils.formatting import mask_phone

load_dotenv()

configure_logging("api")
init_sentry("api")
logger = get_logger("ui-server")

app = FastAPI(title="RapidX AI Dashboard")
app.add_middleware(RequestTracingMiddleware, service="api")


@app.on_event("startup")
async def startup_event():
    run_startup_checks("api")
    _validate_api_security_env()


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(self), geolocation=()")
    if request.headers.get("x-request-id"):
        response.headers.setdefault("X-Request-ID", request.headers["x-request-id"])
    return response


@app.middleware("http")
async def api_rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    try:
        if path.startswith("/api/auth"):
            _assert_rate_limit(request, "auth", limit=120, window_seconds=60)
        elif path.startswith("/api/admin"):
            _assert_rate_limit(request, "admin", limit=60, window_seconds=60)
        elif path.startswith("/api/recordings"):
            _assert_rate_limit(request, "sensitive_api", limit=120, window_seconds=60)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)

CONFIG_FILE = "config.json"
SESSION_COOKIE = "rapid_session"
SESSION_TTL_SECONDS = 60 * 60 * 12
_RATE_LIMITS: dict[str, list[float]] = {}
_RATE_LIMIT_LOCK = threading.Lock()
PUBLIC_CONFIG_KEYS = {
    "first_line",
    "agent_instructions",
    "stt_min_endpointing_delay",
    "llm_model",
    "tts_voice",
    "tts_language",
    "lang_preset",
    "business_name",
    "business_phone",
    "business_hours_json",
    "transfer_number",
    "cal_event_type_id",
}


class AmbiguousTenantLogin(Exception):
    """Raised when an email exists in multiple tenants and no workspace is supplied."""


def _is_production() -> bool:
    return (os.environ.get("ENVIRONMENT") or os.environ.get("APP_ENV") or os.environ.get("NODE_ENV") or "").lower() == "production"


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_key(request: Request, scope: str, discriminator: str = "") -> str:
    return f"{scope}:{_client_ip(request)}:{discriminator.strip().lower()}"


def _check_rate_limit(key: str, *, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    cutoff = now - window_seconds
    with _RATE_LIMIT_LOCK:
        hits = [ts for ts in _RATE_LIMITS.get(key, []) if ts >= cutoff]
        if len(hits) >= limit:
            _RATE_LIMITS[key] = hits
            return False
        hits.append(now)
        _RATE_LIMITS[key] = hits
        return True


def _assert_rate_limit(request: Request, scope: str, *, discriminator: str = "", limit: int, window_seconds: int) -> None:
    key = _rate_limit_key(request, scope, discriminator)
    if not _check_rate_limit(key, limit=limit, window_seconds=window_seconds):
        logger.warning("rate_limit.blocked", extra={"scope": scope, "path": request.url.path})
        raise HTTPException(status_code=429, detail="Too many requests. Try again later.")


def _validate_api_security_env() -> None:
    if not _is_production():
        return
    if _session_secret().decode("utf-8") == "dev-session-secret":
        raise RuntimeError("SESSION_SECRET or DASHBOARD_SESSION_SECRET must be set in production")

def read_config():
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
                config = raw if isinstance(raw, dict) else {}
        except Exception as exc:
            logger.warning("config.read_failed", extra={"error_type": type(exc).__name__})
            config = {}
    config = {k: v for k, v in config.items() if k in PUBLIC_CONFIG_KEYS}

    def get_val(key, env_key, default=""):
        return config.get(key) if config.get(key) else os.getenv(env_key, default)

    return {
        "first_line": get_val("first_line", "FIRST_LINE", "Namaste! This is Aryan from RapidX AI — we help businesses automate with AI. Hmm, may I ask what kind of business you run?"),
        "agent_instructions": get_val("agent_instructions", "AGENT_INSTRUCTIONS", ""),
        "stt_min_endpointing_delay": float(get_val("stt_min_endpointing_delay", "STT_MIN_ENDPOINTING_DELAY", 0.6)),
        "llm_model": get_val("llm_model", "LLM_MODEL", "gpt-4o-mini"),
        "tts_voice": get_val("tts_voice", "TTS_VOICE", "kavya"),
        "tts_language": get_val("tts_language", "TTS_LANGUAGE", "hi-IN"),
        "sip_trunk_id": get_val("sip_trunk_id", "SIP_TRUNK_ID", ""),
        "cal_event_type_id": get_val("cal_event_type_id", "CAL_EVENT_TYPE_ID", ""),
        **config
    }

def write_config(data):
    if _is_production():
        raise HTTPException(status_code=503, detail="File-backed config is disabled in production")
    updates = {k: v for k, v in data.items() if k in PUBLIC_CONFIG_KEYS}
    config = {k: v for k, v in read_config().items() if k in PUBLIC_CONFIG_KEYS}
    config.update(updates)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

def _session_secret() -> bytes:
    secret = (
        os.environ.get("SESSION_SECRET")
        or os.environ.get("DASHBOARD_SESSION_SECRET")
        or os.environ.get("LIVEKIT_API_SECRET")
        or "dev-session-secret"
    )
    return secret.encode("utf-8")

def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))

def _sign_session(payload: str) -> str:
    return hmac.new(_session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()

def _create_session(user: dict) -> str:
    payload = {
        "email": user["email"],
        "tenant_id": user["tenant_id"],
        "tenant_name": user.get("tenant_name") or "RapidX AI",
        "tenant_slug": user.get("tenant_slug") or "default",
        "tenant_phone": user.get("tenant_phone") or "",
        "exp": int(time.time()) + SESSION_TTL_SECONDS,
    }
    encoded = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{encoded}.{_sign_session(encoded)}"

def _read_session(request: Request) -> dict | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw or "." not in raw:
        return None
    payload, signature = raw.rsplit(".", 1)
    if not hmac.compare_digest(_sign_session(payload), signature):
        return None
    try:
        data = json.loads(_b64decode(payload))
    except Exception:
        return None
    if int(data.get("exp", 0)) < int(time.time()):
        return None
    return data

def require_session(request: Request) -> dict:
    session = _read_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")
    _validate_session_tenant(session)
    set_correlation_context(tenant_id=str(session.get("tenant_id") or ""))
    return session

async def _json_body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")
    return data

def _verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    if stored_hash.startswith("$2a$") or stored_hash.startswith("$2b$") or stored_hash.startswith("$2y$"):
        try:
            return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
        except Exception:
            return False
    if stored_hash.startswith("pbkdf2_sha256$"):
        try:
            _, rounds, salt, digest = stored_hash.split("$", 3)
            computed = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt.encode("utf-8"),
                int(rounds),
            ).hex()
            return hmac.compare_digest(computed, digest)
        except Exception:
            return False
    if stored_hash.startswith("sha256$"):
        try:
            _, salt, digest = stored_hash.split("$", 2)
            computed = hashlib.sha256(f"{salt}{password}".encode("utf-8")).hexdigest()
            return hmac.compare_digest(computed, digest)
        except Exception:
            return False
    return False

def _env_login(email: str, password: str, tenant_slug: str | None = None) -> dict | None:
    expected_email = os.environ.get("DASHBOARD_EMAIL") or os.environ.get("ADMIN_EMAIL")
    expected_password = os.environ.get("DASHBOARD_PASSWORD") or os.environ.get("ADMIN_PASSWORD")
    expected_slug = os.environ.get("DASHBOARD_TENANT_SLUG") or "default"
    if not expected_email or not expected_password:
        return None
    if tenant_slug and tenant_slug.strip().lower() != expected_slug.strip().lower():
        return None
    if not hmac.compare_digest(email.strip().lower(), expected_email.strip().lower()):
        return None
    if not hmac.compare_digest(password, expected_password):
        return None
    return {
        "email": expected_email,
        "tenant_id": os.environ.get("DASHBOARD_TENANT_ID") or os.environ.get("TENANT_ID") or "legacy",
        "tenant_name": os.environ.get("DASHBOARD_TENANT_NAME") or "RapidX AI",
        "tenant_slug": expected_slug,
        "tenant_phone": os.environ.get("DASHBOARD_TENANT_PHONE") or os.environ.get("DASHBOARD_BUSINESS_PHONE") or "",
    }

def _postgres_login(email: str, password: str, tenant_slug: str | None = None) -> dict | None:
    if not is_postgres_enabled():
        return None
    if not tenant_slug:
        raise AmbiguousTenantLogin()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, slug, phone_number, is_active
                    FROM tenants
                    WHERE lower(slug) = lower(%s)
                      AND is_active = TRUE
                    LIMIT 1
                    """,
                    (tenant_slug.strip(),),
                )
                tenant = cur.fetchone()
                if tenant is None:
                    return None
                cur.execute(
                    """
                    SELECT email, password_hash, tenant_id
                    FROM users
                    WHERE tenant_id = %s
                      AND lower(email) = lower(%s)
                    LIMIT 1
                    """,
                    (str(tenant[0]), email.strip()),
                )
                row = cur.fetchone()
    except Exception as exc:
        logger.warning("Auth postgres lookup failed: %s", type(exc).__name__)
        return None
    if not row:
        return None
    if not _verify_password(password, row[1]):
        return None
    return {
        "email": row[0],
        "tenant_id": str(row[2]),
        "tenant_name": tenant[1],
        "tenant_slug": tenant[2],
        "tenant_phone": tenant[3] or "",
    }

def _authenticate(email: str, password: str, tenant_slug: str | None = None) -> dict | None:
    return _postgres_login(email, password, tenant_slug) or _env_login(email, password, tenant_slug)

def _set_session_cookie(response: Response, token: str) -> None:
    secure = _is_production() or (os.environ.get("SESSION_COOKIE_SECURE") or "").lower() == "true"
    samesite = (os.environ.get("SESSION_COOKIE_SAMESITE") or "lax").lower()
    if samesite not in {"lax", "strict", "none"}:
        samesite = "lax"
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/",
    )

def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")

def _tenant_uuid(session: dict) -> UUID | None:
    tenant_id = session.get("tenant_id")
    if not tenant_id or tenant_id == "legacy":
        return None
    try:
        return UUID(str(tenant_id))
    except ValueError:
        return None

def _validate_session_tenant(session: dict) -> None:
    tenant_id = _tenant_uuid(session)
    if not tenant_id or not is_postgres_enabled():
        return
    try:
        from backend.db.tenants import get_tenant_by_id

        tenant = get_tenant_by_id(tenant_id)
    except Exception as exc:
        logger.warning("Session tenant validation failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Tenant validation unavailable")
    if not tenant or not tenant.get("is_active", True):
        raise HTTPException(status_code=401, detail="Tenant is not active")

def _user_for_response(session: dict) -> dict:
    user = dict(session)
    tenant_id = _tenant_uuid(user)
    if tenant_id and is_postgres_enabled():
        try:
            from backend.db.tenants import get_tenant_by_id

            tenant = get_tenant_by_id(tenant_id)
            if tenant:
                user["tenant_name"] = tenant.get("name") or user.get("tenant_name")
                user["tenant_slug"] = tenant.get("slug") or user.get("tenant_slug")
                user["tenant_phone"] = tenant.get("phone_number") or user.get("tenant_phone") or ""
                user["tenant_active"] = bool(tenant.get("is_active", True))
        except Exception as exc:
            logger.warning("Session tenant refresh failed: %s", type(exc).__name__)
    return user

def _safe_config(config: dict) -> dict:
    return {k: v for k, v in config.items() if k in PUBLIC_CONFIG_KEYS}

def _legacy_config_for_response() -> dict:
    cfg = _safe_config(read_config())
    cfg.setdefault("business_name", os.environ.get("DASHBOARD_TENANT_NAME") or "RapidX AI")
    cfg.setdefault("business_phone", "")
    cfg.setdefault("transfer_number", os.environ.get("DEFAULT_TRANSFER_NUMBER", ""))
    cfg.setdefault("business_hours_json", "")
    return cfg

def _postgres_config_for_response(session: dict) -> dict | None:
    tenant_id = _tenant_uuid(session)
    if not tenant_id or not is_postgres_enabled():
        return None
    try:
        from backend.db.tenants import get_tenant_by_id, get_tenant_config

        tenant = get_tenant_by_id(tenant_id)
        cfg = get_tenant_config(tenant_id) or {}
    except Exception as exc:
        logger.warning("Tenant config fetch failed: %s", type(exc).__name__)
        return None
    business_hours = cfg.get("business_hours_json")
    if business_hours and not isinstance(business_hours, str):
        business_hours = json.dumps(business_hours, indent=2)
    return {
        "business_name": (tenant or {}).get("name") or session.get("tenant_name") or "",
        "business_phone": (tenant or {}).get("phone_number") or "",
        "tenant_slug": (tenant or {}).get("slug") or session.get("tenant_slug") or "",
        "tenant_active": bool((tenant or {}).get("is_active", True)),
        "agent_instructions": cfg.get("agent_instructions") or "",
        "first_line": cfg.get("first_line") or "",
        "tts_voice": cfg.get("tts_voice") or "kavya",
        "tts_language": cfg.get("tts_language") or "hi-IN",
        "lang_preset": cfg.get("lang_preset") or "multilingual",
        "llm_model": cfg.get("llm_model") or "gpt-4o-mini",
        "stt_min_endpointing_delay": cfg.get("endpointing_delay") or 0.5,
        "business_hours_json": business_hours or "",
        "transfer_number": cfg.get("transfer_number") or "",
        "cal_event_type_id": cfg.get("cal_event_type_id") or "",
        "config_source": "postgres",
    }

def _update_postgres_config(session: dict, data: dict) -> bool:
    tenant_id = _tenant_uuid(session)
    if not tenant_id or not is_postgres_enabled():
        return False
    updates = {}
    tenant_updates = {}
    mapping = {
        "agent_instructions": "agent_instructions",
        "first_line": "first_line",
        "tts_voice": "tts_voice",
        "tts_language": "tts_language",
        "lang_preset": "lang_preset",
        "llm_model": "llm_model",
        "stt_min_endpointing_delay": "endpointing_delay",
        "business_hours_json": "business_hours_json",
        "transfer_number": "transfer_number",
        "cal_event_type_id": "cal_event_type_id",
    }
    for public_key, db_key in mapping.items():
        if public_key in data:
            value = data[public_key]
            if public_key == "business_hours_json":
                if value in ("", None):
                    value = None
                elif isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        raise HTTPException(status_code=400, detail="Business hours must be valid JSON")
            updates[db_key] = value
    if "business_name" in data:
        tenant_updates["name"] = data["business_name"]
    if "business_phone" in data:
        tenant_updates["phone_number"] = data["business_phone"]

    if updates:
        from backend.db.tenants import update_tenant_config

        update_tenant_config(tenant_id, updates)

    if tenant_updates:
        from backend.db.tenants import update_tenant

        update_tenant(tenant_id, tenant_updates)
    return True

def _require_admin_token(request: Request) -> None:
    expected = os.environ.get("TENANT_ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=404, detail="Admin tenant provisioning is not enabled")
    supplied = request.headers.get("x-admin-token", "")
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Invalid admin token")

def _require_bearer_or_header_token(request: Request, env_name: str, header_name: str) -> None:
    expected = os.environ.get(env_name, "").strip()
    if not expected:
        if _is_production():
            raise HTTPException(status_code=404, detail="Endpoint is not enabled")
        return
    supplied = request.headers.get(header_name, "")
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Invalid token")

def _require_metrics_access(request: Request) -> None:
    if (os.environ.get("METRICS_PUBLIC") or "").lower() == "true":
        return
    _require_bearer_or_header_token(request, "METRICS_TOKEN", "x-metrics-token")

def _require_internal_access(request: Request) -> None:
    _require_bearer_or_header_token(request, "INTERNAL_API_TOKEN", "x-internal-token")

def _tenant_response(tenant: dict) -> dict:
    return {
        "id": str(tenant.get("id")),
        "name": tenant.get("name"),
        "slug": tenant.get("slug"),
        "phone_number": tenant.get("phone_number"),
        "is_active": bool(tenant.get("is_active", True)),
        "created_at": tenant.get("created_at"),
    }

# ── API Endpoints ──────────────────────────────────────────────────────────────

@app.post("/api/auth/login")
async def api_auth_login(request: Request, response: Response):
    data = await _json_body(request)
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    tenant_slug = (data.get("tenant_slug") or data.get("workspace") or "").strip() or None
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")
    _assert_rate_limit(request, "login", discriminator=email, limit=8, window_seconds=300)
    try:
        user = _authenticate(email, password, tenant_slug)
    except AmbiguousTenantLogin:
        raise HTTPException(status_code=409, detail="Workspace is required for this email")
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    _set_session_cookie(response, _create_session(user))
    return {"user": {k: user.get(k, "") for k in ("email", "tenant_id", "tenant_name", "tenant_slug", "tenant_phone")}}

@app.post("/api/auth/logout")
async def api_auth_logout(response: Response):
    _clear_session_cookie(response)
    return {"ok": True}

@app.get("/api/auth/me")
async def api_auth_me(user: dict = Depends(require_session)):
    return {"user": _user_for_response(user)}

@app.get("/api/admin/tenants")
async def api_admin_list_tenants(request: Request):
    _require_admin_token(request)
    if not is_postgres_enabled():
        raise HTTPException(status_code=503, detail="PostgreSQL is not enabled")
    try:
        from backend.services.tenant_service import TenantService

        tenants = TenantService().list(limit=200)
        return {"tenants": [_tenant_response(tenant) for tenant in tenants]}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Tenant list failed: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Tenant list failed")

@app.post("/api/admin/tenants")
async def api_admin_create_tenant(request: Request):
    _require_admin_token(request)
    if not is_postgres_enabled():
        raise HTTPException(status_code=503, detail="PostgreSQL is not enabled")
    data = await _json_body(request)
    required = ("name", "phone_number", "user_email", "user_password")
    missing = [key for key in required if not data.get(key)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {', '.join(missing)}")
    try:
        from backend.services.tenant_service import TenantService

        result = TenantService().provision(
            name=str(data["name"]),
            slug=str(data.get("slug") or data["name"]),
            phone_number=str(data["phone_number"]),
            user_email=str(data["user_email"]),
            user_password=str(data["user_password"]),
            config=data.get("config") if isinstance(data.get("config"), dict) else {},
            is_active=bool(data.get("is_active", True)),
        )
        return {
            "tenant": _tenant_response(result["tenant"]),
            "user": {"email": result["user"]["email"]},
        }
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logger.error("Tenant provisioning failed: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Tenant provisioning failed")

@app.patch("/api/admin/tenants/{tenant_id}")
async def api_admin_update_tenant(tenant_id: str, request: Request):
    _require_admin_token(request)
    if not is_postgres_enabled():
        raise HTTPException(status_code=503, detail="PostgreSQL is not enabled")
    data = await _json_body(request)
    try:
        from backend.services.tenant_service import TenantService

        changed = TenantService().update(UUID(tenant_id), data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Tenant update failed: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Tenant update failed")
    if not changed:
        raise HTTPException(status_code=404, detail="Tenant not found or no valid changes")
    return {"ok": True}

@app.get("/api/config")
async def api_get_config(user: dict = Depends(require_session)):
    return jsonable_encoder(_postgres_config_for_response(user) or _legacy_config_for_response())

@app.post("/api/config")
async def api_post_config(request: Request, user: dict = Depends(require_session)):
    data = await _json_body(request)
    if not _update_postgres_config(user, data):
        write_config(data)
    logger.info("Configuration updated via UI.")
    return {"status": "success"}

@app.get("/api/logs")
async def api_get_logs(user: dict = Depends(require_session)):
    tenant_id = _tenant_uuid(user)
    if tenant_id and is_postgres_enabled():
        try:
            from backend.db.call_logs import fetch_call_logs

            return jsonable_encoder(fetch_call_logs(tenant_id, limit=50))
        except Exception as e:
            logger.error("tenant.logs.fetch_failed", extra={"error_type": type(e).__name__})
            return []
    return []

@app.get("/api/logs/{log_id}/transcript")
async def api_get_transcript(log_id: str, user: dict = Depends(require_session)):
    tenant_id = _tenant_uuid(user)
    if tenant_id and is_postgres_enabled():
        try:
            from backend.db.call_logs import get_call_log

            row = get_call_log(tenant_id, UUID(log_id))
            if row is None:
                return PlainTextResponse(content="Not found", status_code=404)
            text = f"Call Log - {row.get('created_at', '')}\n"
            text += f"Phone: {row.get('phone_number', 'Unknown')}\n"
            text += f"Duration: {row.get('duration_seconds', 0)}s\n"
            text += f"Summary: {row.get('summary', '')}\n\n"
            text += "--- TRANSCRIPT ---\n"
            text += row.get("transcript") or "No transcript available."
            return PlainTextResponse(
                content=text,
                media_type="text/plain",
                headers={"Content-Disposition": f"attachment; filename=transcript_{log_id}.txt"},
            )
        except ValueError:
            return PlainTextResponse(content="Invalid log id", status_code=400)
        except Exception as e:
            return PlainTextResponse(content=f"Error: {type(e).__name__}", status_code=500)
    return PlainTextResponse(content="Not found", status_code=404)

@app.get("/api/bookings")
async def api_get_bookings(user: dict = Depends(require_session)):
    tenant_id = _tenant_uuid(user)
    if tenant_id and is_postgres_enabled():
        try:
            from backend.db.bookings import fetch_bookings

            return jsonable_encoder(fetch_bookings(tenant_id, limit=200))
        except Exception as e:
            logger.error("tenant.bookings.fetch_failed", extra={"error_type": type(e).__name__})
            return []
    return []

@app.get("/api/recordings/{recording_id}/playback")
async def api_get_recording_playback(recording_id: str, user: dict = Depends(require_session)):
    tenant_id = _tenant_uuid(user)
    if not tenant_id or not is_postgres_enabled():
        raise HTTPException(status_code=404, detail="Recording not found")
    try:
        from backend.db.recordings import get_recording
        from backend.integrations.storage import S3StorageProvider

        row = get_recording(tenant_id=tenant_id, recording_id=UUID(recording_id))
        if row is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        if row.get("upload_status") != "uploaded":
            raise HTTPException(status_code=409, detail="Recording is not ready")
        storage = S3StorageProvider()
        if not storage.configured:
            raise HTTPException(status_code=503, detail="Recording storage is not configured")
        expires_in = 900
        return {
            "url": storage.generate_signed_url(row["storage_key"], expires_seconds=expires_in),
            "expires_in": expires_in,
        }
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid recording id")
    except Exception as e:
        logger.error("Recording playback URL failed: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Recording playback unavailable")

@app.get("/api/stats")
async def api_get_stats(user: dict = Depends(require_session)):
    tenant_id = _tenant_uuid(user)
    if tenant_id and is_postgres_enabled():
        try:
            from backend.db.call_logs import fetch_call_stats

            return jsonable_encoder(fetch_call_stats(tenant_id))
        except Exception as e:
            logger.error("tenant.stats.fetch_failed", extra={"error_type": type(e).__name__})
            return {"total_calls": 0, "total_bookings": 0, "avg_duration": 0, "booking_rate": 0}
    return {"total_calls": 0, "total_bookings": 0, "avg_duration": 0, "booking_rate": 0}

@app.get("/api/contacts")
async def api_get_contacts(user: dict = Depends(require_session)):
    """CRM endpoint — groups call_logs by phone number, deduplicates into contacts."""
    tenant_id = _tenant_uuid(user)
    if tenant_id and is_postgres_enabled():
        try:
            from backend.db.call_logs import fetch_call_logs

            rows = fetch_call_logs(tenant_id, limit=500)
            contacts: dict = {}
            for row in rows:
                phone = row.get("phone_number") or "unknown"
                if phone not in contacts:
                    contacts[phone] = {
                        "phone_number": phone,
                        "caller_name": "",
                        "total_calls": 0,
                        "last_seen": row.get("created_at"),
                        "is_booked": False,
                    }
                contacts[phone]["total_calls"] += 1
                if row.get("summary") and "Confirmed" in row.get("summary", ""):
                    contacts[phone]["is_booked"] = True
            return jsonable_encoder(sorted(contacts.values(), key=lambda x: x["last_seen"] or "", reverse=True))
        except Exception as e:
            logger.error("tenant.contacts.fetch_failed", extra={"error_type": type(e).__name__})
            return []
    return []



# Prometheus Metrics
try:
    from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
    from fastapi.responses import Response as _Resp

    _voice_calls_total = Counter("voice_calls_total", "Total calls handled by the agent")
    _voice_calls_booked = Counter("voice_calls_booked_total", "Calls that resulted in a booking")
    _voice_call_duration = Histogram(
        "voice_call_duration_seconds",
        "Call duration in seconds",
        buckets=[10, 30, 60, 120, 300, 600, 1200],
    )
    _voice_calls_active = Gauge("voice_calls_active", "Currently active calls")

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request):
        _require_metrics_access(request)
        return _Resp(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/internal/record-call", include_in_schema=False)
    async def record_call_metric(request: Request):
        _require_internal_access(request)
        data = await request.json()
        _voice_calls_total.inc()
        if data.get("booked"):
            _voice_calls_booked.inc()
        if data.get("duration"):
            _voice_call_duration.observe(data["duration"])
        return {"ok": True}

    logger.info("metrics.enabled", extra={"path": "/metrics"})
except ImportError:
    logger.warning("metrics.disabled", extra={"error_type": "ImportError"})


@app.get("/health")
def health_check():
    return aggregate_health(service="rapidx-ai-voice-agent")


@app.get("/")
async def api_root():
    return {"service": "rapidx-ai-api", "status": "ok"}
