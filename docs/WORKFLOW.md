# WORKFLOW.md — Call Flows & Business Logic

> Detailed workflows for the AI receptionist. For system design see `ARCHITECTURE.md`. For rules see `RULES.md`.

---

## 1. Inbound Call Lifecycle

```
1. Patient dials clinic number (Vobiz DID)
2. Vobiz SIP trunk routes to LiveKit SIP Gateway
3. LiveKit creates room, dispatches voice agent
4. Agent reads DID from room metadata
5. Agent queries PostgreSQL: tenant_id from DID
6. Agent loads tenant config (prompt, voice, language, hours, transfer number)
7. Agent injects business context + IST time into system prompt
8. Agent speaks first line (configured per tenant)
9. Conversation loop:
     a. Caller speaks
     b. Sarvam STT → text (streaming)
     c. OpenAI GPT → response or tool call (streaming)
     d. Sarvam TTS → audio (streaming)
     e. Audio plays to caller
     f. If caller interrupts (barge-in) → cancel TTS, process new utterance
10. Call ends (caller hangs up, agent calls end_call, or transfer)
11. Post-call processing (async, after hangup):
     a. Save call log to database
     b. If booking confirmed → create Cal.com booking + save booking record
     c. Send SMS confirmation to patient
     d. Log notification event
     e. Upload recording to storage (async)
```

---

## 2. Booking Flow

### Happy Path

```
Caller: "I want to book an appointment"
Agent:  "Sure! When would you like to come in?"
Caller: "Tomorrow afternoon"
Agent:  → calls check_availability("2026-05-10")
        "I have 2:00, 2:30, and 3:00 PM available. Which works?"
Caller: "2:30"
Agent:  "And your name please?"
Caller: "Priya Sharma"
Agent:  → calls save_booking_intent(start_time, name, phone, notes)
        "Confirmed for tomorrow at 2:30 PM. You'll get an SMS shortly!"
Caller: "Thanks, bye"
Agent:  → calls end_call()
        "Thank you Priya! See you tomorrow."
```

### Post-Booking Processing

1. `save_booking_intent` stores intent in agent memory during call
2. After call ends, booking created via Cal.com API
3. Booking record saved to database (with tenant_id, patient info, Cal.com UID)
4. SMS confirmation sent to patient (via SMS provider)
5. Notification event logged with delivery status

### Edge Cases

| Scenario | Agent Behavior |
|---|---|
| No slots on requested date | "That day is fully booked. How about {next_available}?" |
| Caller unsure about date | "Would today or tomorrow work for you?" |
| Cancel existing booking | Ask for name/reference → call cancel API |
| Reschedule | Cancel old booking → create new one |
| Cal.com API down | "I'm having trouble checking our calendar. Can I transfer you?" |
| Incomplete info | Ask follow-up: "What name should I book under?" |
| Multiple bookings in one call | Each calls `save_booking_intent` separately |

---

## 3. Human Transfer Flow

### Triggers

- Caller says: "Let me talk to someone", "Speak to doctor", "Human please"
- Caller is frustrated or angry
- Query outside scope: billing, medical advice, complaints
- Agent fails to resolve after 3 attempts
- Business config specifies always-transfer scenarios

### Flow

```
1. Agent: "Let me connect you with our team. One moment."
2. Agent calls transfer_call() tool
3. LiveKit SIP REFER to transfer_number (from tenant config)
4. Vobiz routes SIP transfer to destination phone
5. Call bridges to human
6. Agent exits room
```

### Transfer Failure

```
If destination busy/no answer:
  Agent: "The line seems busy. Can I take a message or have someone call you back?"
  → Log failed transfer
  → Continue conversation or end gracefully
```

---

## 4. Language Detection Flow

