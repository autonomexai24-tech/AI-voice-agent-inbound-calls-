# TESTING.md — Verification Loops & Production Tests

> Copy-pasteable commands and checklists. Pairs with `EXECUTION.md`.
> Replace `<HOST>` with your EasyPanel host (e.g. `dhanushpackaging12.aivoice.ocznup.easypanel.host`).

---

## 1. AUTH TESTS

### 1.1 Diagnostic endpoint (verify deployed code)

```bash
curl -s https://<HOST>/api/auth/_diag | jq
```

Expected fields when commit `3b40147` is live:
- `build_marker == "phase3a-credentials-fallback-v1"`
- `expected_password_length == 17`
- `expected_password_first2 == "Ra"`, `expected_password_last2 == "21"`

If 404 or different values → see `EXECUTION.md §A.2`.

### 1.2 Direct login curl (bypasses frontend)

```bash
curl -i -X POST https://<HOST>/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"harshhavanur2005@gmail.com","password":"RapidX-Voice-7421","tenant_slug":"default"}'
```

| Status | Pass/Fail | Action |
|---|---|---|
| 200 + `Set-Cookie: rapid_session=` | ✅ pass | Frontend issue if browser still fails |
| 401 `Invalid credentials` | ❌ | Old code or env override |
| 409 `Workspace is required` | ⚠ | Add `tenant_slug` (already in payload) — Postgres path bug |
| 502/504 | ❌ | ui_server process dead |

### 1.3 Wrong password test

```bash
curl -i -X POST https://<HOST>/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"harshhavanur2005@gmail.com","password":"wrong","tenant_slug":"default"}'
```

Expected: `401 Invalid credentials`. Anything else is a bug.

### 1.4 Missing workspace test (Postgres-enabled only)

```bash
curl -i -X POST https://<HOST>/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"x@y.com","password":"x"}'
```

Expected:
- `USE_POSTGRES=true` → `409 Workspace is required for this email`
- `USE_POSTGRES=false` → `401 Invalid credentials`

### 1.5 Session cookie persistence

```bash
# Login and save cookies
curl -c /tmp/cj.txt -X POST https://<HOST>/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"harshhavanur2005@gmail.com","password":"RapidX-Voice-7421","tenant_slug":"default"}'

# Use saved cookie
curl -b /tmp/cj.txt https://<HOST>/api/auth/me
```

Expected: `{"user": {"email": "...", ...}}`. If `401`, cookie didn't stick → cookie domain or `secure` issue (`EXECUTION.md §A.3`).

### 1.6 Logout

```bash
curl -b /tmp/cj.txt -X POST https://<HOST>/api/auth/logout
curl -b /tmp/cj.txt https://<HOST>/api/auth/me
# Expected: 401
```

### 1.7 Rate-limit test

```bash
# Hit login 10× with bad creds in <5 min
for i in {1..10}; do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<HOST>/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"x@y.com","password":"bad","tenant_slug":"default"}'
done
```

Expected: first 8 return `401`, attempts 9–10 return `429 Too many requests`.

---

## 2. CALL FLOW TESTS

### 2.1 Live inbound call — happy path

Place a real call to your Vobiz DID. Listen for:

| Stage | Expected |
|---|---|
| Ring → answer | < 2s |
| Greeting plays | First line clear, no clip |
| You speak a question | Agent responds within ~1s of you stopping |
| You request booking | Agent collects name → date → time → confirms |
| Hang up | Call ends cleanly |

Then check container logs for one full set of metrics per turn:
```bash
# In EasyPanel logs tab, filter by call_id and look for:
grep '"latency.silence_to_speech_estimate"' /var/log/agent.log | tail -10
grep '"latency.llm"' /var/log/agent.log | tail -10
grep '"latency.tts"' /var/log/agent.log | tail -10
```

### 2.2 Compute mean latency from logs

Once you have ≥10 turns logged:

```bash
# silence-to-speech (turn round-trip)
grep '"latency.silence_to_speech_estimate"' /var/log/agent.log \
  | grep -oE '"latency_ms":\s*[0-9.]+' \
  | awk -F: '{sum+=$2; n++} END {if(n) print "mean:", sum/n, "ms,", "samples:", n}'

# LLM TTFT
grep '"latency.llm"' /var/log/agent.log \
  | grep -oE '"ttft_ms":\s*[0-9.]+' \
  | awk -F: '{sum+=$2; n++} END {if(n) print "LLM ttft mean:", sum/n, "ms"}'

# TTS TTFB
grep '"latency.tts"' /var/log/agent.log \
  | grep -oE '"ttfb_ms":\s*[0-9.]+' \
  | awk -F: '{sum+=$2; n++} END {if(n) print "TTS ttfb mean:", sum/n, "ms"}'
```

Pass criteria:
- LLM TTFT < 600ms
- TTS TTFB < 350ms
- silence_to_speech mean < 1200ms

### 2.3 Multilingual switching

