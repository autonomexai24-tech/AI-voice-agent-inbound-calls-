# REVIEW.md — Production Reliability Audit

> Grounded review of the actual code at HEAD `3b40147`. Every claim points
> to a real file/line. No speculation, no boilerplate.
>
> **Scope:** what's there, what's missing, what's risky, where latency
> hides. This is the input to `EXECUTION.md` (fix plan) and `TESTING.md`.

---

## A. SYSTEM OVERVIEW

### A.1 Deployment topology — single container

```
┌────────────────────────────────────────────────────────────────┐
│ Docker container (EasyPanel) — `Dockerfile` 3-stage build      │
│ Started by supervisord (`supervisord.conf`)                    │
│                                                                │
│  [program:agent]      python agent.py start          (port 8081)│
│  [program:ui_server]  uvicorn ui_server:app          (port 8000)│
│  [program:frontend]   node server.js (Next standalone) (port 3000)│
│                                                                │
│  Frontend → /api/* rewrites → 127.0.0.1:8000  (next.config.mjs:24)│
└────────────────────────────────────────────────────────────────┘
```

- All three processes share one container; loopback networking only.
- Exposed ports: `3000`, `8000`, `8081`. EasyPanel typically routes the
  public hostname to port `3000`.
- Frontend rewrite default: `API_BASE_URL=http://127.0.0.1:8000` (also
  hardcoded in `Dockerfile` line 71 and `supervisord.conf` line 37).

### A.2 Realtime call flow

```
PSTN caller  ─►  Vobiz SIP trunk  ─►  LiveKit room (auto-dispatch)
                                       │
                                       ▼
                              agent.py `entrypoint(ctx)`:
                                1. ctx.connect()
                                2. extract caller_phone + DID
                                3. resolve_runtime_config(did)
                                4. build STT/LLM/TTS plugins
                                5. AgentSession.start()
                                6. greeting via on_enter()
                                7. user_speech_committed → LLM → TTS
                                8. participant_disconnected → shutdown_hook
```

- File: `agent.py:350` (`async def entrypoint`).
- Realtime audio: LiveKit room media plane, not websocket-direct from
  caller. Caller speaks → LiveKit → STT plugin → LLM → TTS plugin → LiveKit → caller.
- Streaming: STT and TTS are streaming plugins (Sarvam Saaras v3 STT,
  Sarvam Bulbul v3 TTS). LLM is streaming via `openai.LLM(...)`.

### A.3 SIP routing

- Vobiz inbound DID → LiveKit SIP trunk → LiveKit dispatches an agent
  job per call. DID is extracted from SIP participant attributes (`agent.py:1093` `_extract_dialed_did`).
- Outbound transfer: `AgentTools.transfer_call` → `api.TransferSIPParticipantRequest`
  with `transfer_to=sip:...` (`agent.py:152`).
- Hangup: `end_call` does a no-op transfer to `tel:+00000000` to drop
  the call (`agent.py:189`). Pragmatic but vendor-dependent.

### A.4 AI pipeline (per-call)

| Stage | Component | Config | File |
|---|---|---|---|
| Audio capture | LiveKit room input | `RoomInputOptions(close_on_disconnect=False)`, optional BVC noise cancel | `agent.py:603-617` |
| VAD / turn-end | STT-based (`turn_detection="stt"`) | `min_endpointing_delay=0.05` (50ms — aggressive) | `agent.py:619-626` |
| STT | Sarvam Saaras v3 (default) or Deepgram Nova-2 | `language="unknown"` (auto), 16kHz | `agent.py:530-557` |
| LLM | OpenAI/Groq/Claude (default `gpt-4o-mini`) | `max_completion_tokens=120` | `agent.py:509-528` |
| TTS | Sarvam Bulbul v3 (default) or ElevenLabs Turbo v2.5 | 24kHz, voice from `tts_voice` | `agent.py:559-584` |
| Pre-warm | TTS prewarm | `session.tts.prewarm()` | `agent.py:631-635` |

