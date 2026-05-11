# RUNTIME_DEBUGGING.md

Real deployment troubleshooting checklist for the current runtime.

## Container Not Starting

- Check EasyPanel build logs.
- Confirm required env vars are present.
- Run `docker logs --tail=200 <container>`.
- Look for `startup.critical_env_missing`.
- Confirm the Dockerfile command starts Supervisor.

## Supervisor FATAL State

- Run `supervisorctl status` inside the container.
- Inspect logs for the specific failed program.
- Confirm `agent.py` can see LiveKit, OpenAI, and Sarvam env vars.
- Confirm `ui_server` can import the app and bind port `8000`.

## Missing Env Vars

- Health may show `startup_validation.status=missing_critical_env`.
- Add missing core env vars in EasyPanel.
- Restart the app.
- Re-check `/health` and Supervisor status.

## PostgreSQL Unavailable

- Check `USE_POSTGRES`; dashboard auth requires `true`.
- Verify `DATABASE_URL` is present and points at the intended EasyPanel PostgreSQL database.
- Query `/api/internal/runtime/auth` with `x-internal-token` and confirm `use_postgres=true`, `database_url_present=true`, and `postgres.postgres=ok`.
- Confirm the database accepts connections from the VPS.
- Add a short connection timeout to the connection string if startup is slow.

## LiveKit Connection Failure

- Verify `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`.
- Check LiveKit project status.
- Inspect agent logs for room/session errors.
- Confirm Vobiz routes to the correct LiveKit SIP setup.

## Sarvam Auth Failure

- Verify `SARVAM_API_KEY`.
- Check Sarvam account quota/status.
- Inspect STT/TTS initialization logs.
- Confirm failures do not crash Supervisor.

## OpenAI Auth Failure

- Verify `OPENAI_API_KEY`.
- Check account quota/project status.
- Inspect LLM error logs.
- Confirm the process stays running after provider errors.

## Health Endpoint Degraded

- Read every object under `checks`.
- `postgres=disabled` means auth, signup, and dashboard tenant APIs cannot run.
- `postgres=uninitialized` means `USE_POSTGRES=true` but the DB pool did not start.
- Missing core env vars must be fixed before staging validation.

## Transfer Failures

- Verify `DEFAULT_TRANSFER_NUMBER`.
- Verify `VOBIZ_SIP_DOMAIN`.
- Inspect transfer tool logs and LiveKit SIP logs.
- Confirm caller hears a fallback instead of silence.

## Booking Failures

- Verify `CAL_API_KEY`.
- Verify `CAL_EVENT_TYPE_ID`.
- Check Cal.com event availability.
- Inspect booking logs for status code and fallback message.

## High Latency

- Inspect `latency.silence_to_speech_estimate`.
- Inspect `latency.stt`, `latency.llm`, and `latency.tts`.
- Compare normal calls, noisy calls, and multilingual calls.
- Do not optimize during validation; capture observations first.

## Multilingual Switching Failure

- Capture the caller language sequence.
- Inspect STT language fields where available.
- Inspect latency before and after switching.
- Note whether the failure is STT recognition, LLM language choice, or TTS output.
