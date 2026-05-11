import os
import json
import logging
import pytz
import re
import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Annotated, Any
from dotenv import load_dotenv

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.getLogger("hpack").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

load_dotenv()
from backend.logging import configure_logging, get_logger, init_sentry, set_correlation_context

configure_logging("voice-agent")
init_sentry("voice-agent")
logger = get_logger("voice-agent")

from livekit import api
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    WorkerOptions,
    cli,
    llm,
)
from livekit.plugins import openai, sarvam, silero

SUPPORTED_SARVAM_LANGUAGES = {"en-IN", "hi-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN"}
SAFE_FALLBACK_FIRST_LINE = "We are unable to load this number's configuration right now. Please call again later."
LANGUAGE_SWITCH_MIN_CONFIDENCE_CHARS = 2

# ── Rate limiting (#37) ───────────────────────────────────────────────────────
_call_timestamps: dict = defaultdict(list)
RATE_LIMIT_CALLS  = 5
RATE_LIMIT_WINDOW = 3600  # 1 hour

def is_rate_limited(phone: str) -> bool:
    if phone in ("unknown", "demo"):
        return False
    now = time.time()
    _call_timestamps[phone] = [t for t in _call_timestamps[phone] if now - t < RATE_LIMIT_WINDOW]
    if len(_call_timestamps[phone]) >= RATE_LIMIT_CALLS:
        return True
    _call_timestamps[phone].append(now)
    return False


# ── Token counter (#11) ───────────────────────────────────────────────────────
def count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model("gpt-4o")
        return len(enc.encode(text))
    except Exception:
        return len(text.split())


# ── External imports ───────────────────────────────────────────────────────────
from calendar_tools import get_available_slots, create_booking, cancel_booking

# Phase 3A: pure helpers now live in backend/voice/. Behavior is byte-identical
# to the previous inline definitions; see docs/PHASE3A_REPORT.md.
from backend.voice.language import LANGUAGE_PRESETS, get_language_instruction
from backend.voice.prompts import get_ist_time_context
from backend.voice.transfer import build_sip_transfer_uri
from backend.core.config_resolver import resolve_runtime_config_async
from backend.core.startup import run_startup_checks
from backend.db.call_logs import insert_call_log
from backend.db.connection import is_postgres_enabled
from backend.integrations.storage import S3StorageProvider, build_recording_storage_key
from backend.services.notification_service import send_booking_confirmation_sms
from backend.services.recording_service import record_livekit_upload_metadata
from backend.utils.formatting import mask_phone


# ══════════════════════════════════════════════════════════════════════════════
# TOOL CONTEXT — All AI-callable functions
# ══════════════════════════════════════════════════════════════════════════════

class AgentTools(llm.ToolContext):

    def __init__(
        self,
        caller_phone: str,
        caller_name: str = "",
        *,
        transfer_number: str | None = None,
        sip_domain: str | None = None,
        cal_api_key: str | None = None,
        cal_event_type_id: str | int | None = None,
        business_hours: dict | None = None,
        call_id: str | None = None,
        tenant_id: str | None = None,
        did: str | None = None,
    ):
        super().__init__(tools=[])
        self.caller_phone        = caller_phone
        self.caller_name         = caller_name
        self.booking_intent: dict | None = None
        self.sip_domain          = sip_domain or os.getenv("VOBIZ_SIP_DOMAIN")
        self.transfer_number     = transfer_number or os.getenv("DEFAULT_TRANSFER_NUMBER")
        self.cal_api_key         = cal_api_key
        self.cal_event_type_id   = cal_event_type_id
        self.business_hours      = business_hours if isinstance(business_hours, dict) else None
        self.call_id             = call_id
        self.tenant_id           = tenant_id
        self.did_masked          = mask_phone(did) if did else None
        self.ctx_api             = None
        self.room_name           = None
        self._sip_identity       = None

    def _log_extra(self, **extra):
        base = {"call_id": self.call_id, "tenant_id": self.tenant_id, "did": self.did_masked}
        base.update(extra)
        return {k: v for k, v in base.items() if v is not None}

    # ── Tool: Transfer to Human ───────────────────────────────────────────
    @llm.function_tool(description="Transfer this call to a human agent. Use if: caller asks for human, is angry, or query is outside scope.")
    async def transfer_call(self) -> str:
        logger.info("[TOOL] transfer_call triggered", extra=self._log_extra())
        destination = build_sip_transfer_uri(self.transfer_number, self.sip_domain)
        try:
            if self.ctx_api and self.room_name and destination and self._sip_identity:
                await self.ctx_api.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.room_name,
                        participant_identity=self._sip_identity,
                        transfer_to=destination,
                        play_dialtone=False,
                    )
                )
                logger.info("[TOOL] transfer_call completed", extra=self._log_extra())
                return "Transfer initiated successfully."
            logger.warning("[TOOL] transfer_call unavailable", extra=self._log_extra(has_destination=bool(destination)))
            return "Unable to transfer right now."
        except Exception as e:
            logger.error("[TOOL] transfer_call failed", extra=self._log_extra(error_type=type(e).__name__))
            return "Unable to transfer right now."

    # ── Tool: End Call ────────────────────────────────────────────────────
    @llm.function_tool(description="End the call. Use ONLY when caller says bye/goodbye or after booking is fully confirmed.")
    async def end_call(self) -> str:
        logger.info("[TOOL] end_call triggered", extra=self._log_extra())
        try:
            if self.ctx_api and self.room_name and self._sip_identity:
                await self.ctx_api.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.room_name,
                        participant_identity=self._sip_identity,
                        transfer_to="tel:+00000000",
                        play_dialtone=False,
                    )
                )
        except Exception as e:
            logger.warning("[END-CALL] SIP hangup failed", extra=self._log_extra(error_type=type(e).__name__))
        return "Call ended."

    # ── Tool: Save Booking Intent ─────────────────────────────────────────
    @llm.function_tool(description="Save booking intent after caller confirms appointment. Call this ONCE after you have name, phone, email, date, time.")
    async def save_booking_intent(
        self,
        start_time:  Annotated[str,  "ISO 8601 datetime e.g. '2026-03-01T10:00:00+05:30'"],
        caller_name: Annotated[str,  "Full name of the caller"],
        caller_phone:Annotated[str,  "Phone number of the caller"],
        notes:       Annotated[str,  "Any notes, email, or special requests"] = "",
    ) -> str:
        logger.info(
            "[TOOL] save_booking_intent",
            extra={
                **self._log_extra(),
                "caller_phone_masked": mask_phone(caller_phone),
                "has_caller_name": bool(caller_name),
                "start_time_present": bool(start_time),
            },
        )
        try:
            self.booking_intent = {
                "start_time":   start_time,
                "caller_name":  caller_name,
                "caller_phone": caller_phone,
                "notes":        notes,
            }
            self.caller_name = caller_name
            return f"Booking intent saved for {caller_name} at {start_time}. I'll confirm after the call."
        except Exception as e:
            logger.error("[TOOL] save_booking_intent failed", extra=self._log_extra(error_type=type(e).__name__))
            return "I had trouble saving the booking. Please try again."

    # ── Tool: Check Availability (#13) ────────────────────────────────────
    @llm.function_tool(description="Check available appointment slots for a given date. Call this when user asks about availability.")
    async def check_availability(
        self,
        date: Annotated[str, "Date to check in YYYY-MM-DD format e.g. '2026-03-01'"],
    ) -> str:
        logger.info("[TOOL] check_availability", extra=self._log_extra(date=date))
        try:
            slots = await asyncio.to_thread(
                get_available_slots,
                date,
                cal_api_key=self.cal_api_key,
                cal_event_type_id=self.cal_event_type_id,
            )
            if not slots:
                return f"No available slots on {date}. Would you like to check another date?"
            slot_strings = [s.get("start_time", str(s))[-8:][:5] for s in slots[:6]]
            return f"Available slots on {date}: {', '.join(slot_strings)} IST."
        except Exception as e:
            logger.error("[TOOL] check_availability failed", extra=self._log_extra(error_type=type(e).__name__))
            return "I'm having trouble checking the calendar right now."

    # ── Tool: Business Hours (#31) ────────────────────────────────────────
    @llm.function_tool(description="Check if the business is currently open and what the operating hours are.")
    async def get_business_hours(self) -> str:
        ist  = pytz.timezone("Asia/Kolkata")
        now  = datetime.now(ist)
        configured = self._configured_business_hours(now)
        if configured:
            return configured

        hours = {
            0: ("Monday",    "10:00", "19:00"),
            1: ("Tuesday",   "10:00", "19:00"),
            2: ("Wednesday", "10:00", "19:00"),
            3: ("Thursday",  "10:00", "19:00"),
            4: ("Friday",    "10:00", "19:00"),
            5: ("Saturday",  "10:00", "17:00"),
            6: ("Sunday",    None,    None),
        }
        day_name, open_t, close_t = hours[now.weekday()]
        current_time = now.strftime("%H:%M")
        if open_t is None:
            return "We are closed on Sundays. Next opening: Monday 10:00 AM IST."
        if open_t <= current_time <= close_t:
            return f"We are OPEN. Today ({day_name}): {open_t}–{close_t} IST."
        return f"We are CLOSED. Today ({day_name}): {open_t}–{close_t} IST."

    def _configured_business_hours(self, now: datetime) -> str | None:
        if not self.business_hours:
            return None
        day_key = now.strftime("%A").lower()
        day_config = self.business_hours.get(day_key) or self.business_hours.get(day_key[:3])
        if not isinstance(day_config, dict):
            return None
        if day_config.get("closed") is True:
            return f"We are CLOSED today ({now.strftime('%A')})."
        open_t = day_config.get("open") or day_config.get("start")
        close_t = day_config.get("close") or day_config.get("end")
        if not open_t or not close_t:
            return None
        current_time = now.strftime("%H:%M")
        if str(open_t) <= current_time <= str(close_t):
            return f"We are OPEN. Today ({now.strftime('%A')}): {open_t}-{close_t} IST."
        return f"We are CLOSED. Today ({now.strftime('%A')}): {open_t}-{close_t} IST."


