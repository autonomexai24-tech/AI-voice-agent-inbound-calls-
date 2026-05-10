import os
import requests
import httpx
from datetime import datetime

from backend.logging import get_logger

logger = get_logger("calendar-tools")

CAL_BASE = "https://api.cal.com/v1"


def get_cal_creds(api_key: str | None = None, event_type_id: str | int | None = None) -> dict:
    raw_event_id = event_type_id if event_type_id not in (None, "") else os.environ.get("CAL_EVENT_TYPE_ID", "0")
    raw_event_id = raw_event_id or "0"
    try:
        event_id = int(raw_event_id)
    except (TypeError, ValueError):
        logger.error("[CAL] Invalid CAL_EVENT_TYPE_ID")
        event_id = 0
    return {
        "api_key":  api_key or os.environ.get("CAL_API_KEY", ""),
        "event_id": event_id,
    }


# ─── Cal.com: Get available slots ─────────────────────────────────────────────

def get_available_slots(
    date_str: str,
    *,
    cal_api_key: str | None = None,
    cal_event_type_id: str | int | None = None,
) -> list:
    """Fetch open Cal.com slots for a given YYYY-MM-DD date."""
    return _get_slots_calcom(
        date_str,
        cal_api_key=cal_api_key,
        cal_event_type_id=cal_event_type_id,
    )


def _get_slots_calcom(
    date_str: str,
    *,
    cal_api_key: str | None = None,
    cal_event_type_id: str | int | None = None,
) -> list:
    try:
        creds = get_cal_creds(api_key=cal_api_key, event_type_id=cal_event_type_id)
        if not creds["api_key"] or not creds["event_id"]:
            logger.warning("[CAL] Availability skipped; Cal.com credentials are not configured")
            return []
        resp = requests.get(
            f"{CAL_BASE}/slots",
            headers={"Content-Type": "application/json"},
            params={
                "apiKey":      creds["api_key"],
                "eventTypeId": creds["event_id"],
                "startTime":   f"{date_str}T00:00:00.000Z",
                "endTime":     f"{date_str}T23:59:59.000Z",
            },
            timeout=8,
        )
        resp.raise_for_status()
        raw_slots = resp.json().get("data", {}).get("slots", {}).get(date_str, [])
        slots = []
        for s in raw_slots:
            dt = datetime.fromisoformat(s["time"])
            slots.append({"time": s["time"], "label": dt.strftime("%-I:%M %p")})
        logger.info("[CAL] slots.loaded", extra={"slot_count": len(slots), "date": date_str})
        return slots
    except Exception as e:
        logger.error("[CAL] get_available_slots failed", extra={"error_type": type(e).__name__})
        return []

# ─── Create a booking ──────────────────────────────────────────────────────────

def create_booking(
    start_time: str,
    caller_name: str,
    caller_phone: str,
    notes: str = "",
    cal_api_key: str | None = None,
    cal_event_type_id: str | int | None = None,
) -> dict:
    """Synchronous wrapper — calls async_create_booking."""
    import asyncio
    try:
        return asyncio.get_event_loop().run_until_complete(
            async_create_booking(
                start_time,
                caller_name,
                caller_phone,
                notes,
                cal_api_key=cal_api_key,
                cal_event_type_id=cal_event_type_id,
            )
        )
    except RuntimeError:
        return asyncio.run(
            async_create_booking(
                start_time,
                caller_name,
                caller_phone,
                notes,
                cal_api_key=cal_api_key,
                cal_event_type_id=cal_event_type_id,
            )
        )


async def async_create_booking(
    start_time: str,
    caller_name: str,
    caller_phone: str,
    notes: str = "",
    cal_api_key: str | None = None,
    cal_event_type_id: str | int | None = None,
) -> dict:
    """Create a Cal.com booking."""
    return await _create_booking_calcom(
        start_time,
        caller_name,
        caller_phone,
        notes,
        cal_api_key=cal_api_key,
        cal_event_type_id=cal_event_type_id,
    )


async def _create_booking_calcom(
    start_time: str,
    caller_name: str,
    caller_phone: str,
    notes: str,
    *,
    cal_api_key: str | None = None,
    cal_event_type_id: str | int | None = None,
) -> dict:
    try:
        creds = get_cal_creds(api_key=cal_api_key, event_type_id=cal_event_type_id)
        if not creds["api_key"] or not creds["event_id"]:
            return {
                "success": False,
                "booking_id": None,
                "message": "Cal.com credentials are not configured.",
            }
        payload = {
            "eventTypeId": creds["event_id"],
            "start": start_time,
            "attendee": {
                "name":        caller_name,
                "email":       f"{caller_phone.replace('+','').replace(' ','')}@voiceagent.placeholder",
                "phoneNumber": caller_phone,
                "timeZone":    "Asia/Kolkata",
                "language":    "en",
            },
            "bookingFieldsResponses": {
                "notes": notes or f"Booked via AI voice agent. Phone: {caller_phone}",
            },
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                "https://api.cal.com/v2/bookings",
                headers={
                    "Authorization":  f"Bearer {creds['api_key']}",
                    "cal-api-version": "2024-08-13",
                    "Content-Type":   "application/json",
                },
                json=payload,
            )
            if resp.status_code not in (200, 201):
                logger.error("[CAL] booking.failed_status", extra={"status_code": resp.status_code})
                return {"success": False, "booking_id": None, "message": resp.text}
            uid = resp.json().get("data", {}).get("uid", "unknown")
            logger.info("[CAL] booking.created", extra={"booking_uid": uid})
            return {"success": True, "booking_id": uid, "message": "Booking confirmed"}
    except httpx.TimeoutException:
        return {"success": False, "booking_id": None, "message": "Booking timed out."}
    except Exception as e:
        logger.error("[CAL] booking.failed", extra={"error_type": type(e).__name__})
        return {"success": False, "booking_id": None, "message": "Booking unavailable."}

# ─── Cancel a booking ──────────────────────────────────────────────────────────

def cancel_booking(booking_id: str, reason: str = "Cancelled by caller") -> dict:
    """Cancel a Cal.com booking by UID."""
    creds = get_cal_creds()
    try:
        resp = requests.delete(
            f"{CAL_BASE}/bookings/{booking_id}/cancel?apiKey={creds['api_key']}",
            headers={"Content-Type": "application/json"},
            json={"reason": reason},
            timeout=8,
        )
        resp.raise_for_status()
        logger.info("[CAL] booking.cancelled", extra={"booking_uid": booking_id})
        return {"success": True, "message": "Cancelled successfully"}
    except Exception as e:
        logger.error("[CAL] cancel_booking.failed", extra={"error_type": type(e).__name__})
        return {"success": False, "message": "Cancellation unavailable."}
