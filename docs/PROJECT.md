# PROJECT.md — What We Are Building

> **Read this first.** Product vision and business direction for AI coding agents.
> Hard rules → `RULES.md` | System design → `ARCHITECTURE.md` | Codebase audit → `CORRECTION.md` | Flows → `WORKFLOW.md`

---

## 1. Product Vision

**Multilingual AI receptionist SaaS for Indian businesses.**

- Answers inbound phone calls 24/7 in the caller's language
- Books, reschedules, cancels appointments via natural voice
- Transfers to a human when needed
- Sends SMS confirmation to patients
- Gives business owners a dashboard for calls, bookings, analytics

**This is NOT:** a sales dialer, outbound cold-calling tool, chatbot, CRM, or marketing platform.

---

## 2. Problem We Solve

Indian appointment-based businesses lose revenue from missed phone calls:

- Receptionist busy → call unanswered
- After-hours / holidays → no one picks up
- Language barriers → caller and staff speak different languages
- High turnover → inconsistent phone experience

**Impact:** ₹2,000–₹15,000 lost per missed call at a dental clinic. Clinics miss 15–30% of inbound calls.

---

## 3. Target Market

**Primary:** Dental clinics, med spas, salons
**Secondary:** Real estate, physiotherapy, veterinary clinics, tutoring centers
**Geography:** India-first. Tier 1 cities → Tier 2/3. Regional language support is the key differentiator.

---

## 4. SaaS Business Model

- Monthly subscription per business (flat rate, no per-minute billing)
- SMS costs absorbed into subscription
- Cal.com integration included at all tiers
- Key cost drivers: Sarvam AI (STT/TTS), OpenAI (tokens), LiveKit (media)

---

## 5. Architecture Principles

### Multi-Tenant with Deployment Flexibility

The platform is logically multi-tenant. Tenant isolation exists at the **data layer**, **auth layer**, **config layer**, and **runtime context layer**. Initial deployments use shared infrastructure; the architecture supports horizontal scaling as needed. Infrastructure topology is replaceable — do not hardcode a single deployment model forever.

### One Logical AI Identity Per Business

Each business has an isolated AI personality (prompt, voice, language, hours). This identity is a configuration row loaded dynamically per call — not a separate process, container, or deployment.

### Inbound-First

This is an AI receptionist for answering incoming calls. Outbound calling (appointment reminders) is Phase 4+ future scope. Never build outbound sales dialer features.

### Low Latency Above All

The voice pipeline must feel like a natural phone conversation. Target < 1.5s silence-to-speech. One LLM call per turn, streaming everything, no RAG during calls. See `RULES.md` for full latency constraints.

### Indian-First Design

All timestamps IST, phone numbers +91, currency INR, 10+ Indian languages, Vobiz SIP (not Twilio), business hours assume Indian working patterns.

---

## 6. Roadmap

| Phase | Focus |
|---|---|
| **1 — Foundation** | PostgreSQL migration, Next.js frontend, SMS notifications, tenant auth, env-only secrets |
| **2 — Production** | First dental clinic customer on EasyPanel, business hours, call recording |
| **3 — Multi-tenant** | Tenant provisioning, Vobiz DID management, admin panel, billing |
| **4 — Growth** | Analytics, follow-up reminders, outbound (reminders only), WhatsApp |
| **5 — Scale** | Horizontal scaling, vertical fine-tuning, voice cloning |

---

## 7. Out of Scope

| Forbidden | Reason |
|---|---|
| Outbound sales dialer | Inbound receptionist only |
| Supabase | PostgreSQL + raw SQL only |
| Vercel / Coolify / Kubernetes | EasyPanel deployment |
| Any ORM | psycopg2 raw SQL only |
| Multi-step LLM routing | Violates latency rules |
| RAG / vector search during calls | Violates latency rules |
| Mobile app | Responsive web dashboard is sufficient |
| Video calls | Voice only |
| Payment processing | Business handles directly |
| Medical records | Appointment data only |

---

*Product direction only. For tech stack and system design → `ARCHITECTURE.md`. For constraints → `RULES.md`.*