| Test | How | Expected |
|---|---|---|
| English caller | Say "Hello, can I book an appointment?" | Agent replies in Indian English |
| Hindi caller | Say "Namaste, mujhe booking karni hai" | Agent replies in Hindi |
| Hinglish caller | "Bhai, kal ka slot available hai?" | Agent matches Hinglish |
| Mid-call switch | Start in English, switch to Hindi | Agent switches within 1 turn (only if `lang_preset=multilingual`) |

### 2.4 Transfer flow

Call and say: "Transfer me to a human."

Expected:
- Agent says "Transfer initiated successfully" (or your custom phrase)
- LiveKit issues SIP REFER to `DEFAULT_TRANSFER_NUMBER`
- Audio bridges to the human number within 2–3s

Verify in logs:
```
[TOOL] transfer_call triggered
[TOOL] transfer_call completed
```

### 2.5 Booking flow end-to-end

Caller says: "Book an appointment for tomorrow at 2pm. My name is Test User. Phone is +911234567890."

Expected sequence in logs:
```
[TOOL] save_booking_intent
notification.sms.enqueued (or skipped if no SMS provider)
[POSTGRES] Call log dual-write complete   (if USE_POSTGRES=true)
[BOOKING] Created on Cal.com               (in calendar_tools log)
```

Verify in dashboard: `/bookings` page should show the new entry.

### 2.6 Interruption test

While agent is mid-greeting (4–5 word point):
- Speak loudly: "Wait!"

Expected:
- Agent stops within 300–500ms
- Your input is processed
- Agent does NOT continue old reply

In logs, search for:
```
[INTERRUPT] Agent interrupted
"latency.tts" "cancelled": true
```

If the agent finishes the entire sentence anyway, see `EXECUTION.md §C`.

### 2.7 Filler-word filter

Caller says only "okay" or "hmm" — agent should **not** treat this as a turn (no LLM call).

In logs:
```
[FILTER-FILLER] Dropped filler transcript
```

---

## 3. LATENCY PROFILING

### 3.1 Capture 5 minutes of real production logs

In EasyPanel Logs tab:
1. Filter by `latency.` prefix.
2. Place 3–4 real test calls.
3. Save the log dump as `latency_capture.txt`.
4. Run `2.2` commands on that file.

### 3.2 Per-stage budget check

Compute these, compare to budget:

| Metric | Budget | Action if exceeded |
|---|---|---|
| `latency.stt.duration_ms` | < 200ms | Switch to Deepgram (`stt_provider=deepgram`) |
| `latency.llm.ttft_ms` | < 600ms | Switch to Groq (`llm_provider=groq, llm_model=llama-3.3-70b-versatile`) |
| `latency.llm.duration_ms` | < 1500ms | Cap reply length more aggressively |
| `latency.tts.ttfb_ms` | < 350ms | Switch to ElevenLabs Turbo |
| `latency.silence_to_speech_estimate` | < 1200ms mean | Investigate worst stage |

### 3.3 First-turn vs steady-state

The first turn pays cold-start tax (LLM warm-up, TTS warm-up). Compare:

```bash
# First turn per call (turn_count=1)
grep '"turn_count": 1' agent.log | grep latency.silence_to_speech_estimate

# Subsequent turns
grep -v '"turn_count": 1' agent.log | grep latency.silence_to_speech_estimate
```

Acceptable: first-turn ~1500ms, steady-state ~800–1100ms.

---

## 4. FAILURE TESTS (chaos)

### 4.1 OpenAI API outage simulation

Block OpenAI on the container's egress for 10s during a live call.

Expected:
- Agent doesn't crash.
- Agent says fallback (or hangs gracefully).
- Call doesn't drop.
- After 10s, network restored → next turn works.

Simulate via iptables on the host (or by setting an invalid `OPENAI_API_KEY` and restarting).

### 4.2 Postgres crash simulation

Stop the Postgres container while a call is in progress.

Expected:
- **Live call continues uninterrupted.** This is the key invariant.
- `postgres.operation.slow` logged for any in-flight query.
- Shutdown hook logs `[POSTGRES] Call log dual-write skipped` with `error_type`.
- Call audio path unaffected.

If the call drops, that's a P0 bug.

### 4.3 SIP disconnect mid-call

Caller hangs up by force (drops cellular signal).

Expected:
- `[HANGUP] Participant disconnected` log line.
- `unified_shutdown_hook` runs.
- Call log written.
- No exception unhandled.

### 4.4 LiveKit reconnect

Hard to simulate; rely on monitoring `latency.silence_to_speech_estimate`
spikes — values > 5s suggest a reconnect happened mid-call.

### 4.5 TTS provider failure

Set invalid `SARVAM_API_KEY` temporarily. Place a call.

Expected:
- Plugin import succeeds (no API call yet).
- First TTS attempt fails → caller hears silence or fallback.
- Agent should say "I'm having trouble" — currently may not. Note as gap.

---

## 5. MULTI-TENANT TESTS

### 5.1 Cross-tenant data isolation

With two tenants A and B:

```bash
# Login as user@A
curl -c /tmp/A.txt -X POST .../api/auth/login -d '{"email":"a@a.com","password":"...","tenant_slug":"a"}'

# Try to read tenant B's call log via direct ID
curl -b /tmp/A.txt .../api/logs/<TENANT_B_LOG_ID>/transcript
# Expected: 404 Not found (NEVER 200 with B's transcript)
```

Repeat for `/api/bookings`, `/api/recordings/<id>/playback`, `/api/contacts`.

### 5.2 DID-based tenant routing

Place call to tenant A's DID, then to tenant B's DID. Check logs:

```
[TENANT] DID resolved for tenant lookup    did_masked=...
config.runtime.selected                    tenant_id=<A_id>, config_source=postgres
```

Then second call:
```
config.runtime.selected                    tenant_id=<B_id>
```

Confirm `tenant_id` differs and matches the DID.

### 5.3 Inactive tenant blocks call

In Postgres: `UPDATE tenants SET is_active=FALSE WHERE id='<TID>';`

Place a call to that tenant's DID.

Expected: greeting says "This number is not configured. Please contact support." and call ends within 4 seconds (`agent.py:418-433`).

### 5.4 Unknown DID falls back

Place a call from a number whose DID has no tenant row.

Expected: same fallback as 5.3, log line `tenant.config.unavailable`.

---

## 6. LOAD TESTS

### 6.1 Concurrent call simulation

LiveKit Cloud allows multiple concurrent agent jobs. With 5 simultaneous test calls:

| Metric | Pass | Fail |
|---|---|---|
| All 5 calls answered | ✅ | If any fail: worker capacity issue |
| Per-call latency degradation | < 100ms vs single-call | If > 200ms: shared resource contention (LLM rate limit?) |
| Postgres pool stats | `available > 0` always | If 0: bump `POSTGRES_POOL_MAX` |
| Container CPU | < 80% sustained | Else: scale or optimize |
| Container RAM | Stable, no leak over 30 min | Else: leak hunt |

Probe `/health`:
```bash
watch -n 2 'curl -s https://<HOST>/health | jq .checks.postgres'
```

### 6.2 Long-call test

Place a 15-minute call. Hit `max_turns=25` early by talking constantly.

Expected:
- At turn 25: agent wraps up gracefully.
- Memory stable.
- After hangup: shutdown hook runs cleanly.

### 6.3 Reconnect storm

Restart container while 3 calls are active.

Expected:
- All 3 calls drop (acceptable — single container).
- After restart, new calls work within 30s.
- No leaked DB connections (check pool stats post-restart).

---

## 7. DEPLOYMENT CHECKS

### 7.1 Verify build commit

After redeploy:
```bash
curl -s https://<HOST>/health | jq .
```

Expected: includes `"build_rev": "<commit-sha>"` once `EXECUTION.md §D.4` is shipped.

### 7.2 Process supervision

Inside container (or via EasyPanel exec):
```bash
supervisorctl status
```

Expected: `agent`, `ui_server`, `frontend` all `RUNNING`.

### 7.3 Frontend ↔ backend wiring

```bash
# From inside the container:
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:3000/api/auth/_diag
# The latter should return the same JSON as direct backend (verifies Next rewrite works)
```

### 7.4 EasyPanel public route

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://<HOST>/login
# Expected: 200

curl -s -o /dev/null -w "%{http_code}\n" https://<HOST>/health
# Expected: 200 (proxied via Next rewrite to FastAPI)
```

---

## 8. REGRESSION CHECKLIST (run before each deploy)

- [ ] `/api/auth/_diag` returns `build_marker` matching expected commit
- [ ] Direct curl login (`§1.2`) returns 200
- [ ] Logged-in `/api/auth/me` returns user (`§1.5`)
- [ ] `/health` returns `status: ok` (or `degraded` only if Postgres intentionally down)
- [ ] Single live call: greeting plays, conversation flows, hangup clean
- [ ] Single live call: no `error_type` lines in logs for the call_id
- [ ] Latency means within budget (`§3.2`)
- [ ] Booking flow creates DB row + Cal.com event
- [ ] No new SQL queries in voice path (search for `cur.execute` reachable from `entrypoint`)
- [ ] No new imports inside functions in `agent.py`
- [ ] Docker image size hasn't grown by more than 50MB

---

## 9. KILL-SWITCH RUNBOOK

If something is on fire in production:

| Symptom | Quick fix |
|---|---|
| All calls fail to answer | EasyPanel → restart agent service |
| Login works but dashboard empty | Check `USE_POSTGRES`; if true, verify `DATABASE_URL` reachable; if not, set `USE_POSTGRES=false` to fall back to JSON |
| Latency suddenly spiked | Check OpenAI status; switch to Groq via dashboard config |
| Booking failures | Check Cal.com API status; verify `CAL_API_KEY` |
| Recording uploads failing | Check S3 endpoint reachability; calls still work, recordings just queue with `failed` status |
| Postgres maxed out | `POSTGRES_POOL_MAX=20`; restart |

---

*TESTING.md complete. Combined with REVIEW.md (audit) and EXECUTION.md (fix plan), this is the operational kit.*