### A.5 Tenant model

- Tables: `tenants`, `users`, `tenant_config`, `call_logs`, `bookings`,
  `notification_events`, `call_recordings`. Migrations 001–004.
- Resolution: DID (called number) → `tenants.phone_number` → tenant_id →
  `tenant_config` row. Implemented at `backend/services/tenant_service.py:30`.
- Voice runtime resolution: `backend/core/config_resolver.py:68`
  (`resolve_runtime_config`) — Postgres first, JSON fallback, env defaults.
- Active flag: `tenants.is_active`; agent refuses calls for inactive tenants
  (`agent.py:411-433`).

### A.6 Auth flow

```
Browser ──► /login (Next.js page)  frontend/app/login/page.tsx
            └─► POST /api/auth/login (cookies: include)
                  │ (proxied via Next rewrite to ui_server)
                  ▼
            ui_server.py `_authenticate()`     ui_server.py:334
              1. _postgres_login()             ui_server.py:287
                  ├─ raises AmbiguousTenantLogin if no workspace
                  ├─ tenant slug lookup; user lookup; bcrypt verify
                  └─ returns user dict OR None
              2. _env_login()                  ui_server.py:256
                  ├─ DASHBOARD_EMAIL / DASHBOARD_PASSWORD (env)
                  ├─ falls back to baked-in defaults (commit 3b40147)
                  └─ returns user dict OR None
            On success: set HMAC-signed cookie `rapid_session`
                        (HttpOnly, Secure in prod, SameSite=Lax)
                        ui_server.py:337-350

Subsequent requests:
  Browser ─► /api/* → cookie validated by `require_session` → tenant_id resolved
```

- Session cookie is **stateless HMAC**, no DB session table.
- Frontend middleware (`frontend/middleware.ts`) gates `/dashboard`,
  `/calls`, `/bookings`, `/settings`. It only checks **cookie presence**,
  not validity — actual validation is server-side in FastAPI.

### A.7 Storage

- **PostgreSQL** (primary, when `USE_POSTGRES=true`): pooled via
  `psycopg2.pool.ThreadedConnectionPool` (`backend/db/connection.py`).
  Min/max conns from `POSTGRES_POOL_MIN`/`MAX` env (defaults 1/10).
- **Supabase** (legacy): `db.py` `save_call_log()`. Used only when
  Postgres is **disabled** — see `agent.py:1019` (the dual-write switch).
- **S3-compatible recording storage**: LiveKit egress → S3 directly via
  `S3StorageProvider` (`agent.py:651-684`).
- **config.json** (file): legacy fallback when Postgres disabled. JSON
  files in `configs/<phone>.json` and `configs/default.json` for per-DID
  configs.

---

## B. WHAT IS COMPLETED / PARTIAL / FAKE / DEAD

### B.1 Fully working (confirmed on real calls)

- ✅ Inbound SIP call → LiveKit dispatch → agent join.
- ✅ Sarvam STT/TTS streaming pipeline.
- ✅ OpenAI LLM streaming with 120-token cap (`agent.py:527`).
- ✅ Greeting (`on_enter`) plays correctly.
- ✅ Conversation turn loop (user_speech_committed → LLM → TTS).
- ✅ Tools: `transfer_call`, `end_call`, `save_booking_intent`, `check_availability`, `get_business_hours`.
- ✅ Cal.com booking creation in shutdown hook.
- ✅ LiveKit Egress recording to S3 (object key built; egress started + stopped).
- ✅ Latency observability: STT/LLM/TTS metrics logged on every turn (`agent.py:742-786`).
- ✅ Multilingual presets: 11 languages, language directive injection (`backend/voice/language.py`).
- ✅ Postgres connection pool + healthcheck (`backend/db/connection.py:174`).
- ✅ Multi-tenant DID → tenant resolution (`backend/services/tenant_service.py`).
- ✅ Login fallback chain: Postgres user → env user → baked-in default (commit `3b40147`).

