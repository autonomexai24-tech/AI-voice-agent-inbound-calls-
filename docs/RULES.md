# RULES.md — Hard Constraints for AI Coding Agents

> **These rules are non-negotiable.** Any AI agent working on this project must follow every rule below.
> Violating these rules will break the product, the architecture, or the deployment.

---

## 1. Forbidden Services

| Never Use | Use Instead |
|---|---|
| Supabase (SDK, Auth, Storage, Realtime) | PostgreSQL + psycopg2 raw SQL |
| Vercel | EasyPanel on Hostinger VPS |
| Coolify | EasyPanel |
| Firebase / Firestore | PostgreSQL |
| AWS / GCP managed services | Self-hosted on VPS |
| Twilio (voice) | Vobiz SIP |
| Any ORM (SQLAlchemy, Prisma, Drizzle, TypeORM) | Raw SQL only |

---

## 2. Database Rules

- **PostgreSQL only** — no other database
- **Raw SQL only** — via `psycopg2` (Python) or `pg` (Node.js)
- **No ORM** — no SQLAlchemy, no Prisma, no Drizzle
- **No Supabase SDK** — even if Supabase hosts the PostgreSQL instance
- **Every table has `tenant_id`** — no exceptions
- **Every query filters by `tenant_id`** — no cross-tenant access
- **Connection pooling** — use `psycopg2.pool` for all database access

---

## 3. Voice Latency Rules

**Target: < 1.5 seconds from end of caller speech to start of agent speech.**

| Rule | Reason |
|---|---|
| **One LLM call per turn** | Each extra call adds 500–1000ms |
| **No RAG / vector search during calls** | Adds 200–500ms |
| **Minimize DB queries during conversation** | Only essential tool queries (availability check); no analytics, no logging mid-call |
| **Streaming STT → LLM → TTS** | Never wait for full completion |
| **Short system prompts** (< 2000 tokens) | Longer = slower time-to-first-token |
| **Short responses** (max 150 tokens) | `max_completion_tokens=150` always |
| **Pre-load config at call start** | Tenant config loaded once at call start, not re-queried mid-call |
| **Silero VAD** | Local, sub-10ms, no network |

### Barge-In & Interruption

- If the caller speaks while the agent is talking, the agent must **stop speaking immediately**
- TTS audio must be cancelled mid-stream — do not finish the sentence
- LiveKit handles barge-in natively via VAD; do not add custom logic that delays this
- Noisy Indian environments (traffic, crowds, TV) must not trigger false barge-in — rely on Silero VAD thresholds

### What Is Allowed At Each Stage

| Stage | Allowed | Forbidden |
|---|---|---|
| Call start | Load tenant config from DB | Heavy computation, cold starts |
| During conversation | STT → LLM → TTS streaming | Logging queries, analytics, file I/O |
| Tool execution (mid-call) | Cal.com API (< 2s timeout), availability DB query | Multiple sequential API calls, LLM re-calls, unbounded queries |
| Call end (post-hangup) | DB writes, SMS, analytics, recording upload | Nothing — caller is gone |

---

## 4. Security Rules

- **No secrets in files** — environment variables only
- **No secrets in frontend** — backend proxies all external API calls
- **No unauthenticated API access** — every data endpoint requires session cookie
- **`/health` is the only unauthenticated endpoint**
- **bcrypt for password hashing** — no plaintext, no MD5, no SHA
- **Signed session cookies** — no JWT tokens, no bearer tokens
- **Rate limiting** on all public endpoints
- **Application startup must fail fast if critical environment variables are missing** — do not silently default, do not start in a degraded state

---

## 5. Architecture Rules

- **Logically multi-tenant** — tenant isolation via `tenant_id` at data, auth, config, and runtime layers
- **Deployment-flexible** — initial deployments use shared infrastructure; architecture supports horizontal scaling later; do not hardcode one deployment topology forever
- **One logical AI identity per business** — isolated via config row, not via separate process
- **Inbound-first** — this is a receptionist, not a sales dialer; outbound is future scope only
- **Supervisor process management** — current model uses single container with Supervisor; this is not permanently locked
- **No GraphQL** — REST API only
- **No WebSockets for dashboard** — polling or SSE is sufficient

---

## 6. Deployment Rules

- **Always EasyPanel** on Hostinger VPS
- **Always Docker** with multi-stage build
- **Always Supervisor** for process management (agent + API + frontend)
- **Always standalone Next.js output** — no Vercel, no serverless
- **Always `/health` endpoint** — EasyPanel pings every 30s
- **Always pin dependency versions** — reproducible builds
- **Image size < 500MB**

---

## 7. Frontend Rules

- **Next.js** with React 19, App Router, standalone output
- **TailwindCSS** for styling
- **shadcn/ui** for components
- **Lucide** for icons
- **No HTML embedded in Python** — ever
- **Frontend calls FastAPI via internal HTTP** (localhost in Docker)
- **Frontend settings dynamically control backend** — changes take effect on next call
- **Tenant-based auth** — email/password login per tenant, not a global password

---

## 8. Notification Rules

- **SMS is the primary patient notification channel** — Fast2SMS is the default provider
- **Provider-abstract design** — notification layer should support swapping SMS providers without changing business logic
- **Telegram is legacy** — prototype only, mark for removal
- **Twilio WhatsApp is removed** — unnecessary cost
- **All notifications logged** in `notification_events` table with delivery status
- **SMS must be concise and multilingual**
- **Notifications never block live calls** — all SMS is post-call async

---

## 9. Code Style Rules

- **All imports at file top level** — never inside functions or request handlers
- **Never mutate `os.environ` at runtime** — set env vars at process start only
- **Never store secrets in `config.json`** — environment variables only
- **Never embed HTML in Python** — all UI in Next.js
- **Never delete or weaken existing tests**
- **Structured JSON logging** — never `print()` for production logs

---

## 10. Indian-First Design

- All timestamps in **IST** (Asia/Kolkata)
- All phone numbers in **+91** format
- All languages are **Indian languages**
- All currency in **INR**
- Business hours assume **Indian working patterns**
- Vobiz is the SIP provider — **not Twilio**

---

*These rules apply to every file, every PR, every conversation. No exceptions.*