```
1. Call starts → agent configured in "multilingual" mode
2. Sarvam STT set to stt_language="unknown" (auto-detect)
3. Caller speaks first utterance (e.g., in Tamil)
4. STT transcribes with detected language
5. System prompt tells GPT: "Reply in the SAME language as the caller"
6. GPT responds in Tamil
7. TTS renders in Tamil (matching voice)
8. If caller switches to Hindi mid-call → STT detects → GPT follows
```

**No extra latency.** Language detection is built into Sarvam STT — no separate classification step.

### Fixed Language Mode

If tenant configures a fixed language (e.g., `tts_language: "ta-IN"`):
- STT set to that language
- System prompt instructs: "Always respond in Tamil"
- No auto-detection

---

## 5. Business Hours Flow

```
1. Call arrives → agent loads business_hours_json from tenant config
2. Current time in IST checked via get_ist_time_context()
3. If WITHIN hours:
     → Normal conversation
4. If OUTSIDE hours:
     → Agent: "We're currently closed. Our hours are Mon–Sat 9 AM to 7 PM. 
              Would you like to book an appointment for when we're open?"
     → Booking still allowed (future slot)
5. get_business_hours tool available for caller questions:
     "When are you open?" → reads from config
```

---

## 6. SMS Notification Flow

### Booking Confirmation SMS

1. Triggered after booking confirmed in Cal.com
2. SMS sent to patient phone (+91 format)
3. Content: "Hi {patient_name}, your appointment at {business_name} is confirmed for {date} at {time}."
4. Notification event logged with delivery status

### Failure Handling

- If SMS provider returns error → retry once after 5 seconds
- If still fails → log as failed, do not crash post-call pipeline
- Never retry more than once
- SMS failures never block other post-call steps

---

## 7. Dashboard Config Update Flow

```
1. Business owner logs into dashboard (/login)
2. Navigates to /settings/agent
3. Edits system prompt, clicks Save
4. Frontend sends config update to backend API
5. Backend verifies session cookie, extracts tenant_id
6. Backend updates tenant config in database
7. Response: 200 OK
8. Next inbound call loads fresh config automatically
9. No restart needed. No cache to invalidate.
```

---

## 8. Tenant Resolution Flow

```
New inbound call arrives:
  1. Vobiz routes to LiveKit via SIP
  2. LiveKit room metadata includes dialed DID
  3. Agent reads DID from SIP participant identity or room metadata
  4. Agent resolves DID to tenant_id via database lookup
  5. If found → load tenant config → run agent with tenant context
  6. If NOT found → play generic message: "This number is not configured." → end call
  7. All subsequent operations use tenant_id from step 4
```

---

## 9. Post-Call Processing Pipeline

After every call (runs async, after caller hangs up):

```
1. Save call log to database (tenant_id, phone, duration, transcript, summary, sentiment)

2. If booking was confirmed during call:
   a. Create booking via Cal.com API
   b. Save booking record to database
   c. Send SMS confirmation to patient
   d. Log notification event with delivery status

3. Upload call recording to storage (async, never blocks)

4. If no booking:
   → Only call log saved + recording uploaded

5. If transfer occurred:
   → Call log saved with transfer note

6. If agent error during call:
   → Sentry captures error
   → Call log saved with error flag
```

---

## 10. Error Handling Principles

| Failure | Response |
|---|---|
| Cal.com API timeout | Tell caller, offer transfer to human |
| SMS provider failure | Log error, continue — never block post-call pipeline |
| PostgreSQL unreachable | Preserve live conversation quality, fail post-call operations gracefully, retry safely where possible |
| OpenAI API timeout | Apologize, retry once, then transfer to human |
| Sarvam STT failure | Fallback to silence → ask caller to repeat |
| LiveKit room error | Log to Sentry, caller hears disconnect |
| Tenant not found for DID | Play "not configured" message, end call |

**Core principle:** Never let an infrastructure failure kill a live phone call.

---

*These flows define the product behavior. For system constraints see `RULES.md`. For architecture see `ARCHITECTURE.md`.*