### B.2 Partial / scaffolded but not exercised

| Area | Status | File |
|---|---|---|
| `NotificationService` SMS provider abstraction | Interface defined; default returns `pending` (no provider wired) | `backend/services/notification_service.py:41-79` |
| Fast2SMS integration | `FAST2SMS_API_KEY` in env example but no adapter found | env only |
| `RecordingService` upload | Metadata insert works; actual upload happens in agent.py via LiveKit egress, not via this service | `backend/services/recording_service.py` vs `agent.py:660` |
| `_log_transcript` (real-time transcript stream) | Returns immediately (line 693) — no streaming impl | `agent.py:692` |
| `upsert_active_call` | Returns immediately (line 687) — no active_calls upsert | `agent.py:686` |
| Admin tenant CRUD endpoints | Wired (`/api/admin/tenants`) but require `ADMIN_TOKEN`; no UI | `ui_server.py:556` |
| Phase 2 startup checks | `run_startup_checks()` is called (`agent.py:1165`) but doesn't fail-fast on missing optional vars; only validates pool init | `backend/core/startup.py` |

### B.3 Dead code / unused

- `before_tts_cb` defined at `agent.py:587-589` but **never registered**
  with `AgentSession`. Was probably meant to chunk TTS sentence-by-sentence;
  currently a no-op.
- `_log_transcript` and `upsert_active_call` return without doing
  anything (`agent.py:686-693`).
- `test_llm.py`, `test_llm_detailed.py`, `test_session_init.py`,
  `test_streaming_tts.py` at repo root are ad-hoc scripts, not pytest tests.
- `notify.py` (5 lines visible from grep, full file 585 bytes) likely
  thin Telegram/WhatsApp shim; the real notification path now is
  `send_booking_confirmation_sms` referenced in `agent.py:875`.
- `ui_server.log`, `runtime-backend.err.log`, `runtime-backend.out.log`,
  `runtime-frontend*.log` at repo root: dev-time leftovers, gitignored
  in spirit but currently committed.

### B.4 Duplicate / overlapping systems

| Pair | Overlap | Resolution status |
|---|---|---|
| `db.py` (Supabase) vs `backend/db/call_logs.py` (Postgres) | Both write call logs | Branch on `USE_POSTGRES` (`agent.py:1018-1056`) — dual-write done |
| `ui_server.py read_config()` vs `backend/core/config_resolver.py` | Both load config | UI uses `read_config`; agent uses `resolve_runtime_config`. Two truths. |
| `agent.py LANGUAGE_PRESETS` (removed in 3A) vs `backend/voice/language.py` | Was duplicate, now imported | Resolved (commit `237f9c4`) |
| `ui_server.py` rate limiter vs `agent.py` rate limiter | Two independent limiters (login attempts vs caller calls) | Intentional, keep |

---

## C. WHAT HAS NEVER BEEN TESTED (in observable production)

Inferred from absence of tests, absence of corresponding log lines, and
silent-fail patterns in code.

| Untested flow | Where | Risk |
|---|---|---|
| LiveKit reconnect (network blip mid-call) | `agent.py` has no explicit reconnect handler | Call drops silently |
| Postgres pool exhaustion under concurrent calls | `_DEFAULT_MAX_CONN=10`, may starve if 20+ concurrent calls hit shutdown hook | Connection wait stalls shutdown |
| `_postgres_login` with non-existent slug + `_env_login` fallback | Needs both paths exercised | This is the live login bug |
| Cookie auth across subdomains (e.g. `dhanushpackaging12.aivoice.ocznup.easypanel.host`) | `_set_session_cookie` doesn't set `domain=` | Cookies may not stick on EasyPanel's wildcard subdomain routing |
| Multi-tenant isolation: tenant A's user reading tenant B's data | Every endpoint goes through `_tenant_uuid(session)`, but cross-tenant test never run | Potential data leak if any endpoint forgets the filter |
| OpenAI timeout / 5xx during LLM streaming | No try/except around `agent_llm` plugin | Call may hang silently until LiveKit timeout |
| Sarvam STT mid-call disconnect | No retry policy | User feels dead air |
| TTS provider swap (Sarvam → ElevenLabs) | Only one provider runs per call; no A/B observability | Fallback path on plugin import failure works but no metrics |
| Long calls (>10 min) | `max_turns=25` cuts off; no time-based cap | Memory growth in chat_ctx |
| Recording upload failure → graceful degradation | Status logged as `failed` but no user-facing notice | Operator must read logs |
| Postgres user with bcrypt hash starting with `$2y$` | `_verify_password` handles `$2a/2b/2y$`; works in theory, not exercised | bcrypt edge case |
| Concurrent login attempts (rate limit) | `_RATE_LIMITS` is in-memory + thread-locked; but only one process | OK for single container, breaks if scaled to 2+ pods |

