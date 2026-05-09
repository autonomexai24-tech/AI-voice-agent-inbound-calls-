"""Voice runtime composition notes (Phase 2 scaffolding — intentionally empty).

This module is a documentation placeholder for the Phase 3 extraction of
the LiveKit agent session construction currently in agent.py `entrypoint`.

Phase 2 does NOT move the session-building code. That code touches:
  * LiveKit plugin constructors (stt, llm, tts)
  * Silero VAD / noise cancellation
  * RoomInputOptions / AgentSession lifecycle
  * Barge-in / interruption event handlers
  * Endpointing configuration

All of these are latency-critical and directly tied to the streaming
pipeline. Per EXECUTION.md §4 we do not move them until we can validate
an inbound call round-trip end-to-end, which is an explicit Phase 3 gate.

When Phase 3 begins, this module will host:
  * `build_agent_session(config, ctx) -> AgentSession`
  * `attach_event_handlers(session, ...)` — barge-in, user speech, etc.
  * `attach_shutdown_hook(session, ...)`

Until then, the running source of truth for session construction is the
`entrypoint()` function in root-level `agent.py`.
"""

from __future__ import annotations
