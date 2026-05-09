"""Formatting helpers (Phase 2 scaffolding).

Pure, reusable formatters for logs and user-facing messages. Any helper
that touches PII (phone numbers, names) must have a masked variant to
keep logs compliant with EXECUTION.md §9.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pytz


_IST = pytz.timezone("Asia/Kolkata")


def mask_phone(phone: Optional[str]) -> str:
    """Mask all but the last 2 digits. Safe for logs.

    Examples:
        mask_phone("+919876543210") -> "+XXXXXXXXXX10"
        mask_phone("+91 98-765 43 210") -> "+XXXXXXXXXX10" (whitespace/dashes ignored)
        mask_phone("") -> ""
        mask_phone(None) -> ""
    """
    if not phone:
        return ""
    digits = [c for c in phone if c.isdigit()]
    if len(digits) <= 2:
        return "XX"
    masked_body = "X" * (len(digits) - 2) + "".join(digits[-2:])
    return f"+{masked_body}" if phone.lstrip().startswith("+") else masked_body


def format_ist_datetime(value: datetime) -> str:
    """Render a datetime in IST in the human-friendly form used in SMS."""
    return value.astimezone(_IST).strftime("%A, %d %B %Y at %I:%M %p IST")


def format_ist_date(value: datetime) -> str:
    """Render a date-only string in IST."""
    return value.astimezone(_IST).strftime("%A, %d %B %Y")
