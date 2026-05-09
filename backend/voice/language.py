"""Language presets and directive builder.

Canonical home for `LANGUAGE_PRESETS` and `get_language_instruction()`.
As of Phase 3A, `agent.py` imports from this module; there is no longer
a duplicate definition in root-level code.

Contract:
- Keys: preset names used in tenant_config.lang_preset.
- Each value dict has: label, tts_language, tts_voice, instruction.
- "multilingual" is the default when an unknown preset is requested.
"""

from __future__ import annotations


LANGUAGE_PRESETS: dict[str, dict[str, str]] = {
    "hinglish": {
        "label": "Hinglish (Hindi+English)",
        "tts_language": "hi-IN",
        "tts_voice": "kavya",
        "instruction": (
            "Speak in natural Hinglish — mix Hindi and English like educated "
            "Indians do. Default to Hindi but use English words when more natural."
        ),
    },
    "hindi": {
        "label": "Hindi",
        "tts_language": "hi-IN",
        "tts_voice": "ritu",
        "instruction": (
            "Speak only in pure Hindi. Avoid English words wherever a Hindi "
            "equivalent exists."
        ),
    },
    "english": {
        "label": "English (India)",
        "tts_language": "en-IN",
        "tts_voice": "dev",
        "instruction": "Speak only in Indian English with a warm, professional tone.",
    },
    "tamil": {
        "label": "Tamil",
        "tts_language": "ta-IN",
        "tts_voice": "priya",
        "instruction": "Speak only in Tamil. Use standard spoken Tamil for a professional context.",
    },
    "telugu": {
        "label": "Telugu",
        "tts_language": "te-IN",
        "tts_voice": "kavya",
        "instruction": "Speak only in Telugu. Use clear, polite spoken Telugu.",
    },
    "gujarati": {
        "label": "Gujarati",
        "tts_language": "gu-IN",
        "tts_voice": "rohan",
        "instruction": "Speak only in Gujarati. Use polite, professional Gujarati.",
    },
    "bengali": {
        "label": "Bengali",
        "tts_language": "bn-IN",
        "tts_voice": "neha",
        "instruction": "Speak only in Bengali (Bangla). Use standard, polite spoken Bengali.",
    },
    "marathi": {
        "label": "Marathi",
        "tts_language": "mr-IN",
        "tts_voice": "shubh",
        "instruction": "Speak only in Marathi. Use polite, standard spoken Marathi.",
    },
    "kannada": {
        "label": "Kannada",
        "tts_language": "kn-IN",
        "tts_voice": "rahul",
        "instruction": "Speak only in Kannada. Use clear, professional spoken Kannada.",
    },
    "malayalam": {
        "label": "Malayalam",
        "tts_language": "ml-IN",
        "tts_voice": "ritu",
        "instruction": "Speak only in Malayalam. Use polite, professional spoken Malayalam.",
    },
    "multilingual": {
        "label": "Multilingual (Auto)",
        "tts_language": "hi-IN",
        "tts_voice": "kavya",
        "instruction": (
            "Detect the caller's language from their first message and reply "
            "in that SAME language for the entire call. Supported: Hindi, "
            "Hinglish, English, Tamil, Telugu, Gujarati, Bengali, Marathi, "
            "Kannada, Malayalam. Switch if caller switches."
        ),
    },
}


def get_language_instruction(lang_preset: str) -> str:
    """Return the language directive block for a given preset.

    Unknown presets fall back to "multilingual" (auto-detect), matching
    the existing agent.py behavior.
    """
    preset = LANGUAGE_PRESETS.get(lang_preset, LANGUAGE_PRESETS["multilingual"])
    return f"\n\n[LANGUAGE DIRECTIVE]\n{preset['instruction']}"