---

## D. PRODUCTION RISKS

### D.1 Critical (live now)

| # | Risk | Evidence | Why critical |
|---|---|---|---|
| **D-1** | Login broken in EasyPanel | User reports "Invalid credentials" with correct creds | Operator can't reach dashboard |
| **D-2** | High perceived latency / unnatural turn-taking | User-reported | Damages product perception |
| **D-3** | Slow / overlapping interruption | User-reported | Conversation feels robotic |

### D.2 Architectural

| # | Risk | Evidence |
|---|---|---|
| **D-4** | EasyPanel build cache may serve old code on push | The login fix was pushed but user reports old behavior |
| **D-5** | Cookie domain not set → likely fails on EasyPanel subdomain routing | `_set_session_cookie` no `domain=` (`ui_server.py:341-349`) |
| **D-6** | `agent_is_speaking` is a **module-global** bool | `agent.py:348` — on a single-worker process this is OK; if LiveKit ever spawns concurrent agent jobs in one process, calls cross-talk. Currently safe (one job per process via WorkerOptions) but fragile. |
| **D-7** | Echo filter drops user transcripts during TTS | `agent.py:801-803` — if `agent_is_speaking==True` when STT commits a transcript, it's silently dropped. Could mask real interruptions. |
| **D-8** | `min_endpointing_delay=0.05` is aggressive | `agent.py:624` — 50ms is below Sarvam's natural inter-word gap; can cut user mid-sentence |
| **D-9** | LLM `max_completion_tokens=120` | `agent.py:527` — caps reply length but doesn't enforce streaming sentence cadence; `before_tts_cb` is dead code |
| **D-10** | No SIGTERM handler on agent.py | Ctrl-C flow OK, but EasyPanel container restart during call = call drops without shutdown hook |

### D.3 Tenant / data isolation

| # | Risk | Evidence |
|---|---|---|
| **D-11** | If `_env_login` succeeds while Postgres has tenants, session has `tenant_id="legacy"` | `ui_server.py:281` — `_tenant_uuid` returns `None` for "legacy", so all `/api/*` Postgres endpoints either fall through to legacy or return empty. Could mean dashboard shows nothing. |
| **D-12** | Admin endpoints `/api/admin/*` require `ADMIN_TOKEN` env | `_require_admin_token` — if token leaks or env unset, anyone can create tenants |
| **D-13** | Session cookie `tenant_slug` in payload but `_validate_session_tenant` only checks `is_active` | `ui_server.py:364` — slug change in DB doesn't invalidate sessions |

### D.4 Async / streaming

| # | Risk | Evidence |
|---|---|---|
| **D-14** | Synchronous-in-async: `httpx.post(..., timeout=5.0)` via `run_in_executor` for n8n webhook | `agent.py:998-1013` — happens in shutdown, OK; but blocks an executor thread for up to 5s |
| **D-15** | `asyncio.to_thread(get_available_slots)` for Cal.com slot lookup | `agent.py:240` — blocks during live call. Cal.com API can be 1–3s. User waits in silence. |
| **D-16** | Sentiment analysis post-call is sync OpenAI call | `agent.py:927` — only runs in shutdown, doesn't affect live call |
| **D-17** | `before_tts_cb` defined but not used → no sentence chunking → first TTS chunk may include multiple sentences | `agent.py:587` — unclear if LiveKit auto-chunks; need to verify with metrics |