# ══════════════════════════════════════════════════════════════════════════════
# AGENT CLASS
# ══════════════════════════════════════════════════════════════════════════════

class ReceptionistAssistant(Agent):

    def __init__(self, agent_tools: AgentTools, first_line: str = "", live_config: dict | None = None):
        tools = llm.find_function_tools(agent_tools)
        self._agent_tools = agent_tools
        self._first_line  = first_line
        self._live_config = live_config or {}
        live_config_loaded = self._live_config

        base_instructions = live_config_loaded.get("agent_instructions", "")
        ist_context       = get_ist_time_context()
        lang_preset       = live_config_loaded.get("lang_preset", "multilingual")
        lang_instruction  = get_language_instruction(lang_preset)
        final_instructions = base_instructions + ist_context + lang_instruction

        # Token counter (#11)
        token_count = count_tokens(final_instructions)
        logger.info(f"[PROMPT] System prompt: {token_count} tokens")
        if token_count > 600:
            logger.warning(f"[PROMPT] Prompt exceeds 600 tokens — consider trimming for latency")

        super().__init__(instructions=final_instructions, tools=tools)

    async def on_enter(self):
        greeting = self._live_config.get(
            "first_line",
            self._first_line or SAFE_FALLBACK_FIRST_LINE,
        )
        await self.session.say(greeting, allow_interruptions=True)
        logger.info(
            "[GREETING] Tenant greeting sent",
            extra={
                "call_id": self._agent_tools.call_id,
                "tenant_id": self._agent_tools.tenant_id,
                "did": self._agent_tools.did_masked,
            },
        )
        if self._live_config.get("_terminate_after_first_line"):
            asyncio.create_task(self._end_after_first_line())

    async def _end_after_first_line(self):
        await asyncio.sleep(4)
        await self._agent_tools.end_call()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRYPOINT
# ══════════════════════════════════════════════════════════════════════════════

agent_is_speaking = False

