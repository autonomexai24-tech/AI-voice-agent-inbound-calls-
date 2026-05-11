import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import agent
from backend.core import config_resolver


TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _participant(identity="sip_919111111111", name="Caller", attributes=None):
    return SimpleNamespace(
        identity=identity,
        name=name,
        attributes=attributes or {},
    )


def _ctx(*, job_metadata="", room_metadata="", participants=None):
    return SimpleNamespace(
        job=SimpleNamespace(metadata=job_metadata),
        room=SimpleNamespace(
            name="room-test",
            metadata=room_metadata,
            remote_participants=participants or {},
        ),
    )


class AgentRuntimeConfigTests(unittest.TestCase):
    def test_extracts_did_from_sip_to_without_confusing_caller_number(self):
        ctx = _ctx(
            participants={
                "sip_919111111111": _participant(
                    attributes={
                        "sip.phoneNumber": "+919111111111",
                        "sip.to": "sip:+917676808950@vobiz.example",
                    },
                )
            }
        )

        caller_phone, _ = agent._extract_caller_info(ctx, {}, {})
        did = agent._extract_dialed_did(ctx, {})

        self.assertEqual(caller_phone, "+919111111111")
        self.assertEqual(did, "+917676808950")

    def test_extracts_did_from_room_metadata(self):
        room_metadata = json.dumps({"sip": {"calledNumber": "+917676808950"}})
        ctx = _ctx(room_metadata=room_metadata)

        self.assertEqual(agent._extract_dialed_did(ctx, {}), "+917676808950")

    def test_extracts_did_from_sip_phone_number_when_provider_uses_that_field(self):
        ctx = _ctx(
            participants={
                "inbound": _participant(attributes={"sip.phoneNumber": "+917676808950"})
            }
        )

        self.assertEqual(agent._extract_dialed_did(ctx, {}), "+917676808950")

    def test_language_runtime_uses_fixed_tenant_language_when_preset_is_fixed(self):
        config = {"tts_language": "ta-IN", "lang_preset": "tamil", "stt_language": "unknown"}
        language = agent._resolve_configured_language(config)

        self.assertEqual(language, "ta-IN")
        self.assertEqual(agent._resolve_stt_language(config, language), "ta-IN")

    def test_language_runtime_uses_auto_stt_for_multilingual(self):
        config = {"tts_language": "hi-IN", "lang_preset": "multilingual"}
        language = agent._resolve_configured_language(config)

        self.assertEqual(language, "hi-IN")
        self.assertEqual(agent._resolve_stt_language(config, language), "unknown")

    def test_fixed_language_preset_overrides_stale_tts_language(self):
        config = {"tts_language": "hi-IN", "lang_preset": "english"}

        self.assertEqual(agent._resolve_configured_language(config), "en-IN")

    def test_lightweight_language_detection_supports_required_indian_languages(self):
        self.assertEqual(agent._detect_language_from_text("नमस्ते मुझे बुकिंग करनी है"), "hi-IN")
        self.assertEqual(agent._detect_language_from_text("வணக்கம் appointment வேண்டும்"), "ta-IN")
        self.assertEqual(agent._detect_language_from_text("నమస్తే appointment కావాలి"), "te-IN")
        self.assertEqual(agent._detect_language_from_text("ನಮಸ್ಕಾರ appointment ಬೇಕು"), "kn-IN")
        self.assertEqual(agent._detect_language_from_text("നമസ്കാരം appointment വേണം"), "ml-IN")


class AsyncRuntimeResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_resolver_loads_tenant_config_from_postgres_helpers(self):
        tenant = {
            "id": TENANT_ID,
            "name": "Autonomex AI",
            "slug": "autonomex-ai",
            "phone_number": "+917676808950",
        }
        tenant_config = {
            "agent_instructions": "Tenant prompt",
            "first_line": "Tenant greeting",
            "tts_voice": "kavya",
            "tts_language": "ta-IN",
            "lang_preset": "tamil",
            "llm_model": "gpt-4o-mini",
            "endpointing_delay": 0.4,
        }

        with patch.object(config_resolver, "is_postgres_enabled", return_value=True), patch.object(
            config_resolver, "get_tenant_by_did", new=AsyncMock(return_value=tenant)
        ), patch.object(
            config_resolver, "load_tenant_config", new=AsyncMock(return_value=tenant_config)
        ):
            resolved = await config_resolver.resolve_runtime_config_async(
                caller_phone="+919111111111",
                did="+917676808950",
            )

        self.assertEqual(resolved.tenant_id, TENANT_ID)
        self.assertEqual(resolved.source, "postgres")
        self.assertEqual(resolved.config["agent_instructions"], "Tenant prompt")
        self.assertEqual(resolved.config["first_line"], "Tenant greeting")
        self.assertEqual(resolved.config["tts_language"], "ta-IN")
        self.assertEqual(resolved.config["llm_model"], "gpt-4o-mini")


if __name__ == "__main__":
    unittest.main()
