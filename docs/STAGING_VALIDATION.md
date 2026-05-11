# STAGING_VALIDATION.md — Phase 3C Operator Checklist

> Audience: staging operator validating the current migrated architecture.
> Goal: prove the container survives realistic runtime conditions without
> changing realtime voice behavior.

Do not use this checklist to add features. If a validation fails, capture
logs, roll back to the previous image, and investigate off the live call path.

---

## 1. Required Staging Setup

Use a staging environment that matches production as closely as possible:

- Docker image built from the current repository.
- Supervisor starts the voice agent and FastAPI process in one container.
- LiveKit credentials point to the staging LiveKit project or staging room namespace.
- Vobiz DID routes to the LiveKit SIP gateway.
- Sarvam and OpenAI keys are valid.
- `USE_POSTGRES=false` is tested first.
- `USE_POSTGRES=true` is tested after PostgreSQL migration and seed data exist.

Critical env vars:

```text
LIVEKIT_URL
LIVEKIT_API_KEY
LIVEKIT_API_SECRET
OPENAI_API_KEY
SARVAM_API_KEY
```

PostgreSQL env vars for enabled mode:

```text
USE_POSTGRES=true
DATABASE_URL=postgresql://...
```

Recommended Postgres connection string option for staging:

```text
connect_timeout=2
```

---

## 2. Log Locations

Inspect these logs during every validation:

- EasyPanel application logs: container stdout/stderr.
- Docker local runtime: `docker logs <container>`.
- Supervisor process logs: stdout/stderr configured in `supervisord.conf`.
- API health response: `GET /health`.
- LiveKit room/session logs in LiveKit dashboard.
- PostgreSQL logs from the EasyPanel-managed database.
- Supabase dashboard logs only for legacy fallback confirmation.

Do not rely on `ui_server.log`; Supervisor writes runtime logs to stdout/stderr.

---

## 3. Docker Runtime Validation

Command:

```bash
docker build -t ai-receptionist:phase3c .
docker run --rm --env-file .env -p 8000:8000 ai-receptionist:phase3c
```

Expected behavior:

- Image builds successfully.
- Container starts Supervisor as PID 1.
- `agent` process starts with `python agent.py start`.
- `ui_server` process starts with `uvicorn ui_server:app`.
- `/health` returns JSON with `status`, `service`, `timestamp`, and `checks`.

Failure symptoms:

- Build fails during `pip install`.
- Supervisor exits immediately.
- `/health` unreachable on port `8000`.
- Logs show `startup.critical_env_missing`.

Rollback action:

- Revert to the last Phase 3B image.
- Restore the last known-good EasyPanel env var set.
- Keep `USE_POSTGRES=false` while investigating.

---

## 4. Supervisor Process Validation

Command inside the container:

```bash
supervisorctl status
```

Expected behavior:

```text
agent      RUNNING
ui_server  RUNNING
```

Failure symptoms:

- `agent` repeatedly enters `BACKOFF` or `FATAL`.
- `ui_server` exits because startup validation fails.
- Logs contain missing critical env vars.

Rollback action:

- Restore missing env vars.
- Restart the container.
- If the error appears after this phase only, roll back the image.

---

## 5. Health Endpoint Validation

Command:

```bash
curl -s http://localhost:8000/health | python -m json.tool
```

Expected behavior:

- `checks.postgres.postgres` is `disabled`, `ok`, `uninitialized`, or `error`.
- `checks.supabase_fallback.status` is `configured` or `not_configured`.
- `checks.config_source.json_source` shows the active JSON fallback source.
- `checks.startup_validation.status` is `ok`.
- `checks.startup_state.status` is `ok` or `degraded`.

Failure symptoms:

- Health endpoint hangs.
- Health endpoint logs secrets.
- Health endpoint performs writes.
- `startup_validation.status=missing_critical_env`.

Rollback action:

