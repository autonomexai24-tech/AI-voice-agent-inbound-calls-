# PLAN.md — Execution Roadmap

> Migration strategy from prototype to production architecture.
> References: `PROJECT.md` (vision), `ARCHITECTURE.md` (target design), `RULES.md` (constraints), `WORKFLOW.md` (flows), `CORRECTION.md` (current audit).

---

## Guiding Principles

1. **Voice pipeline is sacred** — the live inbound call path is never broken at any phase
2. **Incremental migration** — thin slices, adapter layers, feature flags; no big-bang rewrites
3. **Validate after every phase** — Docker build, inbound call, booking, latency, EasyPanel deploy
4. **One concern per change** — each phase solves one architectural problem
5. **Fastest path to first production dental clinic** — sequence optimizes for earliest viable deployment

---

## What Must NOT Be Rewritten Immediately

These systems work today and must remain functional throughout migration:

| System | Location | Why Preserve |
|---|---|---|
| LiveKit Agents voice session | `agent.py` | Core product — streaming STT/LLM/TTS pipeline |
| Sarvam STT/TTS streaming | `agent.py` | Latency-critical, working correctly |
| Silero VAD + barge-in | `agent.py` | Interruption handling is hard to get right |
| OpenAI tool calling | `agent.py` `AgentTools` | Booking, transfer, end_call all work |
| LANGUAGE_PRESETS (10 languages) | `agent.py` | Multilingual auto-detect works |
| Cal.com async booking | `calendar_tools.py` | Slot check + booking creation works |
| Supervisor + Docker build | `Dockerfile`, `supervisord.conf` | Deployment works on EasyPanel |
| Sentry error tracking | `agent.py` | Basic error capture exists |

**Rule:** Voice logic is refactored (moved, split) but never rewritten from scratch.

---

## Phase Overview

| # | Phase | Dependency | Estimated Effort |
|---|---|---|---|
| 0 | Documentation stabilization | None | Done |
| 1 | PostgreSQL migration | Phase 0 | Medium |
| 2 | Backend modularization | Phase 1 | Medium |
| 3 | Authentication system | Phase 1 | Small |
| 3.5 | Operator validation | Phase 3 | Required gate |
| 4 | Next.js frontend | Phase 3.5 passed | Large |
| 5 | SMS integration | Phase 1 | Small |
| 6 | Recording pipeline | Phase 1 | Medium |
| 7 | Multi-tenant rollout | Phases 1–6 | Large |
| 8 | Production hardening | Phase 7 | Medium |

Phases 3, 5, 6 can be parallelized after Phase 2 is complete. Phase 4 is blocked until Phase 3.5 passes in real staging.

---

## Phase 0 — Documentation Stabilization

**Status: COMPLETE**

- `PROJECT.md` — product vision, architecture principles
- `ARCHITECTURE.md` — target system design (13 sections)
- `RULES.md` — hard constraints (10 sections)
- `WORKFLOW.md` — call flows, business logic (10 sections)
- `CORRECTION.md` — codebase audit, refactor targets, migration order

---

## Phase 1 — PostgreSQL Migration

### Goal

Replace Supabase SDK with direct PostgreSQL via psycopg2 connection pool. This is the foundation for every subsequent phase.

### Strategy

Use an **adapter layer**: create new `backend/db/` module with psycopg2, then rewire `agent.py` and `ui_server.py` to call the new module instead of `db.py`. Old `db.py` (Supabase) remains until all callers are migrated.

### Files Touched

| Action | File |
|---|---|
| Create | `backend/db/connection.py` — psycopg2 pool setup |
| Create | `backend/db/call_logs.py` — raw SQL CRUD for call_logs |
| Create | `backend/db/bookings.py` — raw SQL CRUD for bookings |
| Create | `migrations/001_initial.sql` — full schema from ARCHITECTURE.md §4 |
| Modify | `agent.py` — import from `backend.db` instead of `db.py` |
| Modify | `ui_server.py` — import from `backend.db` for read endpoints |
| Modify | `requirements.txt` — add `psycopg2-binary`, remove Supabase packages |
| Modify | `Dockerfile` — ensure `libpq-dev` available |
| Preserve | `db.py` — keep as fallback until migration verified |

### Schema

