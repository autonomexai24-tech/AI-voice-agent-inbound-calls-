import os
import json
import logging
import pytz
import re
import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Annotated
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

CONFIG_FILE = "config.json"

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


# ── Config loader (#17 partial — per-client path awareness) ───────────────────
def get_live_config(phone_number: str | None = None):
    """Load config — tries per-client file first, then default config.json."""
    config = {}
    paths = []
    if phone_number and phone_number != "unknown":
        clean = phone_number.replace("+", "").replace(" ", "")
        paths.append(f"configs/{clean}.json")
    paths += ["configs/default.json", CONFIG_FILE]

    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    config = json.load(f)
                    logger.info(f"[CONFIG] Loaded: {path}")
                    break
            except Exception as e:
                logger.error("[CONFIG] Failed to read", extra={"path": path, "error_type": type(e).__name__})

    return {
        "agent_instructions":       config.get("agent_instructions", ""),
        "stt_min_endpointing_delay":config.get("stt_min_endpointing_delay", 0.05),
        "llm_model":                config.get("llm_model", "gpt-4o-mini"),
        "llm_provider":             config.get("llm_provider", "openai"),
        "tts_voice":                config.get("tts_voice", "kavya"),
        "tts_language":             config.get("tts_language", "hi-IN"),
        "tts_provider":             config.get("tts_provider", "sarvam"),
        "stt_provider":             config.get("stt_provider", "sarvam"),
        "stt_language":             config.get("stt_language", "unknown"),
        "lang_preset":              config.get("lang_preset", "multilingual"),
        "max_turns":                config.get("max_turns", 25),
        **config,
    }


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
from backend.core.config_resolver import resolve_runtime_config
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
            self._first_line or (
                "Namaste, thanks for calling. How can I help you today?"
            )
        )
        await self.session.generate_reply(
            instructions=f"Say exactly this phrase: '{greeting}'"
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
    call_id = ctx.room.name
    set_correlation_context(call_id=call_id)
    logger.info("[ROOM] Connected", extra={"call_id": call_id})

    # ── Extract caller info ───────────────────────────────────────────────
    phone_number = None
    caller_name  = ""
    caller_phone = "unknown"

    # Try metadata first (LiveKit dispatch metadata)
    metadata = ctx.job.metadata or ""
    meta = {}
    if metadata:
        try:
            meta = json.loads(metadata)
            phone_number = meta.get("phone_number")
        except Exception:
            pass

    # Extract from SIP participants
    for identity, participant in ctx.room.remote_participants.items():
        # Name from caller ID (#32)
        if participant.name and participant.name not in ("", "Caller", "Unknown"):
            caller_name = participant.name
            logger.info("[CALLER-ID] Name present from SIP", extra={"call_id": call_id})
        if not phone_number:
            attr = participant.attributes or {}
            phone_number = attr.get("sip.phoneNumber") or attr.get("phoneNumber")
        if not phone_number and "+" in identity:
            import re as _re
            m = _re.search(r"\+\d{7,15}", identity)
            if m:
                phone_number = m.group()

    caller_phone = phone_number or "unknown"
    dialed_did = _extract_dialed_did(ctx, meta)
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
    resolved_config = resolve_runtime_config(caller_phone=caller_phone, did=dialed_did)
    live_config   = resolved_config.config
    tenant_id     = resolved_config.tenant_id
    did_masked    = mask_phone(dialed_did) if dialed_did else None
    set_correlation_context(call_id=call_id, tenant_id=str(tenant_id or ""), did=did_masked or "")
    tenant_config_unavailable = (
        is_postgres_enabled()
        and not tenant_id
        and resolved_config.fallback_reason
        in {"tenant_not_configured", "postgres_error", "did_missing", "postgres_unavailable_or_unconfigured"}
    )
    postgres_tenant_runtime = is_postgres_enabled() and (bool(tenant_id) or tenant_config_unavailable)
    if tenant_config_unavailable:
        if resolved_config.fallback_reason == "tenant_not_configured":
            fallback_line = "This number is not configured. Please contact support."
        else:
            fallback_line = "We are unable to load this number's configuration right now. Please call again later."
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
    delay_setting = live_config.get("stt_min_endpointing_delay", 0.05)
    llm_model     = live_config.get("llm_model", "gpt-4o-mini")
    llm_provider  = live_config.get("llm_provider", "openai")
    tts_voice     = live_config.get("tts_voice", "kavya")
    tts_language  = live_config.get("tts_language", "hi-IN")
    tts_provider  = live_config.get("tts_provider", "sarvam")
    stt_provider  = live_config.get("stt_provider", "sarvam")
    stt_language  = live_config.get("stt_language", "unknown")  # auto-detect (#20)
    max_turns     = live_config.get("max_turns", 25)

    # ── Caller memory (#15) ───────────────────────────────────────────────
    async def get_caller_history(phone: str) -> str:
        if tenant_config_unavailable:
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

    # ── Build STT (#1 16kHz, #20 auto-detect, #9 Deepgram) ──────────────
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
            agent_stt = sarvam.STT(
                language=stt_language,
                model="saaras:v3",
                mode="translate",
                flush_signal=True,
                sample_rate=16000,
            )
    else:
        agent_stt = sarvam.STT(
            language=stt_language,      # "unknown" = auto-detect (#20)
            model="saaras:v3",
            mode="translate",
            flush_signal=True,
            sample_rate=16000,          # force 16kHz (#1)
        )
        logger.info("[STT] Using Sarvam Saaras v3")

    # ── Build TTS (#2 24kHz, #10 ElevenLabs) ────────────────────────────
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
        logger.info(f"[TTS] Using Sarvam Bulbul v3 — voice: {tts_voice} lang: {tts_language}")

    # ── Sentence chunker (keep responses short for voice) ─────────────────
    def before_tts_cb(agent_response: str) -> str:
        sentences = re.split(r'(?<=[।.!?])\s+', agent_response.strip())
        return sentences[0] if sentences else agent_response

    # ── Turn counter + auto-close (#29) ──────────────────────────────────
    turn_count    = 0
    interrupt_count = 0  # (#30)
    last_user_speech_at: float | None = None

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
            logger.info(
                "latency.stt_received",
                extra={
                    "call_id": call_id,
                    "tenant_id": tenant_id,
                    "did": did_masked,
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

        if agent_is_speaking:
            logger.debug("[FILTER-ECHO] Dropped transcript echo", extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked})
            return
        if not transcript or len(transcript) < 3:
            return
        if transcript_lower in FILLER_WORDS:
            logger.debug("[FILTER-FILLER] Dropped filler transcript", extra={"call_id": call_id, "tenant_id": tenant_id, "did": did_masked})
            return

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


def _extract_dialed_did(ctx: JobContext, metadata: dict) -> str | None:
    """Best-effort DID extraction for tenant lookup; fallback stays single-tenant."""
    for key in ("did", "dialed_did", "dialed_number", "called_number", "to"):
        value = metadata.get(key)
        if value:
            return _clean_did_value(value)

    room_metadata = getattr(ctx.room, "metadata", "") or ""
    if room_metadata:
        try:
            room_meta = json.loads(room_metadata)
            for key in ("did", "dialed_did", "dialed_number", "called_number", "to"):
                value = room_meta.get(key)
                if value:
                    return _clean_did_value(value)
        except Exception:
            pass

    for participant in ctx.room.remote_participants.values():
        attr = participant.attributes or {}
        for key in (
            "sip.to",
            "sip.toNumber",
            "sip.calledNumber",
            "sip.requestUri",
            "sip.trunkPhoneNumber",
            "calledNumber",
            "dialedNumber",
            "to",
        ):
            value = attr.get(key)
            if value:
                return _clean_did_value(value)

    return None


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