async def entrypoint(ctx: JobContext):
    global agent_is_speaking

    # ── Connect ───────────────────────────────────────────────────────────
    await ctx.connect()
    if not ctx.room.remote_participants:
        try:
            await asyncio.wait_for(ctx.wait_for_participant(), timeout=2.0)
        except asyncio.TimeoutError:
            logger.warning("[ROOM] No remote participant before config resolution", extra={"call_id": ctx.room.name})
    call_id = ctx.room.name
    set_correlation_context(call_id=call_id)
    logger.info("[ROOM] Connected", extra={"call_id": call_id})

    # ── Extract caller info ───────────────────────────────────────────────
    phone_number = None
    caller_name  = ""

    # Try metadata first (LiveKit dispatch metadata)
    metadata = ctx.job.metadata or ""
    meta = _parse_json_object(metadata)
    room_meta = _parse_json_object(getattr(ctx.room, "metadata", "") or "")

    # Extract from SIP participants
    phone_number, caller_name = _extract_caller_info(ctx, meta, room_meta)
    if caller_name:
        logger.info("[CALLER-ID] Name present from SIP", extra={"call_id": call_id})
    caller_phone = phone_number or "unknown"
    dialed_did = _extract_dialed_did(ctx, meta)
    _log_inbound_sip_metadata(
        ctx,
        job_metadata=metadata,
        parsed_job_metadata=meta,
        parsed_room_metadata=room_meta,
        caller_phone=caller_phone,
        dialed_did=dialed_did,
    )
    if dialed_did:
        logger.info(
            "[TENANT] DID resolved for tenant lookup",
            extra={"call_id": call_id, "did_masked": mask_phone(dialed_did)},
        )

    # ── Rate limiting (#37) ───────────────────────────────────────────────
    if is_rate_limited(caller_phone):
        logger.warning(
            "[RATE-LIMIT] Blocked caller",
            extra={"call_id": call_id, "caller_phone_masked": mask_phone(caller_phone)},
        )
        return

    # ── Load config ───────────────────────────────────────────────────────
    resolved_config = await resolve_runtime_config_async(caller_phone=caller_phone, did=dialed_did)
    live_config   = resolved_config.config
    tenant_id     = resolved_config.tenant_id
    did_masked    = mask_phone(dialed_did) if dialed_did else None
    set_correlation_context(call_id=call_id, tenant_id=str(tenant_id or ""), did=did_masked or "")
    tenant_unavailable = (
        is_postgres_enabled()
        and not tenant_id
        and resolved_config.fallback_reason
        in {"tenant_not_found", "tenant_not_configured", "postgres_error", "did_missing", "postgres_unavailable_or_unconfigured"}
    )
    postgres_tenant_runtime = is_postgres_enabled() and (bool(tenant_id) or tenant_unavailable)
    if tenant_unavailable:
        if resolved_config.fallback_reason == "tenant_not_configured":
            fallback_line = "This number is not configured. Please contact support."
        else:
            fallback_line = SAFE_FALLBACK_FIRST_LINE
        live_config["agent_instructions"] = (
            "This inbound call cannot be mapped to an active tenant configuration. "
            "Do not answer business questions. Say the configured fallback message once and end the call."
        )
        live_config["first_line"] = fallback_line
        live_config["_terminate_after_first_line"] = True
        live_config["max_turns"] = 1
        logger.warning(
            "tenant.config.unavailable",
            extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked},
        )
    logger.info(
        "config.runtime.selected",
        extra={
            "call_id": call_id,
            "tenant_id": tenant_id,
            "did": did_masked,
            "config_source": resolved_config.source,
            "fallback_reason": resolved_config.fallback_reason,
        },
    )
    delay_setting = live_config.get("stt_min_endpointing_delay", 0.2)
    llm_model     = live_config.get("llm_model", "gpt-4o-mini")
    llm_provider  = live_config.get("llm_provider", "openai")
    tts_voice     = live_config.get("tts_voice", "kavya")
    configured_language = _resolve_configured_language(live_config)
    tts_language  = configured_language
    tts_provider  = live_config.get("tts_provider", "sarvam")
    stt_provider  = live_config.get("stt_provider", "sarvam")
    stt_language  = _resolve_stt_language(live_config, configured_language)
    max_turns     = live_config.get("max_turns", 25)
    live_config["language"] = configured_language
    live_config["tts_language"] = tts_language
    live_config["stt_language"] = stt_language
    logger.info(
        "tenant.runtime.loaded",
        extra={
            "call_id": call_id,
            "tenant_id": tenant_id,
            "tenant_name": live_config.get("_tenant_name"),
            "tenant_slug": live_config.get("_tenant_slug"),
            "did": did_masked,
            "language": configured_language,
            "llm_model": llm_model,
            "voice": tts_voice,
            "config_source": resolved_config.source,
        },
    )
    logger.info(
        "inbound.call.started",
        extra={
            "call_id": call_id,
            "tenant_id": tenant_id,
            "tenant_name": live_config.get("_tenant_name"),
            "did": did_masked,
            "caller_phone_masked": mask_phone(caller_phone),
            "language": configured_language,
            "llm_model": llm_model,
            "voice": tts_voice,
        },
    )
    logger.info(
        "latency.call_config",
        extra={
            "call_id": call_id,
            "tenant_id": tenant_id,
            "did": did_masked,
            "stt_provider": stt_provider,
            "stt_language": stt_language,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "tts_provider": tts_provider,
            "tts_language": tts_language,
            "endpointing_delay": float(delay_setting),
        },
    )

    # ── Caller memory (#15) ───────────────────────────────────────────────
    async def get_caller_history(phone: str) -> str:
        if tenant_unavailable:
            return ""
        if phone == "unknown":
            return ""
        if tenant_id and is_postgres_enabled():
            try:
                from uuid import UUID as _UUID
                from backend.db.call_logs import get_latest_call_for_phone

                last = await asyncio.to_thread(
                    get_latest_call_for_phone,
                    _UUID(str(tenant_id)),
                    phone,
                )
                if last:
                    created_at = str(last.get("created_at") or "")[:10]
                    return f"\n\n[CALLER HISTORY: Last call {created_at}. Summary: {last.get('summary') or ''}]"
            except Exception as e:
                logger.warning(
                    "[MEMORY] Could not load tenant caller history",
                    extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked, "error_type": type(e).__name__},
                )
                return ""
        return ""

    caller_history = await get_caller_history(caller_phone)
    if caller_history:
        logger.info(
            "[MEMORY] Loaded caller history",
            extra={"call_id": call_id, "caller_phone_masked": mask_phone(caller_phone)},
        )
        # Append to live_config instructions
        live_config["agent_instructions"] = (live_config.get("agent_instructions","") + caller_history)

    # ── Instantiate tools ─────────────────────────────────────────────────
    agent_tools = AgentTools(
        caller_phone=caller_phone,
        caller_name=caller_name,
        transfer_number=live_config.get("transfer_number") or os.getenv("DEFAULT_TRANSFER_NUMBER"),
        sip_domain=os.getenv("VOBIZ_SIP_DOMAIN"),
        cal_api_key=live_config.get("cal_api_key"),
        cal_event_type_id=live_config.get("cal_event_type_id"),
        business_hours=_coerce_business_hours(live_config.get("business_hours_json")),
        call_id=call_id,
        tenant_id=tenant_id,
        did=dialed_did,
    )
    agent_tools._sip_identity = (
        f"sip_{caller_phone.replace('+','')}" if phone_number else "inbound_caller"
    )
    agent_tools.ctx_api   = ctx.api
    agent_tools.room_name = ctx.room.name

    # ── Build LLM (#8 Groq support) ───────────────────────────────────────
    if llm_provider == "groq":
        agent_llm = openai.LLM.with_groq(
            model=llm_model or "llama-3.3-70b-versatile",
            max_completion_tokens=120,
        )
        logger.info(f"[LLM] Using Groq: {llm_model}")
    elif llm_provider == "claude":
        # Claude Haiku 3.5 via Anthropic API (#27)
        _anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        agent_llm = openai.LLM(
            model=llm_model or "claude-haiku-3-5-latest",
            base_url="https://api.anthropic.com/v1/",
            api_key=_anthropic_key,
            max_completion_tokens=120,
        )
        logger.info(f"[LLM] Using Claude via Anthropic: {llm_model}")
    else:
        agent_llm = openai.LLM(model=llm_model, max_completion_tokens=120)  # cap tokens (#7)
        logger.info(f"[LLM] Using OpenAI: {llm_model}")
    logger.info(
        "openai.initialized" if llm_provider == "openai" else "llm.initialized",
        extra={
            "call_id": call_id,
            "tenant_id": tenant_id,
            "did": did_masked,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
        },
    )

    # ── Build STT (#1 16kHz, #20 auto-detect, #9 Deepgram) ──────────────
    actual_stt_provider = stt_provider
    if stt_provider == "deepgram":
        try:
            from livekit.plugins import deepgram
            agent_stt = deepgram.STT(
                model="nova-2-general",
                language="multi",        # multilingual mode
                interim_results=False,
            )
            logger.info("[STT] Using Deepgram Nova-2")
        except ImportError:
            logger.warning("[STT] deepgram plugin not installed — falling back to Sarvam")
            actual_stt_provider = "sarvam"
            agent_stt = sarvam.STT(
                language=stt_language,
                model="saaras:v3",
                mode="transcribe",
                flush_signal=True,
                sample_rate=16000,
            )
    else:
        agent_stt = sarvam.STT(
            language=stt_language,      # "unknown" = auto-detect (#20)
            model="saaras:v3",
            mode="transcribe",
            flush_signal=True,
            sample_rate=16000,          # force 16kHz (#1)
        )
        actual_stt_provider = "sarvam"
        logger.info("[STT] Using Sarvam Saaras v3", extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked, "language": stt_language})

    # ── Build TTS (#2 24kHz, #10 ElevenLabs) ────────────────────────────
    actual_tts_provider = tts_provider
    if tts_provider == "elevenlabs":
        try:
            from livekit.plugins import elevenlabs
            _el_voice_id = live_config.get("elevenlabs_voice_id", "21m00Tcm4TlvDq8ikWAM")
            agent_tts = elevenlabs.TTS(
                model="eleven_turbo_v2_5",
                voice_id=_el_voice_id,
            )
            logger.info(f"[TTS] Using ElevenLabs Turbo v2.5 — voice: {_el_voice_id}")
        except ImportError:
            logger.warning("[TTS] elevenlabs plugin not installed — falling back to Sarvam")
            actual_tts_provider = "sarvam"
            agent_tts = sarvam.TTS(
                target_language_code=tts_language,
                model="bulbul:v3",
                speaker=tts_voice,
                speech_sample_rate=24000,
            )
    else:
        agent_tts = sarvam.TTS(
            target_language_code=tts_language,
            model="bulbul:v3",
            speaker=tts_voice,
            speech_sample_rate=24000,          # force 24kHz (#2)
        )
        actual_tts_provider = "sarvam"
        logger.info(
            "[TTS] Using Sarvam Bulbul v3",
            extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked, "voice": tts_voice, "language": tts_language},
        )
    if actual_stt_provider == "sarvam" or actual_tts_provider == "sarvam":
        logger.info(
            "sarvam.initialized",
            extra={
                "call_id": call_id,
                "tenant_id": tenant_id,
                "did": did_masked,
                "stt_provider": actual_stt_provider,
                "stt_language": stt_language,
                "tts_provider": actual_tts_provider,
                "tts_language": tts_language,
                "voice": tts_voice,
            },
        )

    # ── Sentence chunker (keep responses short for voice) ─────────────────
    def before_tts_cb(agent_response: str) -> str:
        sentences = re.split(r'(?<=[।.!?])\s+', agent_response.strip())
        return sentences[0] if sentences else agent_response

    # ── Turn counter + auto-close (#29) ──────────────────────────────────
    turn_count    = 0
    interrupt_count = 0  # (#30)
    last_user_speech_at: float | None = None
    current_language = tts_language
    language_switch_enabled = _language_switch_enabled(live_config)

    def maybe_switch_language(candidate: Any, *, reason: str, transcript: str | None = None):
        nonlocal current_language, stt_language, tts_language
        if not language_switch_enabled:
            return
        detected_language = _normalize_detected_language(candidate, transcript=transcript)
        if not detected_language or detected_language == current_language:
            return
        previous_language = current_language
        current_language = detected_language
        stt_language = detected_language
        tts_language = detected_language
        live_config["detected_language"] = detected_language
        live_config["caller_language"] = detected_language
        if stt_provider == "sarvam":
            _switch_sarvam_stt_language(agent_stt, detected_language)
        if tts_provider == "sarvam":
            _switch_sarvam_tts_language(agent_tts, detected_language)
        logger.info(
            "language.switch",
            extra={
                "call_id": call_id,
                "tenant_id": tenant_id,
                "did": did_masked,
                "from_language": previous_language,
                "to_language": detected_language,
                "reason": reason,
            },
        )

    # ── Build agent ───────────────────────────────────────────────────────
    agent = ReceptionistAssistant(
        agent_tools=agent_tools,
        first_line=live_config.get("first_line", ""),
        live_config=live_config,
    )

    # ── Build session (#3 noise cancellation attempted) ───────────────────
    try:
        from livekit.agents import noise_cancellation as nc
        _noise_cancel = nc.BVC()
        logger.info("[AUDIO] BVC noise cancellation enabled")
    except Exception:
        _noise_cancel = None
        logger.info("[AUDIO] BVC not available — running without noise cancellation")

    room_input = RoomInputOptions(close_on_disconnect=False)
    if _noise_cancel:
        try:
            room_input = RoomInputOptions(close_on_disconnect=False, noise_cancellation=_noise_cancel)
        except Exception:
            room_input = RoomInputOptions(close_on_disconnect=False)

    session = AgentSession(
        stt=agent_stt,
        llm=agent_llm,
        tts=agent_tts,
        turn_detection="stt",
        min_endpointing_delay=float(delay_setting),  # 0.05 default (#6)
        allow_interruptions=True,
    )

    await session.start(room=ctx.room, agent=agent, room_input_options=room_input)

    # ── TTS pre-warm (#12) ────────────────────────────────────────────────
    try:
        await session.tts.prewarm()
        logger.info("[TTS] Pre-warmed successfully")
    except Exception as e:
        logger.debug(f"[TTS] Pre-warm skipped: {e}")

    logger.info("[AGENT] Session live - waiting for caller audio.", extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked})
    call_start_time = datetime.now()

    # ── Recording → S3-Compatible Storage ────────────────────────────────
    egress_id = None
    recording_storage_key = build_recording_storage_key(
        tenant_id=str(tenant_id) if tenant_id else "legacy",
        call_id=call_id,
        filename="recording.ogg",
    )
    recording_object_url = ""
    recording_upload_status = "pending"
    recording_file_size = None
    recording_duration_seconds = None
    try:
        storage = S3StorageProvider()
        if not storage.configured:
            raise RuntimeError("s3_storage_not_configured")
        rec_api = api.LiveKitAPI(
            url=os.environ["LIVEKIT_URL"],
            api_key=os.environ["LIVEKIT_API_KEY"],
            api_secret=os.environ["LIVEKIT_API_SECRET"],
        )
        egress_resp = await rec_api.egress.start_room_composite_egress(
            api.RoomCompositeEgressRequest(
                room_name=ctx.room.name,
                audio_only=True,
                file_outputs=[api.EncodedFileOutput(
                    file_type=api.EncodedFileType.OGG,
                    filepath=recording_storage_key,
                    s3=api.S3Upload(**storage.livekit_s3_config())
                )]
            )
        )
        egress_id = egress_resp.egress_id
        recording_object_url = storage.object_url(recording_storage_key)
        await rec_api.aclose()
        logger.info(
            "recording.egress.started",
            extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked, "egress_id": egress_id},
        )
    except Exception as e:
        recording_upload_status = "failed"
        logger.warning(
            "recording.egress.start_failed",
            extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked, "error_type": type(e).__name__},
        )

    # ── Upsert active_calls (#38) ─────────────────────────────────────────
    async def upsert_active_call(status: str):
        return

    await upsert_active_call("active")

    # ── Real-time transcript streaming (#33) ─────────────────────────────
    async def _log_transcript(role: str, content: str):
        return

    # ── Session event handlers ────────────────────────────────────────────
    @session.on("agent_speech_started")
    def _agent_speech_started(ev):
        nonlocal last_user_speech_at
        global agent_is_speaking
        agent_is_speaking = True
        if last_user_speech_at is not None:
            logger.info(
                "latency.silence_to_speech_estimate",
                extra={
                    "call_id": call_id,
                    "tenant_id": tenant_id,
                    "did": did_masked,
                    "turn_count": turn_count,
                    "latency_ms": round((time.perf_counter() - last_user_speech_at) * 1000, 2),
                },
            )
            last_user_speech_at = None

    @session.on("agent_speech_finished")
    def _agent_speech_finished(ev):
        global agent_is_speaking
        agent_is_speaking = False

    # Interrupt logging (#30)
    @session.on("agent_speech_interrupted")
    def _on_interrupted(ev):
        nonlocal interrupt_count
        interrupt_count += 1
        logger.info(
            "[INTERRUPT] Agent interrupted",
            extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked, "interrupt_count": interrupt_count},
        )

    @session.on("user_input_transcribed")
    def _on_user_input_transcribed(ev):
        if getattr(ev, "is_final", False):
            maybe_switch_language(getattr(ev, "language", None), reason="stt_event")
            logger.info(
                "latency.stt_received",
                extra={
                    "call_id": call_id,
                    "tenant_id": tenant_id,
                    "did": did_masked,
                    "turn_count": turn_count,
                    "stt_received_at": getattr(ev, "created_at", None),
                    "language": getattr(ev, "language", None),
                },
            )

    @session.on("metrics_collected")
    def _on_metrics_collected(ev):
        metrics = getattr(ev, "metrics", None)
        if metrics is None:
            return
        metric_type = getattr(metrics, "type", "")
        if metric_type == "stt_metrics":
            logger.info(
                "latency.stt",
                extra={
                    "call_id": call_id,
                    "tenant_id": tenant_id,
                    "did": did_masked,
                    "turn_count": turn_count,
                    "duration_ms": round(getattr(metrics, "duration", 0.0) * 1000, 2),
                    "audio_duration_ms": round(getattr(metrics, "audio_duration", 0.0) * 1000, 2),
                    "streamed": getattr(metrics, "streamed", None),
                },
            )
        elif metric_type == "llm_metrics":
            logger.info(
                "latency.llm",
                extra={
                    "call_id": call_id,
                    "tenant_id": tenant_id,
                    "did": did_masked,
                    "turn_count": turn_count,
                    "ttft_ms": round(getattr(metrics, "ttft", 0.0) * 1000, 2),
                    "duration_ms": round(getattr(metrics, "duration", 0.0) * 1000, 2),
                    "cancelled": getattr(metrics, "cancelled", None),
                },
            )
        elif metric_type == "tts_metrics":
            logger.info(
                "latency.tts",
                extra={
                    "call_id": call_id,
                    "tenant_id": tenant_id,
                    "did": did_masked,
                    "turn_count": turn_count,
                    "ttfb_ms": round(getattr(metrics, "ttfb", 0.0) * 1000, 2),
                    "duration_ms": round(getattr(metrics, "duration", 0.0) * 1000, 2),
                    "audio_duration_ms": round(getattr(metrics, "audio_duration", 0.0) * 1000, 2),
                    "cancelled": getattr(metrics, "cancelled", None),
                    "streamed": getattr(metrics, "streamed", None),
                },
            )

    FILLER_WORDS = {
        "okay.", "okay", "ok", "uh", "hmm", "hm", "yeah", "yes",
        "no", "um", "ah", "oh", "right", "sure", "fine", "good",
        "haan", "han", "theek", "theek hai", "accha", "ji", "ha",
    }

    @session.on("user_speech_committed")
    def on_user_speech_committed(ev):
        nonlocal last_user_speech_at, turn_count
        global agent_is_speaking

        transcript = ev.user_transcript.strip()
        transcript_lower = transcript.lower().rstrip(".")

        if not transcript or len(transcript) < 3:
            return
        if agent_is_speaking:
            # Short utterance during agent speech is usually echo (TTS audio
            # bleeding back into STT). Long utterances are real barge-in.
            # Threshold tunable via INTERRUPT_MIN_CHARS (default 8).
            interrupt_threshold = int(os.getenv("INTERRUPT_MIN_CHARS", "8"))
            if len(transcript) < interrupt_threshold:
                logger.debug(
                    "[FILTER-ECHO] Dropped short transcript during agent speech",
                    extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked, "len": len(transcript)},
                )
                return
            logger.info(
                "[FILTER-ECHO] Allowing long transcript as barge-in",
                extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked, "len": len(transcript)},
            )
            # fall through — process as real interruption
        if transcript_lower in FILLER_WORDS:
            logger.debug("[FILTER-FILLER] Dropped filler transcript", extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked})
            return

        maybe_switch_language(None, reason="transcript_script", transcript=transcript)

        # Real-time transcript stream
        asyncio.create_task(_log_transcript("user", transcript))

        # Turn counter + auto-close (#29)
        turn_count += 1
        last_user_speech_at = time.perf_counter()
        logger.info(
            "[TRANSCRIPT] User turn committed",
            extra={
                "call_id": call_id,
                "tenant_id": tenant_id,
                "did": did_masked,
                "turn_count": turn_count,
                "max_turns": max_turns,
                "transcript_chars": len(transcript),
            },
        )
        if turn_count >= max_turns:
            logger.info(f"[LIMIT] Reached {max_turns} turns — wrapping up")
            asyncio.create_task(
                session.generate_reply(
                    instructions="Politely wrap up: thank the caller, say they can call back anytime, and say a warm goodbye."
                )
            )

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        global agent_is_speaking
        logger.info("[HANGUP] Participant disconnected", extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked})
        agent_is_speaking = False
        asyncio.create_task(unified_shutdown_hook(ctx))

    # ══════════════════════════════════════════════════════════════════════
    # POST-CALL SHUTDOWN HOOK
    # ══════════════════════════════════════════════════════════════════════

    async def unified_shutdown_hook(shutdown_ctx: JobContext):
        nonlocal recording_upload_status, recording_file_size, recording_duration_seconds
        logger.info("[SHUTDOWN] Sequence started.", extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked})

        duration = int((datetime.now() - call_start_time).total_seconds())

        # Booking
        booking_status_msg = "No booking"
        booking_result = None
        if agent_tools.booking_intent:
            from calendar_tools import async_create_booking
            intent = agent_tools.booking_intent
            result = await async_create_booking(
                start_time=intent["start_time"],
                caller_name=intent["caller_name"] or "Unknown Caller",
                caller_phone=intent["caller_phone"],
                notes=intent["notes"],
                cal_api_key=live_config.get("cal_api_key"),
                cal_event_type_id=live_config.get("cal_event_type_id"),
            )
            booking_result = result
            if result.get("success"):
                sms_language = (
                    live_config.get("detected_language")
                    or live_config.get("caller_language")
                    or live_config.get("lang_preset")
                )
                if str(sms_language or "").lower() in ("", "auto", "multilingual", "unknown"):
                    sms_language = stt_language if stt_language != "unknown" else tts_language
                asyncio.create_task(send_booking_confirmation_sms(
                    tenant_id=tenant_id,
                    call_id=call_id,
                    caller_name=intent["caller_name"],
                    caller_phone=intent["caller_phone"],
                    booking_time_iso=intent["start_time"],
                    business_name=live_config.get("business_name") or live_config.get("_tenant_name") or "RapidX AI",
                    language=sms_language,
                    did=did_masked,
                ))
                logger.info(
                    "notification.sms.enqueued",
                    extra={
                        "call_id": call_id,
                        "tenant_id": tenant_id,
                        "did": did_masked,
                        "phone_masked": mask_phone(intent["caller_phone"]),
                    },
                )
                booking_status_msg = f"Booking Confirmed: {result.get('booking_id')}"
            else:
                booking_status_msg = f"Booking Failed: {result.get('message')}"
        else:
            logger.info(
                "notification.sms.skipped",
                extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked, "reason": "no_booking"},
            )

        # Build transcript
        transcript_text = ""
        try:
            messages = agent.chat_ctx.messages
            if callable(messages):
                messages = messages()
            lines = []
            for msg in messages:
                if getattr(msg, "role", None) in ("user", "assistant"):
                    content = getattr(msg, "content", "")
                    if isinstance(content, list):
                        content = " ".join(str(c) for c in content if isinstance(c, str))
                    lines.append(f"[{msg.role.upper()}] {content}")
            transcript_text = "\n".join(lines)
        except Exception as e:
            logger.error("[SHUTDOWN] Transcript read failed", extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked, "error_type": type(e).__name__})
            transcript_text = "unavailable"

        # Sentiment analysis (#14)
        sentiment = "unknown"
        if transcript_text and transcript_text != "unavailable":
            try:
                import openai as _oai
                _client = _oai.AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
                resp = await _client.chat.completions.create(
                    model="gpt-4o-mini", max_tokens=5,
                    messages=[{"role":"user","content":
                        f"Classify this call as one word: positive, neutral, negative, or frustrated.\n\n{transcript_text[:800]}"}]
                )
                sentiment = resp.choices[0].message.content.strip().lower()
                logger.info(f"[SENTIMENT] {sentiment}")
            except Exception as e:
                logger.warning("[SENTIMENT] Failed", extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked, "error_type": type(e).__name__})

        # Cost estimation (#34)
        def estimate_cost(dur: int, chars: int) -> float:
            return round(
                (dur / 60) * 0.002 +
                (dur / 60) * 0.006 +
                (chars / 1000) * 0.003 +
                (chars / 4000) * 0.0001,
                5
            )
        estimated_cost = estimate_cost(duration, len(transcript_text))
        logger.info(f"[COST] Estimated: ${estimated_cost}")

        # Analytics timestamps (#19)
        ist = pytz.timezone("Asia/Kolkata")
        call_dt = call_start_time.astimezone(ist)

        # Stop recording
        recording_url = recording_object_url
        if egress_id:
            try:
                stop_api = api.LiveKitAPI(
                    url=os.environ["LIVEKIT_URL"],
                    api_key=os.environ["LIVEKIT_API_KEY"],
                    api_secret=os.environ["LIVEKIT_API_SECRET"],
                )
                egress_info = await stop_api.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
                await stop_api.aclose()
                recording_upload_status = "failed" if getattr(egress_info, "error", "") else "uploaded"
                file_results = list(getattr(egress_info, "file_results", []) or [])
                if file_results:
                    first_file = file_results[0]
                    recording_file_size = int(getattr(first_file, "size", 0) or 0) or None
                    duration_ns = int(getattr(first_file, "duration", 0) or 0)
                    if duration_ns:
                        recording_duration_seconds = max(1, round(duration_ns / 1_000_000_000))
                    if getattr(first_file, "location", ""):
                        recording_url = first_file.location
                logger.info(
                    "recording.egress.stopped",
                    extra={
                        "call_id": call_id,
                        "tenant_id": tenant_id,
                        "did": did_masked,
                        "egress_id": egress_id,
                        "upload_status": recording_upload_status,
                        "file_size": recording_file_size,
                    },
                )
            except Exception as e:
                recording_upload_status = "failed"
                logger.warning(
                    "recording.egress.stop_failed",
                    extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked, "error_type": type(e).__name__},
                )

        # Update active_calls to completed (#38)
        await upsert_active_call("completed")

        # n8n webhook (#39)
        _n8n_url = live_config.get("n8n_webhook_url") or (None if postgres_tenant_runtime else os.getenv("N8N_WEBHOOK_URL"))
        if _n8n_url:
            try:
                import httpx
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: httpx.post(_n8n_url, json={
                        "event":        "call_completed",
                        "phone":        caller_phone,
                        "caller_name":  agent_tools.caller_name,
                        "duration":     duration,
                        "booked":       bool(agent_tools.booking_intent),
                        "sentiment":    sentiment,
                        "summary":      booking_status_msg,
                        "recording_url":recording_url,
                        "interrupt_count": interrupt_count,
                    }, timeout=5.0)
                )
                logger.info("[N8N] Webhook triggered")
            except Exception as e:
                logger.warning("[N8N] Webhook failed", extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked, "error_type": type(e).__name__})

        # Legacy fallback is intentionally disabled; PostgreSQL is the production path.
        if not postgres_tenant_runtime:
            from db import save_call_log
            save_call_log(
                phone=caller_phone,
                duration=duration,
                transcript=transcript_text,
                summary=booking_status_msg,
                recording_url=recording_url,
                caller_name=agent_tools.caller_name or "",
                sentiment=sentiment,
                estimated_cost_usd=estimated_cost,
                call_date=call_dt.date().isoformat(),
                call_hour=call_dt.hour,
                call_day_of_week=call_dt.strftime("%A"),
                was_booked=bool(agent_tools.booking_intent),
                interrupt_count=interrupt_count,
            )

        postgres_call_log_id = None
        if is_postgres_enabled() and tenant_id:
            try:
                postgres_call_log_id = insert_call_log(
                    tenant_id=tenant_id,
                    phone_number=caller_phone,
                    duration_seconds=duration,
                    transcript=transcript_text,
                    summary=booking_status_msg,
                    sentiment=sentiment,
                )
                logger.info(
                    "[POSTGRES] Call log dual-write complete",
                    extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked},
                )
            except Exception as e:
                logger.warning(
                    "[POSTGRES] Call log dual-write skipped",
                    extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked, "error_type": type(e).__name__},
                )

            if recording_storage_key:
                await record_livekit_upload_metadata(
                    tenant_id=tenant_id,
                    call_log_id=postgres_call_log_id,
                    call_id=call_id,
                    storage_key=recording_storage_key,
                    duration_seconds=recording_duration_seconds or duration,
                    file_size=recording_file_size,
                    upload_status=recording_upload_status,
                )

            if booking_result and booking_result.get("success") and agent_tools.booking_intent:
                try:
                    from uuid import UUID as _UUID
                    from backend.services.booking_service import BookingService

                    intent = agent_tools.booking_intent
                    BookingService().record_booking(
                        tenant_id=_UUID(str(tenant_id)),
                        call_log_id=postgres_call_log_id,
                        patient_name=intent["caller_name"] or "Unknown Caller",
                        patient_phone=intent["caller_phone"],
                        start_time=_parse_booking_datetime(intent["start_time"]),
                        cal_booking_uid=str(booking_result.get("booking_id") or ""),
                        status="confirmed",
                    )
                except Exception as e:
                    logger.warning(
                        "[POSTGRES] Booking write skipped",
                        extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked, "error_type": type(e).__name__},
                    )

    ctx.add_shutdown_callback(unified_shutdown_hook)