Deploy the simplified inbound schema:
- `tenants` only
- DID lookup uses normalized `phone_number`
- Prompt, greeting, language, and voice live directly on the tenant row

### Risks

| Risk | Mitigation |
|---|---|
| Connection pool exhaustion | Default pool size 5, max 10; monitor with health check |
| Schema mismatch breaks agent | Adapter functions match existing `save_call_log()` signatures |
| Supabase removal breaks something missed | Keep `db.py` as dead code until Phase 2 cleanup |
| DATABASE_URL not set in EasyPanel | Add to env vars before deploy; health check verifies DB connection |

### Success Criteria

- [ ] `migrations/001_initial.sql` runs cleanly on fresh PostgreSQL
- [ ] `backend/db/call_logs.py` passes write + read for call logs
- [ ] `backend/db/bookings.py` passes write + read for bookings
- [ ] Agent completes inbound call → call log appears in PostgreSQL (not Supabase)
- [ ] Docker build succeeds with psycopg2
- [ ] Latency unchanged (< 1.5s silence-to-speech)

### Validation Checkpoint

```
1. docker build -t ai-receptionist .
2. docker run with DATABASE_URL pointing to test PostgreSQL
3. Make inbound test call via Vobiz
4. Verify call log in PostgreSQL
5. Verify booking in PostgreSQL (if booking made)
6. Measure latency — must be < 1.5s
```

---

## Phase 2 — Backend Modularization

### Goal

Split monolith files into modular folder structure per `ARCHITECTURE.md` §13. Voice logic moves but is not rewritten.

### Strategy

**Move and rewire, do not rewrite.** Extract functions from `agent.py` and `ui_server.py` into the target folder structure. Use Python imports to keep everything wired. Supervisor commands update to point to new module paths.

### Files Touched

| Action | File |
|---|---|
| Create | `backend/voice/agent.py` — extracted from root `agent.py` |
| Create | `backend/voice/tools.py` — `AgentTools` class extracted |
| Create | `backend/voice/languages.py` — `LANGUAGE_PRESETS` + `get_language_instruction()` |
| Create | `backend/voice/config.py` — tenant config loader (reads from PostgreSQL) |
| Create | `backend/api/app.py` — thin FastAPI, imports routes |
| Create | `backend/api/routes/` — config, calls, bookings, health, metrics |
| Create | `backend/integrations/calendar.py` — Cal.com only (extracted from `calendar_tools.py`) |
| Modify | `supervisord.conf` — update commands to new module paths |
| Modify | `Dockerfile` — update COPY paths, entry points |
| Remove | Outbound endpoints from API (mark as dead code first) |

### Refactor Rules

- **`agent.py` → `backend/voice/`**: Move tool definitions to `tools.py`, language presets to `languages.py`, config loading to `config.py`. The session/pipeline code stays in `agent.py`.
- **`ui_server.py` → `backend/api/`**: Strip all HTML. Keep only JSON API endpoints. Remove outbound call dispatch.
- **`calendar_tools.py` → `backend/integrations/calendar.py`**: Remove Google Calendar code. Keep Cal.com only.
- **`notify.py`**: Leave as-is for now (Phase 5 replaces it).

### Structured Logging (introduce here)

Replace `print()` and basic `logging` with JSON-structured logger:
- Add `backend/logging.py` — configured JSON formatter
- Every log line includes `timestamp`, `level`, `service`
- `call_id` and `tenant_id` added as context where available

### Risks

| Risk | Mitigation |
|---|---|
| Import path changes break agent | Run inbound call test after every file move |
| Supervisor can't find new entry points | Test `supervisord.conf` in local Docker before deploy |
| Circular imports | Keep dependency direction: voice → db, api → db, voice does NOT import api |
| Latency regression from refactor | Measure after every move; revert if > 1.5s |

### Success Criteria

- [ ] `backend/voice/agent.py` starts and handles inbound calls
- [ ] `backend/api/app.py` serves `/health`, `/api/config`, `/api/calls`
- [ ] All `print()` replaced with structured JSON logger
- [ ] Supervisor starts all 3 processes from new paths
- [ ] Docker build succeeds
- [ ] Inbound call → booking → call log — full flow works
- [ ] Latency unchanged

### Validation Checkpoint

```
1. docker build
2. supervisord starts voice agent + API + frontend (placeholder)
3. GET /health returns 200
4. Inbound test call completes with booking
5. GET /api/calls returns call log (from PostgreSQL)
6. Latency < 1.5s
```

