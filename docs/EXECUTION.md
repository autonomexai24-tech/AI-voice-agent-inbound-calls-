# EXECUTION.md — Priority-Ordered Fix Plan

> Concrete code targets and ship order. Each item has a **file:line**,
> a clear **why**, an explicit **fix**, and a **verification step**.
> Refers back to `REVIEW.md` risk IDs (e.g. **D-1**).

---

## PRIORITY ORDER

1. **P0 — Auth login broken in production** (D-1, D-4, D-5, D-11)
2. **P0 — Latency: confirm bottleneck via real logs** (D-3, E.2)
3. **P0 — Interruption quality** (D-7, D-8)
4. **P1 — Deployment hardening** (D-18, D-19, D-20)
5. **P1 — Tenant safety validation** (D-11, D-12, D-13)
6. **P2 — Failure handling** (D-14, D-15)
7. **P2 — Observability completion** (B.2 — `_log_transcript`, `upsert_active_call`)
8. **P3 — Cleanup / dead code** (B.3)

---

## P0-A — AUTH DEBUGGING PLAN

### A.1 Confirm what's deployed (commit `3b40147`)

The fastest signal is the diagnostic endpoint added in commit `3b40147`:

```
GET https://<easypanel-host>/api/auth/_diag
```

Expected response:
```json
{
  "build_marker": "phase3a-credentials-fallback-v1",
  "use_postgres": true,
  "fallback_active": {
    "email_from": "code_default" | "env",
    "password_from": "code_default" | "env",
    "slug_from": "code_default" | "env"
  },
  "expected_email": "harshhavanur2005@gmail.com",
  "expected_password_length": 17,
  "expected_password_first2": "Ra",
  "expected_password_last2": "21",
  "expected_slug": "default"
}
```

### A.2 Decision tree based on `/_diag`

| Diag response | Root cause | Fix |
|---|---|---|
| `404 Not Found` | EasyPanel build cache served old code | Force rebuild without cache (EasyPanel → Service → Settings → "Rebuild without cache") |
| `password_from: "env"` AND `expected_password_length != 17` | Stale `DASHBOARD_PASSWORD` env var overriding code default | EasyPanel → Environment → delete `DASHBOARD_PASSWORD` and `ADMIN_PASSWORD` → restart |
| `expected_password_length: 17` AND `_first2: "Ra"` AND login still 401 | Cookie domain mismatch (D-5) | See A.3 |
| `use_postgres: true` AND a real user row exists with mismatched bcrypt | `_postgres_login` returns None, fallback should kick in | Verify with raw curl (A.4) |

### A.3 Fix cookie domain on EasyPanel subdomain (D-5)

`ui_server.py:337-350` does **not** set a cookie domain. On a host like
`dhanushpackaging12.aivoice.ocznup.easypanel.host`, the browser scopes
the cookie to that exact host. If the dashboard is later accessed via
`aivoice.ocznup.easypanel.host` (without `dhanushpackaging12`), the
cookie won't transmit.

**Code change** in `_set_session_cookie`:

```python
def _set_session_cookie(response: Response, token: str) -> None:
    secure = _is_production() or (os.environ.get("SESSION_COOKIE_SECURE") or "").lower() == "true"
    samesite = (os.environ.get("SESSION_COOKIE_SAMESITE") or "lax").lower()
    if samesite not in {"lax", "strict", "none"}:
        samesite = "lax"
    domain = os.environ.get("SESSION_COOKIE_DOMAIN") or None  # ← NEW
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/",
        domain=domain,  # ← NEW
    )
```

Mirror in `_clear_session_cookie`. Set `SESSION_COOKIE_DOMAIN` to the
actual host (or leave unset for default current-host scoping).

### A.4 Direct curl verification

Bypass the frontend entirely:

```bash
curl -i -X POST https://<easypanel-host>/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"harshhavanur2005@gmail.com","password":"RapidX-Voice-7421","tenant_slug":"default"}'
```