_DID_METADATA_KEYS = (
    "did",
    "dialed_did",
    "dialed_number",
    "dialedNumber",
    "called_number",
    "calledNumber",
    "destination_did",
    "destination_number",
    "destinationNumber",
    "callee",
    "to",
    "sip.to",
    "sip.toNumber",
    "sip.calledNumber",
    "sip.destinationNumber",
    "sip.requestUri",
    "sip.trunkPhoneNumber",
    "sip.phoneNumber",
)

_CALLER_METADATA_KEYS = (
    "caller_phone",
    "caller_number",
    "callerNumber",
    "from_number",
    "fromNumber",
    "ani",
    "from",
    "sip.from",
    "sip.fromNumber",
    "sip.callerNumber",
    "sip.callerPhoneNumber",
    "sip.phoneNumber",
    "phone_number",
    "phoneNumber",
)

_LANGUAGE_ALIASES = {
    "english": "en-IN",
    "en": "en-IN",
    "en-in": "en-IN",
    "en_in": "en-IN",
    "hindi": "hi-IN",
    "hinglish": "hi-IN",
    "hi": "hi-IN",
    "hi-in": "hi-IN",
    "hi_in": "hi-IN",
    "tamil": "ta-IN",
    "ta": "ta-IN",
    "ta-in": "ta-IN",
    "ta_in": "ta-IN",
    "telugu": "te-IN",
    "te": "te-IN",
    "te-in": "te-IN",
    "te_in": "te-IN",
    "kannada": "kn-IN",
    "kn": "kn-IN",
    "kn-in": "kn-IN",
    "kn_in": "kn-IN",
    "malayalam": "ml-IN",
    "ml": "ml-IN",
    "ml-in": "ml-IN",
    "ml_in": "ml-IN",
}