---

## Phase 3 — Authentication System

### Goal

Replace unauthenticated API with tenant-based email/password login per `ARCHITECTURE.md` §5.

### Strategy

Add auth middleware to FastAPI. Create `users` table seed. Add `/api/auth/login` and `/api/auth/logout`. Protect all data endpoints with session cookie validation.

### Files Touched

| Action | File |
|---|---|
| Create | `backend/api/routes/auth.py` — login, logout endpoints |
| Create | `backend/api/middleware/auth.py` — session cookie verification, tenant_id extraction |
| Create | `backend/db/users.py` — user CRUD (bcrypt hash verification) |
| Modify | `backend/api/app.py` — register auth middleware |
| Modify | All API routes — require authenticated session |
| Preserve | `/health` — remains unauthenticated |

### Risks

| Risk | Mitigation |
|---|---|
| Locked out of dashboard during migration | Create seed user in migration script; test login before protecting endpoints |
| Voice agent calls internal API | Voice agent does NOT call API; it reads DB directly — no auth issue |
| Cookie misconfiguration | Test in Docker (same-origin localhost); set Secure flag only in production |

### Success Criteria

- [ ] `POST /api/auth/login` returns session cookie with tenant_id
- [ ] `GET /api/config` without cookie returns 401
- [ ] `GET /api/config` with valid cookie returns tenant-scoped data
- [ ] `/health` remains accessible without auth
- [ ] Voice pipeline unaffected (doesn't use API auth)

---

## Phase 3.5 — Operator Validation

### Goal

Prove the current migrated runtime works in real deployment and telephony conditions before any frontend implementation begins.

### Scope

This phase includes only:

- EasyPanel deployment validation
- Real inbound call testing
- Kannada validation
- Marathi validation
- Multilingual switching validation
- Interruption testing
- Transfer testing
- Booking validation
- PostgreSQL coexistence validation
- Graceful degradation validation
- Latency validation
- Structured logging validation

### Gate

Frontend Phase 4 is blocked until Phase 3.5 passes.

### Success Criteria

- [ ] EasyPanel deployment builds and starts under Supervisor
- [ ] Real inbound calls answer through Vobiz and LiveKit
- [ ] Kannada and Marathi calls are usable with acceptable latency
- [ ] Multilingual switching works in a real call
- [ ] Interruptions are responsive
- [ ] Transfers work or fail gracefully
- [ ] Booking and failed-booking flows behave correctly
- [ ] `USE_POSTGRES=false` preserves legacy behavior
- [ ] `USE_POSTGRES=true` resolves tenant/config and dual-writes call logs
- [ ] PostgreSQL, SMS, Cal.com, invalid DID, and missing tenant failures do not crash calls
- [ ] Latency logs are captured and reviewed
- [ ] Logs include `call_id` and `tenant_id` where available and do not expose secrets or transcripts

### Validation Artifact

Operators complete `OPERATOR_VALIDATION.md` and update staging evidence before Phase 4 can start.

---

## Phase 4 — Next.js Frontend Migration

### Goal

Replace inline HTML dashboard in `ui_server.py` with standalone Next.js app per `ARCHITECTURE.md` §6.

**Blocked until Phase 3.5 passes.**

### Strategy

Build Next.js app in `frontend/` directory. Supervisor serves it on port 3000. EasyPanel Traefik routes to it. Old HTML in `ui_server.py` is deleted only after Next.js is fully functional.

### Files Touched

| Action | File |
|---|---|
| Create | `frontend/` — full Next.js App Router project |
| Create | Pages: `/login`, `/dashboard`, `/calls`, `/bookings`, `/settings/*` |
| Create | `frontend/lib/api.ts` — fetch wrapper to localhost:8000 |
| Modify | `supervisord.conf` — add Next.js process |
| Modify | `Dockerfile` — add Node.js build stage for frontend |
| Remove | Inline HTML from `ui_server.py` (after Next.js validated) |

### Build Strategy

```
Dockerfile:
  Stage 1: Python deps + backend
  Stage 2: Node.js deps + next build (standalone output)
  Stage 3: Runtime — Python + Node.js + ffmpeg + Supervisor
```

### Pages (priority order)

1. `/login` — email/password, session cookie
2. `/dashboard` — stats, recent calls
3. `/calls` — call log table, transcript view
4. `/bookings` — booking list
5. `/settings/agent` — system prompt, first line
6. `/settings/voice` — language, TTS voice
7. `/settings/business` — hours, transfer number

### Risks

| Risk | Mitigation |
|---|---|
| Docker image size blows past 500MB | Multi-stage build; standalone output (no node_modules in final image) |
| Frontend ↔ API routing broken in Docker | Test Traefik routing in EasyPanel staging |
| Building Next.js adds deploy time | Cache node_modules layer in Dockerfile |
| Old dashboard still serving | Only remove HTML from `ui_server.py` after Next.js validated end-to-end |

### Success Criteria

- [ ] `next build` produces standalone output
- [ ] `/login` → `/dashboard` flow works with session cookie
- [ ] All settings pages read/write via FastAPI
- [ ] Call logs and bookings display correctly
- [ ] Docker image < 500MB
- [ ] EasyPanel routes domain to Next.js, `/api/*` to FastAPI

### Validation Checkpoint

```
1. docker build (multi-stage with frontend)
2. Image size check < 500MB
3. Login → dashboard → settings → save → next call picks up changes
4. Call logs display after inbound test call
5. EasyPanel deploy + domain routing verified
```

---

## Phase 5 — SMS Integration

### Goal

Replace Telegram/WhatsApp notifications with provider-abstract SMS layer. Fast2SMS as default provider.

### Strategy

Create `backend/integrations/sms.py` with abstract interface. Implement Fast2SMS adapter. Wire into post-call pipeline. Add `notification_events` logging. Keep `notify.py` as dead code until validated.

### Files Touched

| Action | File |
|---|---|
| Create | `backend/integrations/sms.py` — abstract SMS interface + Fast2SMS adapter |
| Create | `backend/db/notifications.py` — notification_events CRUD |
| Modify | `backend/voice/agent.py` — post-call pipeline calls SMS instead of Telegram |
| Preserve | `notify.py` — dead code until Phase 5 validated |

### SMS Message Templates

- Booking confirmation: "Hi {name}, your appointment at {business} is confirmed for {date} at {time}."
- All messages in patient's detected language (from call)
- All phone numbers in +91 format

### Risks

| Risk | Mitigation |
|---|---|
| Fast2SMS API unreliable | Retry once, log failure, never block post-call pipeline |
| Wrong phone number format | Validate +91 format before sending |
| SMS sent but notification_events not logged | Wrap in try/finally — always log |

### Success Criteria

- [ ] Inbound call with booking → SMS received on patient phone
- [ ] `notification_events` row created with status='sent'
- [ ] Failed SMS logged as status='failed', pipeline continues
- [ ] Telegram code not executing (dead code)
- [ ] Latency unaffected (SMS is post-call async)

---

## Phase 6 — Recording Pipeline

### Goal

Add call recording with async upload to S3-compatible storage per `ARCHITECTURE.md` §10.

### Strategy

Enable LiveKit room recording (Egress API or composite recording). After call ends, upload recording file to S3-compatible storage asynchronously. Save metadata to `call_recordings` table. Dashboard serves recordings via signed URLs.

### Files Touched

| Action | File |
|---|---|
| Create | `backend/integrations/storage.py` — S3-compatible upload (abstract interface) |
| Create | `backend/db/recordings.py` — call_recordings CRUD |
| Modify | `backend/voice/agent.py` — post-call pipeline triggers recording upload |
| Modify | `backend/api/routes/calls.py` — add recording download endpoint (signed URL) |
| Modify | Frontend — add playback UI on call detail page |

### Risks

| Risk | Mitigation |
|---|---|
| Recording upload blocks post-call pipeline | Async background task — fire-and-forget with logging |
| S3 credentials misconfigured | Health check verifies storage connectivity; recording upload failures logged, never fatal |
| Large recordings fill disk | Upload immediately, delete local file after confirmed upload |
| LiveKit Egress not available on plan | Fall back to local recording via agent audio capture |

### Success Criteria

- [ ] Inbound call → recording uploaded to storage
- [ ] `call_recordings` row created with storage_key
- [ ] Dashboard plays recording via signed URL
- [ ] Upload failure logged, does not crash pipeline
- [ ] Latency unaffected (recording is async)

---

## Phase 7 — Multi-Tenant Rollout

### Goal

Enable true multi-tenant operation: multiple businesses, each with their own DID, config, dashboard login, and isolated data.

### Strategy

This phase wires together everything built in Phases 1–6. The simplified runtime schema keeps tenant identity and AI behavior on one row. This phase adds:
- Tenant provisioning (create tenant + DID mapping + prompt/greeting/language/voice)
- Voice agent reads tenant_id from DID at call start
- Dashboard shows only tenant's own data

### Files Touched

| Action | File |
|---|---|
| Create | `backend/db/tenants.py` — tenant CRUD, provisioning |
| Create | `backend/api/routes/admin.py` — tenant provisioning (internal/admin) |
| Modify | `backend/voice/config.py` — load config by DID → tenant_id (remove hardcoded tenant) |
| Modify | `backend/voice/agent.py` — read DID from LiveKit room metadata |
| Modify | Frontend settings — display tenant name, DID |

### Tenant Provisioning Flow

```
1. Startup creates/seeds tenant (name, phone_number/DID, prompt, greeting, language, voice)
2. Dashboard login uses workspace slug derived from tenant name plus env password
3. Dashboard edits update the same tenant row
4. Vobiz DID configured to route to LiveKit SIP gateway
5. Business owner logs in → sees their dashboard
6. Inbound call to their DID → agent loads their tenant row
```

### Risks

| Risk | Mitigation |
|---|---|
| DID not found → call fails silently | Agent plays "This number is not configured" message and ends call gracefully |
| Cross-tenant data leak | Every query has `WHERE tenant_id = %s`; auth middleware enforces; test with 2+ tenants |
| Hardcoded tenant_id remnants | Search entire codebase for hardcoded UUIDs before Phase 7 deploy |
| Vobiz DID provisioning delay | Onboard first clinic with manually configured DID; automate later |

### Success Criteria

- [ ] Two tenants created with different DIDs
- [ ] Call to DID-A → loads tenant-A config, speaks tenant-A first line
- [ ] Call to DID-B → loads tenant-B config, speaks tenant-B first line
- [ ] Tenant-A dashboard shows only tenant-A calls/bookings
- [ ] Tenant-B dashboard shows only tenant-B calls/bookings
- [ ] Unknown DID → "not configured" message → call ends

### Validation Checkpoint

```
1. Create 2 test tenants with different Vobiz DIDs
2. Call DID-A → verify tenant-A agent personality
3. Call DID-B → verify tenant-B agent personality
4. Login as tenant-A → verify only tenant-A data visible
5. Login as tenant-B → verify only tenant-B data visible
6. Call unknown DID → verify graceful rejection
```

---

## Phase 8 — Production Hardening

### Goal

Production readiness for first dental clinic customer on EasyPanel.

### Sub-Phases

### 8a. Observability

- Deploy structured JSON logging across all services
- Add `call_id` correlation to every log line in call context
- Add `tenant_id` to every log line in tenant context
- Wire Prometheus metrics per `ARCHITECTURE.md` §11
- Configure Sentry with tenant_id + call_id tags
- Verify `/health` endpoint returns DB connectivity status

### 8b. Security Hardening

- Audit all endpoints for auth requirement
- Rate limiting on `/api/auth/login` (brute force protection)
- Verify no secrets in frontend code or logs
- Verify no secrets in Sentry payloads
- Verify session cookie flags (HttpOnly, Secure, SameSite)
- Verify `config.json` is not deployed (env vars only)

### 8c. Performance Validation

- End-to-end latency profiling under load (multiple concurrent calls)
- Verify barge-in works correctly with noisy background
- Verify endpointing delay is tuned per tenant
- Verify connection pool handles concurrent calls without exhaustion
- Verify Docker image < 500MB

### 8d. Deployment Finalization

- EasyPanel production environment configured
- Domain + SSL via Traefik
- PostgreSQL production database (EasyPanel-managed)
- All environment variables set in EasyPanel
- Health check configured (30s interval, 3 failure threshold)
- Backup strategy for PostgreSQL

### Files Touched

| Action | File |
|---|---|
| Modify | All backend files — structured logging |
| Modify | `backend/api/middleware/auth.py` — rate limiting |
| Modify | `Dockerfile` — HEALTHCHECK directive |
| Create | `backend/logging.py` — JSON formatter with call_id/tenant_id context |
| Verify | All env vars in EasyPanel match `ARCHITECTURE.md` §8 |

### Risks

| Risk | Mitigation |
|---|---|
| Logging adds latency to voice path | Log asynchronously; never block STT/LLM/TTS pipeline |
| Rate limiting too aggressive | Start with generous limits (100 req/min per IP); tune based on real traffic |
| Production database not backed up | Configure daily pg_dump before going live |

### Success Criteria

- [ ] All logs are JSON-structured with call_id and tenant_id
- [ ] Prometheus metrics accessible at `/metrics`
- [ ] Sentry captures errors with tenant/call context
- [ ] No unauthenticated data endpoints (except `/health`)
- [ ] Latency < 1.5s under 5 concurrent calls
- [ ] Docker image < 500MB
- [ ] EasyPanel health check passing
- [ ] First dental clinic tenant provisioned and receiving live calls

---

## Non-Negotiables (every phase)

These must remain true after every phase:

- [ ] Inbound calls work end-to-end (PSTN → Vobiz → LiveKit → agent → response)
- [ ] Streaming STT → LLM → TTS pipeline unbroken
- [ ] Barge-in/interruption handling works
- [ ] Latency < 1.5s silence-to-speech
- [ ] 10 Indian languages + auto-detect functional
- [ ] Cal.com booking flow works
- [ ] Docker builds successfully
- [ ] Supervisor starts all processes
- [ ] EasyPanel deployment works
- [ ] No secrets in files, frontend, or logs
- [ ] PostgreSQL raw SQL path validated; Supabase fallback preserved until explicit removal is approved

---

## Safe Migration Rules

| Rule | Rationale |
|---|---|
| **Never rewrite voice engine from scratch** | Barge-in, streaming, VAD tuning are fragile; move code, don't rewrite |
| **Adapter layers before removal** | New module works → old module becomes dead code → remove after validation |
| **Feature flags over switches** | `USE_POSTGRES=true` env var to toggle DB backend during Phase 1 |
| **One concern per PR** | Easier to revert, easier to validate |
| **Test inbound call after every change** | Automated if possible; manual at minimum |
| **Docker build before deploy** | Never push code that doesn't build |
| **Latency check after every phase** | Regression means revert |
| **Keep dead code temporarily** | Old `db.py`, `notify.py` stay until replacement is validated |
| **Seed data for testing** | Every phase includes test tenant + test user for validation |

---

## Parallel Work Opportunities

After Phase 2 (modularization), these can proceed in parallel:

```
Phase 2 complete
  ├── Phase 3 (Auth)     ─── can start immediately
  ├── Phase 5 (SMS)      ─── can start immediately (only needs db/)
  └── Phase 6 (Recording) ── can start immediately (only needs db/ + storage)

Phase 3 complete
  └── Phase 3.5 (Operator Validation) ── proves real deployment and telephony

Phase 3.5 complete
  └── Phase 4 (Frontend)  ── allowed only after real runtime validation

Phases 3.5–6 all complete
  └── Phase 7 (Multi-tenant) ── wires everything together
  
Phase 7 complete
  └── Phase 8 (Hardening)  ── production readiness
```

---

## Milestone: First Production Dental Clinic

**Minimum viable deployment** requires:
- Phases 1–5 complete (PostgreSQL, modular backend, auth, frontend, SMS)
- Phase 7 with single tenant (no provisioning UI needed — manual setup)
- Phase 8a (observability) and 8b (security) at minimum

**Can defer to post-launch:**
- Phase 6 (recording) — nice-to-have, not blocking
- Phase 8c (load testing) — single clinic won't have concurrent calls initially
- Tenant provisioning UI — first clinic is manually provisioned

**Fastest path:**
```
Phase 1 → Phase 2 → Phase 3 → Phase 3.5 (operator validation) → Phase 4 (MVP pages only) → Phase 5 → Phase 7 (single tenant) → Phase 8 (minimal) → LAUNCH
```

**Execution speed depends on validation discipline, AI coding quality, deployment stability, and testing rigor.**

---

*Execution roadmap only. No code, no migrations, no scripts. Implementation follows this plan. Validate against `RULES.md` and `ARCHITECTURE.md` at every step.*