| Result | Meaning |
|---|---|
| `200 OK` + `Set-Cookie: rapid_session=...` | Backend works → frontend issue (cookie not stored / domain) |
| `401 {"detail":"Invalid credentials"}` | `_authenticate()` returning None — old code or env override |
| `409 {"detail":"Workspace is required..."}` | Postgres path raised AmbiguousTenantLogin (workspace empty in payload) |
| `404` | Old code still deployed (no `/api/auth/login` route is impossible — would mean different routing) |
| `502/504` | ui_server process dead — check supervisord |

### A.5 Remove `/api/auth/_diag` once login works (D-20)

Diagnostic endpoint leaks `expected_email`. Delete the route after fix
confirmed. Plan: leave it for one more debug cycle, then strip in same
commit as the cookie domain fix.

### A.6 Defense-in-depth: ensure `_env_login` is reachable even on Postgres errors

Currently if `_postgres_login` raises an unexpected exception (not
`AmbiguousTenantLogin`), the exception propagates and `_env_login`
never runs. Looking at `_postgres_login`, the inner `try/except` catches
generic `Exception` so this is unlikely — but make it explicit in
`_authenticate`:

```python
def _authenticate(email: str, password: str, tenant_slug: str | None = None) -> dict | None:
    try:
        result = _postgres_login(email, password, tenant_slug)
        if result is not None:
            return result
    except AmbiguousTenantLogin:
        # bubble up — caller turns this into 409
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("auth.postgres_login_failed", extra={"error_type": type(exc).__name__})
    return _env_login(email, password, tenant_slug)
```

This guarantees env-login runs whenever Postgres is unreachable. Single
small change at `ui_server.py:334`.

---

## P0-B — LATENCY OPTIMIZATION PLAN

### B.1 Phase 0: measure first

The code already logs every stage. **Do not change anything until you
have 5 minutes of real production logs.** Commands in `TESTING.md` §3.

For each turn, expect to see:
```
latency.stt_received        stt_received_at
latency.stt                 duration_ms, audio_duration_ms
latency.llm                 ttft_ms, duration_ms
latency.tts                 ttfb_ms, duration_ms
latency.silence_to_speech_estimate    latency_ms
```

If `silence_to_speech_estimate > 1500ms`, identify which stage dominates.

### B.2 If LLM `ttft_ms > 600ms` → switch to faster path

Two options, both already implemented:

**Option 1: Groq llama-3.3-70b** — sub-300ms TTFT, free.
- In dashboard or `tenant_config`: set `llm_provider="groq"` and
  `llm_model="llama-3.3-70b-versatile"`.
- Already wired at `agent.py:510-515`.
- Trade-off: slightly different reply style; verify booking flow still works.

**Option 2: gpt-4o-mini → gpt-4.1-mini**
- Newer mini model is 30–40% faster TTFT on chat completions.
- Requires no code change; just update `llm_model`.

### B.3 If TTS `ttfb_ms > 350ms` → switch to ElevenLabs Turbo v2.5

- Already implemented at `agent.py:560-568`.
- In `tenant_config`: `tts_provider="elevenlabs"`, `elevenlabs_voice_id=<id>`.
- Trade-off: cost (~$0.30 per 1k chars vs Sarvam free-tier).
- Cuts TTFB to ~150ms.

### B.4 Tune `min_endpointing_delay` (D-8)

Current: `0.05` (50ms) — too aggressive.

Recommended tier-by-tier:
- 0.20s — natural for most Indian-English speakers
- 0.30s — safer for slow-paced Hindi callers
- Test both with real callers; pick whichever has fewer "hello? hello?" reloop events.

Change in dashboard: Settings → STT min endpointing delay.

### B.5 Trim system prompt

Check log line at `agent.py:320`:
```
[PROMPT] System prompt: <N> tokens
```

If `N > 600`:
- Shorten `agent_instructions` in `tenant_config`.
- Drop the IST 7-day table to today + tomorrow only (saves ~80 tokens) — edit `backend/voice/prompts.py:32-39`.
- Drop unused language directive instructions for non-active presets.