_HINDI_ROMAN_TOKENS = {
    "namaste",
    "namaskar",
    "mujhe",
    "mujko",
    "aap",
    "hai",
    "haan",
    "nahi",
    "kal",
    "aaj",
    "parso",
    "chahiye",
    "karna",
    "krna",
    "booking",
    "theek",
}


def _extract_dialed_did(ctx: JobContext, metadata: dict) -> str | None:
    """Best-effort DID extraction for tenant lookup across LiveKit SIP shapes."""
    room_meta = _parse_json_object(getattr(ctx.room, "metadata", "") or "")
    for mapping in (metadata, room_meta):
        did = _extract_phone_by_keys(mapping, _DID_METADATA_KEYS)
        if did:
            return did

    for raw in (getattr(ctx.job, "metadata", "") or "", getattr(ctx.room, "metadata", "") or ""):
        did = _extract_phone_near_did_label(raw)
        if did:
            return did

    for participant in ctx.room.remote_participants.values():
        attr = participant.attributes or {}
        did = _extract_phone_by_keys(attr, _DID_METADATA_KEYS)
        if did:
            return did
        did = _extract_phone_by_key_hints(
            attr,
            include=("did", "dialed", "called", "callee", "destination", "to", "trunk"),
            exclude=("from", "caller", "ani"),
        )
        if did:
            return did

    for participant in ctx.room.remote_participants.values():
        identity_did = _extract_phone_near_did_label(getattr(participant, "identity", "") or "")
        if identity_did:
            return identity_did
        identity_phone = _clean_did_value(getattr(participant, "identity", "") or "")
        if identity_phone and re.search(r"\d{7,15}", identity_phone):
            return identity_phone

    return None