- Fix env vars if missing.
- If health hangs due Postgres, add `connect_timeout=2` to `DATABASE_URL`.
- If health still blocks, set `USE_POSTGRES=false` and redeploy.

---

## 6. PostgreSQL Disabled Mode

Env:

```text
USE_POSTGRES=false
```

Expected behavior:

- Startup logs `startup.postgres_disabled`.
- Config source is legacy JSON.
- Inbound call behavior matches Phase 3A/3B.
- Supabase remains the persistence fallback.

Failure symptoms:

- Agent attempts tenant lookup despite `USE_POSTGRES=false`.
- Call fails before greeting.
- Prompt or language preset differs from `config.json`.

Rollback action:

- Keep `USE_POSTGRES=false`.
- Roll back image if legacy config parity breaks.

---

## 7. PostgreSQL Enabled Mode

Preconditions:

```bash
psql "$DATABASE_URL" -f migrations/001_initial.sql
```

Seed one `tenants` row with the staging DID, prompt, greeting, language, and voice.

Expected behavior:

- Startup attempts pool initialization.
- If DB is reachable, logs show `startup.postgres_initialized`.
- DID resolves to tenant.
- Config source log shows `config_source=postgres.tenants`.
- Post-call call log dual-write completes when tenant is resolved.
- Missing optional call-log tables are skipped without crashing the live call.

Failure symptoms:

- `tenant.resolve.error`.
- `tenant.resolve.not_found`.
- `config_source=safe_fallback` when DID should resolve.
- Postgres call-log dual-write skipped.

Rollback action:

- Redeploy with the corrected `DEFAULT_PHONE_NUMBER` and tenant row.
- Check tenant `phone_number` matches DID exactly.
- Check migration was applied.

---

## 8. Graceful Degradation Validation

Run:

```bash
python scripts/phase3c_failure_checks.py
```

Expected behavior:

- `postgres_unavailable.ok=true`.
- `missing_tenant_or_invalid_did.ok=true`.
- `missing_optional_integrations.ok=true`.
- `sms_failure.ok=true`.
- `calcom_failure.ok=true`.

Failure symptoms:

- Missing optional integrations crash startup.
- Invalid DID raises instead of using the safe fallback.
- SMS provider exception escapes.
- Simulated Cal.com failure returns success.

Rollback action:

- Revert the current image.
- Keep optional integrations unset until fixed.
- Use `USE_POSTGRES=false` for live call testing.

---

## 9. Inbound Call Validation

Steps:

1. Call the staging Vobiz DID from a real phone.
2. Confirm LiveKit room is created.
3. Confirm the agent joins.
4. Wait for first line.
5. Speak a normal request.

Expected behavior:

- Caller hears the configured first line.
- Agent responds after caller speech.
- No extra silence beyond prior Phase 3B baseline.
- Logs show config source selection once at call start.

Failure symptoms:

- Caller hears silence.
- Agent never joins room.
- First line changed unexpectedly.
- Startup logs are green but LiveKit dispatch fails.

Rollback action:

- Set `USE_POSTGRES=false`.
- Confirm LiveKit dispatch settings.
- Roll back image if Phase 3B worked with the same env vars.

---

## 10. Multilingual Switching Validation

Steps:

1. Start the call in Hindi or Hinglish.
2. Switch to English mid-call.
3. Switch to one regional language configured in the language presets.

Expected behavior:

- Agent detects and mirrors the caller language.
- `lang_preset=multilingual` remains active unless tenant config intentionally overrides it.
- No extra LLM calls are added.

Failure symptoms:

- Agent replies only in English.
- Language changes reset conversation context.
- Latency spikes after switching languages.

Rollback action:

- Compare `lang_preset`, `tts_language`, and `tts_voice` between Postgres config and `config.json`.
- If Postgres config is wrong, set `USE_POSTGRES=false`.

---

## 11. Interruption Handling Validation

Steps:

