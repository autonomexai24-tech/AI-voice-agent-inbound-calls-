"""Voice runtime package (Phase 2 scaffolding).

Modules here mirror the pure, side-effect-free portions of the existing
agent.py so Phase 3 can rewire imports without changing runtime behavior.

Phase 2 rules:
- The running agent still reads its logic from root-level `agent.py`.
- Modules here are the migration target. They must be kept in sync with
  agent.py until Phase 3 flips the import direction.
- No LiveKit, Sarvam, or Silero imports at module top level — we keep
  this package importable in contexts that don't load the voice plugins
  (e.g. the API process).
"""

from backend.voice.language import LANGUAGE_PRESETS, get_language_instruction
from backend.voice.prompts import (
    build_system_prompt,
    get_ist_time_context,
)
from backend.voice.transfer import build_sip_transfer_uri

__all__ = [
    "LANGUAGE_PRESETS",
    "get_language_instruction",
    "build_system_prompt",
    "get_ist_time_context",
    "build_sip_transfer_uri",
]
