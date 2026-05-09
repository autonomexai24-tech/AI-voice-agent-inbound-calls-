"""Prompt composition helpers.

Canonical home for `get_ist_time_context()` and the
`build_system_prompt()` composer. As of Phase 3A, `agent.py` imports
`get_ist_time_context` from this module.

Both functions are pure and side-effect-free. `get_ist_time_context`
accepts an optional `now` parameter for deterministic testing; callers
that pass nothing get current IST time (matching the previous inline
behavior in agent.py byte-for-byte).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytz

from backend.voice.language import get_language_instruction


_IST = pytz.timezone("Asia/Kolkata")


def get_ist_time_context(now: datetime | None = None) -> str:
    """Return the SYSTEM CONTEXT block with today + the next 6 days in IST.

    `now` defaults to the current IST time. Override is available for
    deterministic tests.
    """
    resolved = (now or datetime.now(_IST)).astimezone(_IST)
    today_str = resolved.strftime("%A, %B %d, %Y")
    time_str = resolved.strftime("%I:%M %p")

    days_lines = []
    for i in range(7):
        day = resolved + timedelta(days=i)
        label = "Today" if i == 0 else ("Tomorrow" if i == 1 else day.strftime("%A"))
        days_lines.append(
            f"  {label}: {day.strftime('%A %d %B %Y')} → ISO {day.strftime('%Y-%m-%d')}"
        )
    days_block = "\n".join(days_lines)

    return (
        f"\n\n[SYSTEM CONTEXT]\n"
        f"Current date & time: {today_str} at {time_str} IST\n"
        f"Resolve ALL relative day references using this table:\n{days_block}\n"
        f"Always use ISO dates when calling save_booking_intent. "
        f"Appointments in IST (+05:30).]"
    )


def build_system_prompt(
    agent_instructions: str,
    lang_preset: str,
    now: datetime | None = None,
    extra_context: str = "",
) -> str:
    """Compose the final system prompt for a call turn.

    Order (preserves agent.py behavior):
        <tenant agent_instructions>
        + IST time context
        + language directive
        + optional extra context (e.g. caller history)
    """
    return (
        (agent_instructions or "")
        + get_ist_time_context(now=now)
        + get_language_instruction(lang_preset)
        + (extra_context or "")
    )