1. Let the agent begin speaking.
2. Interrupt naturally before the sentence ends.
3. Repeat once in a noisy environment.

Expected behavior:

- Agent stops speaking immediately.
- New caller utterance is handled normally.
- Logs show interruption count, not transcript content.

Failure symptoms:

- Agent finishes its sentence after interruption.
- TTS audio overlaps caller speech.
- Logs show raw transcript text.

Rollback action:

- Roll back image immediately if barge-in regresses.
- Do not patch forward on the live path.

---

## 12. Booking Flow Validation

Steps:

1. Ask for an appointment.
2. Provide date, time, name, phone, and email if prompted.
3. Confirm the booking.
4. Hang up normally.

Expected behavior:

- `save_booking_intent` runs during call.
- Cal.com booking creation happens post-call in shutdown hook.
- Caller gets normal confirmation behavior.
- Supabase call log is saved.
- Optional Postgres call-log dual-write happens only after call end.

Failure symptoms:

- Booking tool blocks the live turn.
- Shutdown hook crashes before Supabase log.
- Cal.com error crashes the call.

Rollback action:

- Keep Supabase and `config.json` active.
- Set `USE_POSTGRES=false` if dual-write causes errors.
- Validate Cal.com credentials outside the call.

---

## 13. Transfer Flow Validation

Steps:

1. During the call, ask for a human.
2. Confirm SIP transfer is attempted.
3. Confirm destination phone rings or transfer fails gracefully.

Expected behavior:

- Existing SIP transfer call path is unchanged.
- Destination URI comes from existing transfer config.
- Failure returns a spoken fallback, not a crash.

Failure symptoms:

- Tool is not called.
- SIP REFER target is malformed.
- Room disconnects unexpectedly.

Rollback action:

- Restore `DEFAULT_TRANSFER_NUMBER` and `VOBIZ_SIP_DOMAIN`.
- Set `USE_POSTGRES=false` if tenant config transfer fields are suspect.
- Roll back image if transfer worked in Phase 3B.

---

## 14. Latency Instrumentation Validation

During a test call, inspect logs for:

```text
latency.stt_received
latency.stt
latency.llm
latency.tts
latency.silence_to_speech_estimate
```

Expected behavior:

- Logs appear once per relevant event or provider metric.
- Logs include `call_id`.
- Logs include `tenant_id` when resolved.
- No transcript text is logged.

Failure symptoms:

- Latency logs include raw caller speech.
- Latency logs are emitted in a tight loop.
- Silence-to-speech estimate is consistently above the target.

Rollback action:

- Roll back if instrumentation causes latency or logging volume issues.
- Keep operator validation notes for later analysis.

---

## 15. EasyPanel Deployment Validation

Expected EasyPanel settings:

- Build from Dockerfile.
- One container.
- Supervisor remains process manager.
- Route external health checks to `/health` on port `8000`.
- Keep env vars in EasyPanel, not in committed files.

Expected behavior:

- EasyPanel build succeeds.
- Container stays running.
- Health check passes every 30 seconds.
- Logs are visible from EasyPanel stdout/stderr.

Failure symptoms:

- EasyPanel restarts container repeatedly.
- `/health` is unreachable.
- Startup validation fails due missing env vars.

Rollback action:

- Redeploy previous image.
- Restore previous env vars.
- Disable Postgres with `USE_POSTGRES=false`.

---

## 16. Pass Criteria

Phase 3C staging validation passes only when:

- Docker build succeeds.
- Supervisor starts both processes.
- `/health` returns expected fields.
- Existing inbound calls work.
- Multilingual switching works.
- Interruption handling works.
- Transfer works.
- Booking works.
- `USE_POSTGRES=false` preserves old behavior.
- `USE_POSTGRES=true` works with seeded tenant config.
- Postgres failures degrade to config/Supabase fallback.
- Latency logs appear without transcript or secret leakage.
- EasyPanel deployment remains a single Supervisor-managed container.