def _extract_caller_info(ctx: JobContext, metadata: dict, room_metadata: dict) -> tuple[str | None, str]:
    caller_name = ""
    caller_phone = _extract_phone_by_keys(metadata, _CALLER_METADATA_KEYS)
    if not caller_phone:
        caller_phone = _extract_phone_by_keys(room_metadata, _CALLER_METADATA_KEYS)

    for identity, participant in ctx.room.remote_participants.items():
        if participant.name and participant.name not in ("", "Caller", "Unknown"):
            caller_name = participant.name
        attr = participant.attributes or {}
        if not caller_phone:
            caller_phone = _extract_phone_by_keys(attr, _CALLER_METADATA_KEYS)
        if not caller_phone:
            caller_phone = _extract_phone_by_key_hints(
                attr,
                include=("from", "caller", "ani"),
                exclude=("to", "called", "dialed", "destination", "trunk"),
            )
        if not caller_phone and "+" in identity:
            match = re.search(r"\+\d{7,15}", identity)
            if match:
                caller_phone = match.group(0)

    return caller_phone, caller_name


def _log_inbound_sip_metadata(
    ctx: JobContext,
    *,
    job_metadata: str,
    parsed_job_metadata: dict,
    parsed_room_metadata: dict,
    caller_phone: str,
    dialed_did: str | None,
) -> None:
    participants = []
    sip_headers: dict[str, Any] = {}
    for identity, participant in ctx.room.remote_participants.items():
        attributes = dict(participant.attributes or {})
        participants.append(
            {
                "identity": _redact_log_value(identity),
                "name": _redact_log_value(participant.name or ""),
                "attributes": _redact_log_value(attributes),
            }
        )
        for key, value in attributes.items():
            lowered = key.lower()
            if lowered.startswith("sip.") or "header" in lowered:
                sip_headers[key] = value

    logger.info(
        "sip.metadata.snapshot",
        extra={
            "call_id": ctx.room.name,
            "job_metadata": _redact_log_value(parsed_job_metadata or job_metadata),
            "room_name": ctx.room.name,
            "room_metadata": _redact_log_value(parsed_room_metadata or getattr(ctx.room, "metadata", "") or ""),
            "participants": participants,
            "sip_headers": _redact_log_value(sip_headers),
            "caller_number": mask_phone(caller_phone) if caller_phone != "unknown" else "unknown",
            "destination_did": mask_phone(dialed_did) if dialed_did else None,
        },
    )