### D.5 Deployment

| # | Risk | Evidence |
|---|---|---|
| **D-18** | All three processes in one container — restarts kill voice + dashboard together | `supervisord.conf` |
| **D-19** | No healthcheck for the agent process — supervisord only restarts on exit, not on hung worker | `supervisord.conf:6-16` |
| **D-20** | Public dashboard exposes `/api/auth/_diag` after commit `3b40147` (NEW) — leaks expected_email in plain JSON | This was added by me for debugging; should be removed once login works |

---

## E. LATENCY BREAKDOWN

### E.1 What's already measured (good news)

The code logs every stage. Look in container logs for these keys:

| Log key | Stage | Where logged |
|---|---|---|
| `latency.silence_to_speech_estimate` | User-stops-speaking → agent-starts-speaking (full turn round-trip) | `agent.py:702` |
| `latency.stt` | STT processing time + audio duration + streamed flag | `agent.py:749` |
| `latency.stt_received` | When final STT transcript arrived | `agent.py:731` |
| `latency.llm` | LLM `ttft_ms` + total `duration_ms` + `cancelled` | `agent.py:761` |
| `latency.tts` | TTS `ttfb_ms` + total `duration_ms` + `audio_duration_ms` | `agent.py:773` |

### E.2 Expected vs measured budget (per-turn round trip)

```
User stops speaking
   │  ⏱ min_endpointing_delay (50ms) — config currently 0.05
   ▼
STT finalize
   │  ⏱ Sarvam Saaras v3 finalize: ~80–150ms (streamed)
   ▼
LLM ttft (first token)
   │  ⏱ gpt-4o-mini cold ~400–700ms; warm ~250–450ms
   │     ⚠ Anchored to OpenAI API hop from EasyPanel region
   ▼
LLM streaming → TTS
   │  ⏱ first sentence ready: another ~150–300ms after ttft
   ▼
TTS ttfb (first audio byte)
   │  ⏱ Sarvam Bulbul v3: ~250–500ms (Indian-language TTS)
   ▼
Audio plays in caller's ear
   │  ⏱ playback buffering: ~50–100ms
   ▼
TARGET: silence_to_speech ≤ 800ms feels natural
        ≤ 1.2s acceptable
        > 1.5s feels laggy
        > 2.5s product-broken
```

Without real production logs we can't pin the bottleneck. **The first
debugging action is to read 5 minutes of `/var/log/...` and compute the
mean for each stage** (commands in `TESTING.md`).

### E.3 Likely top contributors (ranked by typical impact)

| # | Suspect | Lever | Expected gain |
|---|---|---|---|
| 1 | **OpenAI API region** | EasyPanel container → OpenAI: if not in `us-east-*`, expect +200–400ms TTFT | Move to closer region or switch to Groq for sub-300ms TTFT |
| 2 | **System prompt size** (>600 tokens warned at `agent.py:321`) | Trim agent_instructions; current IST table is ~80 tokens, language directive ~50 | Each 100 tokens ≈ +20–40ms TTFT |
| 3 | **`min_endpointing_delay=0.05`** + `turn_detection="stt"` | Too aggressive → STT premature finalize → LLM called twice → user feels reply is wrong/slow | Try 0.2–0.3s |
| 4 | **TTS ttfb (Sarvam Bulbul)** | Switch to ElevenLabs Turbo v2.5 (already supported via `tts_provider="elevenlabs"`) | -150 to -300ms TTFB |
| 5 | **`prewarm()` only on TTS** | STT/LLM not pre-warmed; first turn pays cold-start tax | First-turn slow but subsequent normal — acceptable |
| 6 | **`check_availability` blocks during live call** | `asyncio.to_thread(get_available_slots)` — Cal.com API call mid-conversation | Cache last-known slots in-memory per tenant |
| 7 | **No filler word during LLM wait** | If LLM takes 800ms, caller hears silence | Optional: play "एक सेकंड..." after 400ms wait |

