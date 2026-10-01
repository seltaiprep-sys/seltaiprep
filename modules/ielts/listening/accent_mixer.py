"""Accent mixing configuration for realistic IELTS Listening tests"""

import random
import logging
from typing import Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class AccentMixer:
    """Mix different English accents across IELTS Listening sections."""

    SECTIONS = (1, 2, 3, 4)
    DEFAULT_ACCENT = "british"
    DEFAULT_COMBO: Dict[str, str] = {"1": "british", "2": "british", 
                                      "3": "british", "4": "british"}

    ACCENT_COMBINATIONS: List[Dict[str, str]] = [
        {"1": "british", "2": "british", "3": "british", "4": "british"},
        {"1": "british", "2": "british", "3": "australian", "4": "british"},
        {"1": "british", "2": "australian", "3": "british", "4": "british"},
        {"1": "australian", "2": "british", "3": "british", "4": "british"},
        {"1": "british", "2": "british", "3": "neutral", "4": "british"},
        {"1": "british", "2": "australian", "3": "neutral", "4": "british"},
        {"1": "australian", "2": "australian", "3": "australian", "4": "australian"},
    ]

    ACCENT_METADATA: Dict[str, Dict[str, str]] = {
        "british": {
            "name": "British English (RP)",
            "region": "UK",
            "features": "Non-rhotic R, formal tone",
            "vocabulary": "queue, flat, lift, holiday",
            "tts_voice": "en-GB-SoniaNeural",
        },
        "australian": {
            "name": "Australian English",
            "region": "Australia",
            "features": "Rising tone, relaxed speech",
            "vocabulary": "arvo, mate, brekkie",
            "tts_voice": "en-AU-NatashaNeural",
        },
        "neutral": {
            "name": "North American English",
            "region": "USA/Canada",
            "features": "Rhotic R, clear T",
            "vocabulary": "apartment, elevator, vacation",
            "tts_voice": "en-US-JennyNeural",
        },
    }

    SECTION_CONTEXT: Dict[int, str] = {
        1: "Service conversation between two speakers",
        2: "Monologue or guided talk",
        3: "Academic discussion (2-4 speakers)",
        4: "Academic lecture or presentation",
    }

    @classmethod
    def get_random_combo(cls) -> Dict[str, str]:
        if not cls.ACCENT_COMBINATIONS:
            logger.warning("Accent combinations empty, using default British combo")
            return cls.DEFAULT_COMBO.copy()
        return random.choice(cls.ACCENT_COMBINATIONS).copy()

    @classmethod
    def generate_random_combo(cls) -> Dict[str, str]:
        """Generate a random accent combo with realistic weighting."""
        # Section 4: mostly British
        section4 = random.choices(
            ['british', 'australian', 'neutral'],
            weights=[0.85, 0.10, 0.05]
        )[0]
        # Sections 1-3: balanced, but still British-leaning
        sections = {}
        for i in range(1, 4):
            sections[str(i)] = random.choices(
                ['british', 'australian', 'neutral'],
                weights=[0.5, 0.3, 0.2]
            )[0]
        sections['4'] = section4
        return sections

    @classmethod
    def _normalize_section_key(cls, section: Union[int, str]) -> str:
        return str(section)

    @classmethod
    def get_section_accent(
        cls,
        section: Union[int, str],
        combo: Optional[Dict[str, str]] = None,
    ) -> str:
        section_key = cls._normalize_section_key(section)
        try:
            section_int = int(section_key)
        except ValueError:
            logger.warning(f"Invalid section '{section}', defaulting to {cls.DEFAULT_ACCENT}")
            return cls.DEFAULT_ACCENT

        if section_int not in cls.SECTIONS:
            logger.warning(f"Invalid section {section_int}, defaulting to {cls.DEFAULT_ACCENT}")
            return cls.DEFAULT_ACCENT

        combo = combo or cls.get_random_combo()
        accent = combo.get(section_key)
        if accent is None:
            logger.warning(f"No accent for section {section_key}, using default")
            return cls.DEFAULT_ACCENT
        return accent

    @classmethod
    def get_accent_metadata(cls, accent: str) -> Dict[str, str]:
        if accent not in cls.ACCENT_METADATA:
            logger.warning(f"Unknown accent '{accent}', falling back to {cls.DEFAULT_ACCENT}")
            return cls.ACCENT_METADATA[cls.DEFAULT_ACCENT].copy()
        return cls.ACCENT_METADATA[accent].copy()

    @classmethod
    def get_section_context(cls, section: Union[int, str], accent: str) -> str:
        try:
            section_int = int(cls._normalize_section_key(section))
        except ValueError:
            section_int = 1
        base = cls.SECTION_CONTEXT.get(section_int, "Listening section")
        meta = cls.get_accent_metadata(accent)
        return f"{base} - {meta['name']} speakers"

    @classmethod
    def build_accent_prompt_context(cls, combo: Dict[str, str]) -> str:
        lines = []
        for section in cls.SECTIONS:
            section_key = cls._normalize_section_key(section)
            accent = combo.get(section_key, cls.DEFAULT_ACCENT)
            meta = cls.get_accent_metadata(accent)
            context = cls.get_section_context(section, accent)
            lines.append(f"Section {section}: {meta['name']} | {context}")
        return "\n".join(lines)

    @classmethod
    def build_voice_overrides(cls, combo: Dict[str, str]) -> Dict[int, str]:
        return {
            section: combo.get(cls._normalize_section_key(section), cls.DEFAULT_ACCENT)
            for section in cls.SECTIONS
        }

    @classmethod
    def get_tts_voice(cls, accent: str) -> str:
        """Return the TTS voice ID for an accent."""
        meta = cls.get_accent_metadata(accent)
        return meta.get('tts_voice', 'en-GB-SoniaNeural')

    @classmethod
    def is_valid_accent(cls, accent: str) -> bool:
        return accent in cls.ACCENT_METADATA

    @classmethod
    def is_valid_combo(cls, combo: Dict[str, str]) -> bool:
        required = {str(s) for s in cls.SECTIONS}
        if set(combo.keys()) != required:
            return False
        return all(cls.is_valid_accent(v) for v in combo.values())

    @classmethod
    def list_accents(cls) -> List[str]:
        return list(cls.ACCENT_METADATA.keys())

    @classmethod
    def list_combinations(cls) -> List[Dict[str, str]]:
        return cls.ACCENT_COMBINATIONS.copy()


# CREATE THE INSTANCE THAT YOUR __init__.py IMPORTS
accent_mixer = AccentMixer()