### B.6 Cache Cal.com slot lookups (D-15)

`check_availability` blocks the conversation 1–3s while hitting Cal.com.
Cache per `(tenant_id, date)` for 60s in-memory:

Add a small helper in `calendar_tools.py` (or a new `_slot_cache.py`):

```python
import time
_SLOT_CACHE: dict[tuple, tuple[float, list]] = {}
_SLOT_TTL = 60.0

def get_available_slots_cached(date, *, cal_api_key, cal_event_type_id):
    key = (cal_api_key or "default", cal_event_type_id or "default", date)
    now = time.time()
    cached = _SLOT_CACHE.get(key)
    if cached and (now - cached[0]) < _SLOT_TTL:
        return cached[1]
    slots = get_available_slots(date, cal_api_key=cal_api_key, cal_event_type_id=cal_event_type_id)
    _SLOT_CACHE[key] = (now, slots)
    return slots
```

Wire into `agent.py:240` instead of raw `get_available_slots`.

### B.7 Optional: filler word during LLM wait

If LLM TTFT consistently > 500ms, play a brief filler ("एक सेकंड…" or "Let me check…")
after 400ms of silence. LiveKit AgentSession supports this via:

```python
session.say("Let me check that for you.", allow_interruptions=True)
```

Triggered conditionally on a per-tool or per-LLM-wait basis. **Defer
this until B.1–B.5 confirm a real LLM bottleneck.**

---

## P0-C — INTERRUPTION OPTIMIZATION

### C.1 Fix the echo filter (D-7)

`agent.py:801-803` drops user transcripts if `agent_is_speaking==True`.
This is a workaround for echo, but it kills genuine interruptions.

**Better approach**: keep the echo filter, but only drop transcripts
**under N characters** (likely actual echo) and process longer ones
(likely a real user statement):

```python
if agent_is_speaking:
    # Likely echo if very short; pass through if substantive
    if len(transcript) < 8:
        logger.debug("[FILTER-ECHO] Dropped short transcript during agent speech",
                     extra={"call_id": call_id, "len": len(transcript)})
        return
    logger.info("[FILTER-ECHO] Allowing long transcript despite agent_speaking",
                extra={"call_id": call_id, "len": len(transcript)})
    # fall through and process — barge-in
```

Single edit at `agent.py:801-803`.

### C.2 Tune Silero VAD sensitivity

LiveKit's default Silero VAD is conservative. Increase sensitivity for
faster barge-in detection. Current code uses `turn_detection="stt"` which
**bypasses VAD-based turn end** entirely.

Option A: switch to `turn_detection="vad"` with explicit Silero plugin:

```python
from livekit.plugins import silero
session = AgentSession(
    stt=agent_stt,
    llm=agent_llm,
    tts=agent_tts,
    vad=silero.VAD.load(min_silence_duration=0.3, threshold=0.4),
    turn_detection="vad",
    allow_interruptions=True,
)
```

- `min_silence_duration=0.3`: 300ms silence ends a user turn.
- `threshold=0.4`: lower = more sensitive (faster interrupt detect).

Option B: keep `turn_detection="stt"` but add explicit VAD only for
interruption detection. LiveKit's API supports this via the
`InterruptionConfig` (verify against installed `livekit-agents` version).

**Recommendation**: A/B test option A for one day. Roll back via dashboard
config if naturalness degrades.

### C.3 Verify TTS cancellation latency

After interrupting, check the metric:
```
log key: latency.tts          field: cancelled=true
```

If `cancelled=true` events show high `audio_duration_ms` (lots of audio
played after interrupt), the TTS plugin is buffering too deeply. Mitigation:

- For Sarvam Bulbul, no exposed buffer-size knob; switching to
  ElevenLabs Turbo is the main lever.
- Confirm with side-by-side: 5 min Sarvam, 5 min ElevenLabs, compare
  `audio_duration_ms` after interrupt events.

### C.4 Lower `min_endpointing_delay` for interruption (separate from B.4)