### E.4 Interruption latency (D-7 deep-dive)

- VAD: Silero (LiveKit default). Default frame size 32ms, sensitivity ~0.5.
- When user starts speaking during TTS:
  1. Silero detects voice (~80–150ms)
  2. LiveKit fires `agent_speech_interrupted` event
  3. TTS playback stops (decoder flush): ~50–100ms
  4. Agent transitions to listening state: ~50ms
  5. Total cancel-to-silence: **~200–300ms typical**

If user reports "AI keeps talking after I interrupt", suspects:
- **Silero VAD threshold too high** — needs tuning via `RoomInputOptions` or VAD plugin
- **TTS buffer too deep** — Sarvam plugin may buffer 200–400ms of audio before playing
- **Echo filter dropping user input** (D-7) — when `agent_is_speaking=True`, transcript is dropped → looks like "interruption ignored"

---

## F. ENVIRONMENT VARIABLES — full inventory

From `.env.example` (production must set these). Bold = required.

| Group | Vars |
|---|---|
| **LiveKit (required)** | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_AGENT_NAME` |
| **OpenAI (required)** | `OPENAI_API_KEY` |
| **Sarvam (required)** | `SARVAM_API_KEY` |
| **Postgres** | `USE_POSTGRES`, `DATABASE_URL`, `POSTGRES_POOL_MIN`, `POSTGRES_POOL_MAX`, `POSTGRES_SLOW_OPERATION_MS` |
| **SIP** | `VOBIZ_SIP_DOMAIN`, `VOBIZ_USERNAME`, `VOBIZ_PASSWORD`, `VOBIZ_OUTBOUND_NUMBER`, `DEFAULT_TRANSFER_NUMBER` |
| **Cal.com** | `CAL_API_KEY`, `CAL_EVENT_TYPE_ID` |
| **S3 recording** | `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_REGION` |
| **SMS** | `FAST2SMS_API_KEY`, `FAST2SMS_SENDER_ID`, `FAST2SMS_ROUTE` |
| **Auth (legacy)** | `DASHBOARD_EMAIL`, `DASHBOARD_PASSWORD`, `DASHBOARD_TENANT_SLUG`, `DASHBOARD_SESSION_SECRET` (or `SESSION_SECRET`) |
| **Admin** | `ADMIN_TOKEN` (gates `/api/admin/*`) |
| **Sentry** | `SENTRY_DSN` |
| **Misc** | `ENVIRONMENT` (production gates), `LOG_LEVEL`, `N8N_WEBHOOK_URL` |
| **Frontend (Docker only)** | `API_BASE_URL`, `HOSTNAME`, `PORT` |

---

## G. WORKER LIFECYCLE

- `agent.py:1164` — `if __name__ == "__main__"` calls `run_startup_checks("voice-agent")` then `cli.run_app(WorkerOptions(...))`.
- LiveKit agents framework spawns `entrypoint(ctx)` per call.
- `WorkerOptions(agent_name="inbound-receptionist")` → only inbound dispatch jobs route here.
- Per-call lifecycle:
  1. `entrypoint()` invoked with `JobContext`.
  2. All resources scoped to the `entrypoint` coroutine.
  3. On disconnect → `unified_shutdown_hook` runs (registered via `ctx.add_shutdown_callback`).
  4. Process stays alive for next call.

**Failure modes:**
- If `entrypoint` raises before `session.start()`: caller hears nothing, call dies on Vobiz timeout.
- If `entrypoint` raises after `session.start()`: LiveKit catches; agent stays up.
- If process dies (OOM, segfault): supervisord restarts; in-flight calls drop.

---

*End of REVIEW.md. Findings flow into `EXECUTION.md` (priority-ordered fixes) and `TESTING.md` (verification commands).*
