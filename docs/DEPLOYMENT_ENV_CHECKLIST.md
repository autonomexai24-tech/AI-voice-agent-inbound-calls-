# DEPLOYMENT_ENV_CHECKLIST.md

Use EasyPanel environment variables. Do not paste secrets into docs, screenshots, chat, or logs.

## LiveKit

| Variable | Status | Notes |
|---|---|---|
| `LIVEKIT_URL` | Required | LiveKit Cloud websocket URL. |
| `LIVEKIT_API_KEY` | Required | Required by agent startup and LiveKit API calls. |
| `LIVEKIT_API_SECRET` | Required | Required by agent startup and LiveKit API calls. |

## OpenAI

| Variable | Status | Notes |
|---|---|---|
| `OPENAI_API_KEY` | Required | Required by startup validation and LLM calls. |

## Sarvam

| Variable | Status | Notes |
|---|---|---|
| `SARVAM_API_KEY` | Required | Required by startup validation and STT/TTS calls. |

## PostgreSQL

| Variable | Status | Notes |
|---|---|---|
| `USE_POSTGRES` | Required mode flag | Set `false` for legacy fallback mode; set `true` for PostgreSQL coexistence validation. |
| `DATABASE_URL` | Required when `USE_POSTGRES=true` | Add a short connection timeout in the URL if supported by the provider. |
| `POSTGRES_POOL_MIN` | Optional | Defaults to `1`. |
| `POSTGRES_POOL_MAX` | Optional | Defaults to `10`. |

## Vobiz

| Variable | Status | Notes |
|---|---|---|
| `VOBIZ_SIP_DOMAIN` | Optional | Required for transfer URI construction. |
| `DEFAULT_TRANSFER_NUMBER` | Optional | Required for human transfer. |
| `VOBIZ_USERNAME` | Optional | Used by trunk setup tooling, not required by normal container startup. |
| `VOBIZ_PASSWORD` | Optional | Used by trunk setup tooling, not required by normal container startup. |
| `VOBIZ_OUTBOUND_NUMBER` | Optional | Used by trunk setup tooling, not required by normal container startup. |

## Cal.com

| Variable | Status | Notes |
|---|---|---|
| `CAL_API_KEY` | Required for booking | Missing or invalid value should produce booking fallback, not crash live calls. |
| `CAL_EVENT_TYPE_ID` | Required for booking | Must match the Cal.com event type used for appointments. |

## Fast2SMS

| Variable | Status | Notes |
|---|---|---|
| `FAST2SMS_API_KEY` | Optional / fallback-supported | Current runtime treats SMS failure as non-fatal. Validate during notification testing when Fast2SMS is wired. |
