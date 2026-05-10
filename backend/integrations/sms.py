from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

import httpx
import pytz

from backend.utils.formatting import mask_phone
from backend.utils.logging import get_logger

logger = get_logger("backend.integrations.sms")

FAST2SMS_ENDPOINT = "https://www.fast2sms.com/dev/bulkV2"
_IST = pytz.timezone("Asia/Kolkata")
_NON_DIGIT_RE = re.compile(r"\D+")


@dataclass(frozen=True)
class SMSResult:
    provider: str
    phone_number: str
    status: str
    request_id: Optional[str] = None
    error_message: Optional[str] = None


class SMSProvider(Protocol):
    name: str

    async def send_sms(self, phone_number: str, message: str) -> SMSResult: ...


class Fast2SMSProvider:
    name = "fast2sms"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        sender_id: Optional[str] = None,
        route: Optional[str] = None,
        endpoint: str = FAST2SMS_ENDPOINT,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("FAST2SMS_API_KEY", "")
        self.sender_id = sender_id if sender_id is not None else os.environ.get("FAST2SMS_SENDER_ID", "")
        self.route = route if route is not None else os.environ.get("FAST2SMS_ROUTE", "q")
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds

    async def send_sms(self, phone_number: str, message: str) -> SMSResult:
        try:
            normalized = normalize_indian_phone(phone_number)
        except ValueError as exc:
            return SMSResult(
                provider=self.name,
                phone_number=phone_number,
                status="failed",
                error_message=str(exc),
            )

        if not self.api_key:
            return SMSResult(
                provider=self.name,
                phone_number=normalized,
                status="failed",
                error_message="missing_fast2sms_api_key",
            )

        payload = {
            "route": self.route or "q",
            "numbers": _fast2sms_number(normalized),
            "message": message,
            "flash": "0",
        }
        if self.sender_id:
            payload["sender_id"] = self.sender_id
        if _requires_unicode(message):
            payload["language"] = "unicode"

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self.endpoint,
                    headers={"authorization": self.api_key},
                    data=payload,
                )
            data = _json_or_empty(response)
            if response.status_code >= 400:
                return SMSResult(
                    provider=self.name,
                    phone_number=normalized,
                    status="failed",
                    error_message=_provider_error(data, f"http_{response.status_code}"),
                )
            if _provider_success(data):
                return SMSResult(
                    provider=self.name,
                    phone_number=normalized,
                    status="sent",
                    request_id=str(data.get("request_id") or data.get("requestId") or ""),
                )
            return SMSResult(
                provider=self.name,
                phone_number=normalized,
                status="failed",
                error_message=_provider_error(data, "provider_rejected"),
            )
        except httpx.TimeoutException:
            logger.warning("sms.provider.timeout", extra={"provider": self.name, "phone_masked": mask_phone(normalized)})
            return SMSResult(provider=self.name, phone_number=normalized, status="failed", error_message="timeout")
        except httpx.RequestError as exc:
            logger.warning(
                "sms.provider.request_error",
                extra={"provider": self.name, "phone_masked": mask_phone(normalized), "error_type": type(exc).__name__},
            )
            return SMSResult(
                provider=self.name,
                phone_number=normalized,
                status="failed",
                error_message=type(exc).__name__,
            )


def normalize_indian_phone(phone_number: str) -> str:
    digits = _NON_DIGIT_RE.sub("", phone_number or "")
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 11 and digits.startswith("0"):
        return f"+91{digits[1:]}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    raise ValueError("invalid_indian_phone_number")


def render_booking_sms(
    *,
    caller_name: str,
    business_name: str,
    start_time: str,
    language: Optional[str] = None,
) -> str:
    template_key = resolve_template_language(language)
    dt = _parse_start_time(start_time)
    values = {
        "name": _safe_value(caller_name, "there"),
        "business": _safe_value(business_name, "our clinic"),
        "date": dt.strftime("%d %b %Y"),
        "time": dt.strftime("%I:%M %p").lstrip("0"),
    }
    return _TEMPLATES[template_key].format(**values)


def resolve_template_language(language: Optional[str]) -> str:
    value = (language or "english").strip().lower()
    if value in {"en", "en-in", "english"}:
        return "english"
    if value in {"hi", "hi-in", "hindi", "hinglish"}:
        return "hindi"
    if value in {"kn", "kn-in", "kannada"}:
        return "kannada"
    if value in {"ta", "ta-in", "tamil"}:
        return "tamil"
    if value in {"te", "te-in", "telugu"}:
        return "telugu"
    if value in {"ml", "ml-in", "malayalam"}:
        return "malayalam"
    return "english"


def _parse_start_time(start_time: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(start_time)
    except ValueError:
        return datetime.now(_IST)
    if parsed.tzinfo is None:
        parsed = _IST.localize(parsed)
    return parsed.astimezone(_IST)


def _safe_value(value: str, fallback: str, max_len: int = 64) -> str:
    cleaned = " ".join(str(value or "").split())
    return (cleaned or fallback)[:max_len]


def _fast2sms_number(normalized: str) -> str:
    return normalized[3:] if normalized.startswith("+91") else normalized


def _requires_unicode(message: str) -> bool:
    return any(ord(char) > 127 for char in message)


def _json_or_empty(response: httpx.Response) -> dict:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _provider_success(data: dict) -> bool:
    return data.get("return") is True or data.get("success") is True


def _provider_error(data: dict, fallback: str) -> str:
    message = data.get("message") or data.get("error") or data.get("errors") or fallback
    if isinstance(message, list):
        message = "; ".join(str(part) for part in message[:2])
    return str(message)[:240]


_TEMPLATES = {
    "english": "Hi {name}, your appointment at {business} is confirmed for {date} at {time}.",
    "hindi": "नमस्ते {name}, {business} में आपकी appointment {date} को {time} पर confirmed है.",
    "kannada": "ನಮಸ್ಕಾರ {name}, {business} ನಲ್ಲಿ ನಿಮ್ಮ appointment {date} {time}ಕ್ಕೆ confirmed ಆಗಿದೆ.",
    "tamil": "வணக்கம் {name}, {business} இல் உங்கள் appointment {date} {time}க்கு confirmed.",
    "telugu": "నమస్తే {name}, {business} వద్ద మీ appointment {date} {time}కి confirmed.",
    "malayalam": "നമസ്കാരം {name}, {business}ൽ നിങ്ങളുടെ appointment {date} {time}ന് confirmed.",
}