def _parse_json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    raw = str(value or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_phone_by_keys(mapping: dict, keys: tuple[str, ...]) -> str | None:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = _deep_get(mapping, key)
        did = _clean_did_value(value)
        if did:
            return did
    return None


def _extract_phone_by_key_hints(
    mapping: dict,
    *,
    include: tuple[str, ...],
    exclude: tuple[str, ...] = (),
) -> str | None:
    for key, value in _flatten_mapping(mapping).items():
        lowered = key.lower()
        if any(token in lowered for token in include) and not any(token in lowered for token in exclude):
            phone = _clean_did_value(value)
            if phone:
                return phone
    return None


def _extract_phone_near_did_label(value: Any) -> str | None:
    raw = str(value or "")
    if not raw:
        return None
    pattern = re.compile(
        r"(?i)(?:did|dialed|called|callee|destination|trunk|to)[^+0-9]{0,48}(\+?\d[\d\s().-]{6,}\d)"
    )
    match = pattern.search(raw)
    return _clean_did_value(match.group(1)) if match else None


def _deep_get(mapping: dict, key: str) -> Any:
    if key in mapping:
        return mapping[key]
    current: Any = mapping
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _flatten_mapping(mapping: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    flattened: dict[str, Any] = {}
    for key, value in mapping.items():
        joined = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flattened.update(_flatten_mapping(value, joined))
        else:
            flattened[joined] = value
    return flattened


def _resolve_configured_language(config: dict) -> str:
    direct_language = _normalize_language_code(config.get("language"), allow_auto=False)
    if direct_language:
        return direct_language

    preset = str(config.get("lang_preset") or "").strip().lower()
    if preset not in {"", "auto", "multilingual"}:
        preset_language = _normalize_language_code(preset, allow_auto=False)
        if preset_language:
            return preset_language

    tts_language = _normalize_language_code(config.get("tts_language"), allow_auto=False)
    if tts_language:
        return tts_language

    return "hi-IN"


def _resolve_stt_language(config: dict, configured_language: str) -> str:
    explicit = config.get("stt_language")
    explicit_language = _normalize_language_code(explicit, allow_auto=True)
    if explicit_language and explicit_language != "unknown":
        return explicit_language
    if _language_switch_enabled(config):
        return "unknown"
    return configured_language


def _language_switch_enabled(config: dict) -> bool:
    preset = str(config.get("lang_preset") or "").strip().lower()
    explicit = str(config.get("language_switching") or "").strip().lower()
    return preset in {"", "auto", "multilingual"} or explicit in {"1", "true", "yes", "on"}


def _normalize_language_code(value: Any, *, allow_auto: bool) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if allow_auto and lowered in {"auto", "unknown", "multilingual"}:
        return "unknown"
    if raw in SUPPORTED_SARVAM_LANGUAGES:
        return raw
    alias = _LANGUAGE_ALIASES.get(lowered)
    if alias in SUPPORTED_SARVAM_LANGUAGES:
        return alias
    preset = LANGUAGE_PRESETS.get(lowered)
    if preset:
        preset_language = preset.get("tts_language")
        return preset_language if preset_language in SUPPORTED_SARVAM_LANGUAGES else None
    return None


def _normalize_detected_language(candidate: Any, *, transcript: str | None = None) -> str | None:
    language = _normalize_language_code(candidate, allow_auto=False)
    if language:
        return language
    return _detect_language_from_text(transcript or "")


def _detect_language_from_text(text: str) -> str | None:
    if not text or len(text.strip()) < LANGUAGE_SWITCH_MIN_CONFIDENCE_CHARS:
        return None
    script_ranges = (
        ("hi-IN", r"[\u0900-\u097F]"),
        ("ta-IN", r"[\u0B80-\u0BFF]"),
        ("te-IN", r"[\u0C00-\u0C7F]"),
        ("kn-IN", r"[\u0C80-\u0CFF]"),
        ("ml-IN", r"[\u0D00-\u0D7F]"),
    )
    for language, pattern in script_ranges:
        if len(re.findall(pattern, text)) >= LANGUAGE_SWITCH_MIN_CONFIDENCE_CHARS:
            return language

    tokens = {token.strip(".,!?;:").lower() for token in text.split()}
    if tokens & _HINDI_ROMAN_TOKENS:
        return "hi-IN"
    if re.search(r"[A-Za-z]", text):
        return "en-IN"
    return None


def _switch_sarvam_stt_language(stt_obj: Any, language: str) -> None:
    opts = getattr(stt_obj, "_opts", None)
    if opts is None:
        return
    model = getattr(opts, "model", "saaras:v3")
    mode = getattr(opts, "mode", "transcribe")
    try:
        opts.language = language
        for stream in list(getattr(stt_obj, "_streams", []) or []):
            update_options = getattr(stream, "update_options", None)
            if callable(update_options):
                update_options(language=language, model=model, mode=mode)
    except Exception as exc:  # noqa: BLE001
        logger.warning("language.switch.stt_failed", extra={"language": language, "error_type": type(exc).__name__})


def _switch_sarvam_tts_language(tts_obj: Any, language: str) -> None:
    opts = getattr(tts_obj, "_opts", None)
    if opts is None:
        return
    try:
        opts.target_language_code = language
        for stream in list(getattr(tts_obj, "_streams", []) or []):
            stream_opts = getattr(stream, "_opts", None)
            if stream_opts is not None:
                stream_opts.target_language_code = language
    except Exception as exc:  # noqa: BLE001
        logger.warning("language.switch.tts_failed", extra={"language": language, "error_type": type(exc).__name__})


def _redact_log_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _redact_log_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_log_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_log_value(item) for item in value)
    if isinstance(value, str):
        redacted = re.sub(r"\+?\d[\d\s().-]{6,}\d", lambda match: mask_phone(match.group(0)), value)
        return redacted[:2000]
    return value


def _clean_did_value(value) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("sip:"):
        raw = raw[4:]
    if raw.startswith("tel:"):
        raw = raw[4:]
    raw = raw.split("@", 1)[0].split(";", 1)[0]
    match = re.search(r"\+?\d{7,15}", raw)
    return match.group(0) if match else raw


def _coerce_business_hours(value) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _parse_booking_datetime(value: str) -> datetime:
    normalized = str(value or "").replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


# ══════════════════════════════════════════════════════════════════════════════
# WORKER ENTRY
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    run_startup_checks("voice-agent")
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name=os.getenv("LIVEKIT_AGENT_NAME", "inbound-receptionist"),
    ))