These are different settings:
- `min_endpointing_delay` = time of silence before STT considers turn ended.
- VAD `min_silence_duration` = analogous for VAD-based turn detection.

If using `turn_detection="stt"`, set `min_endpointing_delay=0.2` (B.4).
If switching to `turn_detection="vad"`, set VAD `min_silence_duration=0.3`.

---

## P1-A — DEPLOYMENT HARDENING

### D.1 Docker layer cache busting (D-4)

Add a `BUILD_REV` arg that breaks cache on every push:

`Dockerfile`:
```dockerfile
ARG BUILD_REV=unknown
ENV BUILD_REV=${BUILD_REV}
```

Pass via EasyPanel build args: `BUILD_REV=$GITHUB_SHA`.

This forces invalidation of all subsequent layers on every commit. Adds
build time (no cache) but eliminates stale-layer mystery.

### D.2 Healthcheck for the agent process (D-19)

`supervisord` only restarts on process exit. If the agent hangs (e.g.
LiveKit websocket dead-alive), no restart. Add a periodic health probe:

In `agent.py`, expose a tiny `/health` HTTP endpoint on port 8081:

```python
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a, **k): pass

def _start_health(port=8081):
    HTTPServer(("0.0.0.0", port), _Health).serve_forever()

threading.Thread(target=_start_health, daemon=True).start()
```

Then EasyPanel's health probe hits `/health:8081` and restarts container
if probe fails 3× in 30s.

### D.3 Remove diagnostic endpoint (D-20)

After login is fixed (P0-A), remove the `/api/auth/_diag` route
(`ui_server.py:552-579`).

### D.4 Add `BUILD_REV` to `/health` response

Stamps the running container with its commit hash:

```python
@app.get("/health")
def health_check():
    return {
        **aggregate_health(service="rapidx-ai-voice-agent"),
        "build_rev": os.environ.get("BUILD_REV", "unknown"),
    }
```

Now `curl /health` directly tells operator which commit is running. No
diagnostic-endpoint workaround needed.

---

## P1-B — TENANT SAFETY VALIDATION

### E.1 Cross-tenant data leak audit

Every endpoint that returns tenant data should call `_tenant_uuid(session)`
and use it in the SQL query. Audit script:

```bash
grep -n "@app.get\|@app.post" ui_server.py | head -60
# For each: check that handler calls _tenant_uuid and passes to DB query
```

Endpoints to verify:
- `/api/config` GET/POST (`ui_server.py:623, 627`) ✓ uses `require_session`
- `/api/logs` GET (`ui_server.py:635`) ✓
- `/api/logs/<id>/transcript` GET (`ui_server.py:648`) ✓
- `/api/bookings` GET (`ui_server.py:675`) ✓
- `/api/recordings/<id>/playback` GET (`ui_server.py:688`) ✓
- `/api/stats` GET (`ui_server.py:718`) ✓
- `/api/contacts` GET (`ui_server.py:731`) ✓

All current handlers do filter by `tenant_id`. Just keep checking on
every new endpoint.

### E.2 Admin endpoint protection (D-12)

`/api/admin/*` requires `ADMIN_TOKEN`. If env unset, `_require_admin_token`
should refuse all requests. Verify behavior:

```bash
unset ADMIN_TOKEN; curl https://<host>/api/admin/tenants
# Expected: 401 or 503; never 200
```

If it returns 200, fix `_require_admin_token` to fail-closed.

### E.3 Cookie tenant validation freshness (D-13)

When tenant becomes inactive, existing sessions stay valid until
`SESSION_TTL_SECONDS` (12 hours). For high-stakes ops, check active flag
on every request — already done at `_validate_session_tenant`. Acceptable.

---

## P2 — FAILURE HANDLING

### F.1 Cal.com timeout protection (D-15)

Wrap `get_available_slots` in `asyncio.wait_for` to cap at 2s:

