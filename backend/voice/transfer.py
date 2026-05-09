"""SIP transfer helpers (Phase 2 scaffolding).

Extraction of the pure URI-construction logic used in
`AgentTools.transfer_call` in agent.py. No I/O, no LiveKit API calls —
those stay in the tool implementation and remain on the live voice path.

Until Phase 3 rewires agent.py, keep this helper's output identical to
the inline logic in agent.py.
"""

from __future__ import annotations

from typing import Optional


def build_sip_transfer_uri(
    destination: Optional[str],
    sip_domain: Optional[str],
) -> Optional[str]:
    """Return a fully-qualified SIP URI for transfer, or None if invalid.

    Rules (match agent.py exactly):
      * If destination is falsy → return None.
      * If destination doesn't contain '@' and sip_domain is set, append
        '@<sip_domain>' after stripping any 'tel:' or 'sip:' prefix.
      * Ensure the final URI starts with 'sip:'.

    Examples:
      ("+91-80-1234-5678", "example.sip.vobiz.ai")
        → "sip:+91-80-1234-5678@example.sip.vobiz.ai"
      ("sip:ops@pbx.example", None)
        → "sip:ops@pbx.example"
      ("tel:+911234567", None)
        → "sip:+911234567"
      (None, "anything")
        → None
    """
    if not destination:
        return None

    resolved = destination
    if sip_domain and "@" not in resolved:
        clean = resolved.replace("tel:", "").replace("sip:", "")
        resolved = f"sip:{clean}@{sip_domain}"

    if not resolved.startswith("sip:"):
        resolved = f"sip:{resolved}"

    return resolved