```python
try:
    slots = await asyncio.wait_for(
        asyncio.to_thread(get_available_slots, date, ...),
        timeout=2.0,
    )
except asyncio.TimeoutError:
    return "Sorry, I can't reach the calendar right now. Let me transfer you to a person who can help."
```

### F.2 OpenAI/LLM timeout

LiveKit handles LLM streaming timeouts internally. Verify by killing
network mid-call (firewall block on OpenAI for 10s) — observe whether
agent says "I'm having trouble, please try again" or hangs in silence.

### F.3 Postgres pool exhaustion (D-pool)

Default `max=10`. Under 20 concurrent calls all completing simultaneously,
the shutdown hook's call-log insert may queue. Monitor:

```
log key: postgres.operation.slow    duration_ms > 500
```

If frequent, increase `POSTGRES_POOL_MAX=20` and verify pool stats via
`/health`.

---

## P2 — OBSERVABILITY COMPLETION

### G.1 Implement `_log_transcript` (B.2)

Currently `agent.py:692-693` returns immediately. To enable real-time
transcript streaming to the dashboard:

- Insert each user/assistant message into a small in-memory ring buffer
  per `call_id`.
- Expose `GET /api/calls/<id>/transcript-stream` (Server-Sent Events) in
  `ui_server.py`.
- Frontend dashboard subscribes during active calls.

**Defer to P3.** Not blocking production.

### G.2 Implement `upsert_active_call` (B.2)

Tracks live calls. Can show live-call counter on dashboard.

**Defer to P3.** Not blocking.

---

## P3 — CLEANUP / DEAD CODE

### H.1 Remove `before_tts_cb` (B.3)

Dead since rollout (`agent.py:587-589`). Remove or actually wire it into
`AgentSession`. If LiveKit auto-chunks streaming TTS, just delete it.

### H.2 Strip dev log files

`ui_server.log`, `runtime-backend.*.log`, `runtime-frontend*.log` are
local dev leftovers. Add to `.gitignore` and `git rm`.

### H.3 Move `test_*.py` scripts

Either convert to `pytest` tests under `tests/`, or move to
`scripts/dev_smoke/` and add a README.

### H.4 Document config priority

Add a short comment to `backend/core/config_resolver.py` linking to
`REVIEW.md §A.5` so future readers understand priority order.

---

## SHIP ORDER (concrete commits)

| # | Commit | Files | Verifies |
|---|---|---|---|
| 1 | **fix(auth): add cookie domain support + safer authenticate fallback** | `ui_server.py:337-349, 334` | A.3, A.6 |
| 2 | **chore(deploy): add BUILD_REV to /health** | `Dockerfile`, `ui_server.py:797` | D.4 |
| 3 | **fix(voice): allow long transcripts during agent speech** | `agent.py:801-803` | C.1 |
| 4 | **perf(voice): bump min_endpointing_delay default to 0.2s** | `backend/core/config_resolver.py:32`, `config.json`, ui_server defaults | B.4 |
| 5 | **fix(deploy): force layer rebust via BUILD_REV arg** | `Dockerfile` | D.1 |
| 6 | **chore(auth): remove /api/auth/_diag** | `ui_server.py:552-579` | D.3 |
| 7 | **perf(tools): cache Cal.com slot lookups** | `calendar_tools.py`, `agent.py:240` | B.6 |
| 8 | **feat(voice): VAD-based turn detection (opt-in via env)** | `agent.py:619-626` | C.2 |
| 9 | **chore: add agent process /health endpoint** | `agent.py` | D.2 |
| 10 | **chore: gitignore + cleanup dev logs** | `.gitignore`, log files | H.2 |

Each commit ≤ 100 lines change. Roll back any single commit independently.

---

## STOP CONDITIONS

Pause execution and re-plan if:
- Any commit causes production call latency to **increase** by ≥150ms.
- Any commit breaks an inbound call (call doesn't answer or audio drops).
- Postgres pool exhaustion observed during normal load (1–3 concurrent calls).

---

*EXECUTION.md complete. Next: TESTING.md provides verification commands for every fix above.*
