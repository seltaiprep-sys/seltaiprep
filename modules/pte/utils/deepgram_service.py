# modules/pte/utils/deepgram_service.py
"""Deepgram TTS + STT service for PTE.

 UPDATED: Aura-1 → Aura-2 aware voice validation + conditional speed.

Deepgram model capability matrix:
  • aura-1-* / aura-* → does NOT support 'speed' parameter (HTTP 400)
  • aura-2-* → DOES support 'speed' parameter (0.7 – 1.5)

Fix:
  • Accept both Aura-1 and Aura-2 voices.
  • Only send 'speed' query param for Aura-2 voices.
  • Default TTS voice upgraded to Aura-2.
"""
import os
import requests
import logging
from io import BytesIO

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Valid Deepgram voices — both Aura-1 AND Aura-2 generations
# ═══════════════════════════════════════════════════════════════════
VALID_DEEPGRAM_VOICES = {
    # ── Aura-1 (older generation) ─────────────────────────────────
    'aura-asteria-en', 'aura-luna-en', 'aura-stella-en',
    'aura-athena-en', 'aura-hera-en',
    'aura-orion-en', 'aura-arcas-en', 'aura-perseus-en',
    'aura-angus-en', 'aura-helios-en', 'aura-zeus-en', 'aura-orpheus-en',

    # ── Aura-2 (newer generation — supports 'speed') ──────────────
    'aura-2-asteria-en', 'aura-2-luna-en', 'aura-2-stella-en',
    'aura-2-athena-en', 'aura-2-hera-en',
    'aura-2-orion-en', 'aura-2-arcas-en', 'aura-2-perseus-en',
    'aura-2-angus-en', 'aura-2-helios-en', 'aura-2-zeus-en', 'aura-2-orpheus-en',
    'aura-2-pandora-en', 'aura-2-apollo-en',
    'aura-2-thalia-en', 'aura-2-andromeda-en',
    'aura-2-helena-en', 'aura-2-aurora-en',
    'aura-2-juno-en', 'aura-2-minerva-en',
}

# Valid encodings
VALID_ENCODINGS = {'mp3', 'linear16', 'mulaw', 'flac', 'aac', 'opus'}

# Speed bounds enforced by Deepgram
SPEED_MIN = 0.7
SPEED_MAX = 1.5
DEFAULT_SPEED = 1.0


def _is_aura2(voice: str) -> bool:
    """Return True if the voice is an Aura-2 model (supports 'speed')."""
    return isinstance(voice, str) and voice.startswith('aura-2-')


class DeepgramService:
    def __init__(self):
        self.api_key = os.environ.get('DEEPGRAM_API_KEY')
        self.stt_url = "https://api.deepgram.com/v1/listen"
        self.tts_url = "https://api.deepgram.com/v1/speak"
        self.stt_model = os.environ.get('DEEPGRAM_STT_MODEL', 'nova-2')

        # Default upgraded to Aura-2 (supports speed, better prosody)
        default_voice = os.environ.get('DEEPGRAM_TTS_VOICE', 'aura-2-asteria-en')
        if default_voice not in VALID_DEEPGRAM_VOICES:
            logger.warning(
                f" DEEPGRAM_TTS_VOICE='{default_voice}' not in known voices — "
                f"falling back to 'aura-2-asteria-en'"
            )
            default_voice = 'aura-2-asteria-en'
        self.tts_voice = default_voice

        if not self.api_key:
            logger.warning("DEEPGRAM_API_KEY not set. Deepgram features disabled.")

    # ═══════════════════════════════════════════════════════════════
    # STT — Speech to Text
    # ═══════════════════════════════════════════════════════════════
    def transcribe(self, audio_data, language='en-US', smart_format=True,
                   content_type='audio/webm'):
        """
        Transcribe audio using Deepgram STT (nova-2 by default).

        Args:
            audio_data: raw audio bytes
            language: BCP-47 language code
            smart_format: apply smart formatting
            content_type: MIME type of audio ('audio/webm' or 'audio/wav')

        Returns:
            dict with success, transcript, confidence, words, utterances
        """
        if not self.api_key:
            return {"success": False, "error": "Deepgram API key missing"}

        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": content_type
        }
        params = {
            "model": self.stt_model,
            "language": language,
            "smart_format": str(smart_format).lower(),
            "punctuate": "true",
            "diarize": "false",
            "utterances": "true",
        }

        response = None
        try:
            response = requests.post(
                self.stt_url,
                headers=headers,
                params=params,
                data=audio_data,
                timeout=30
            )
            response.raise_for_status()
            result = response.json()

            channel = result.get('results', {}).get('channels', [{}])[0]
            alt = channel.get('alternatives', [{}])[0]
            transcript = alt.get('transcript', '')
            confidence = alt.get('confidence', 0.0)
            words = alt.get('words', [])
            utterances = result.get('results', {}).get('utterances', [])

            return {
                "success": True,
                "transcript": transcript,
                "confidence": confidence,
                "words": words,
                "utterances": utterances,
            }
        except requests.exceptions.HTTPError as e:
            err_body = response.text if response is not None else 'No response'
            logger.error(f"Deepgram STT HTTP error: {e} - {err_body}")
            return {"success": False, "error": f"HTTP {e.response.status_code if e.response else 'unknown'}"}
        except Exception as e:
            logger.error(f"Deepgram STT error: {e}")
            return {"success": False, "error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # TTS — Text to Speech
    # ═══════════════════════════════════════════════════════════════
    def text_to_speech(self, text, voice=None, speed=1.0, output_format='mp3',
                       return_bytes=False):
        """
        Deepgram Aura TTS — Text to Speech.

        Query parameters (sent to Deepgram):
            model = voice name (e.g. 'aura-2-asteria-en')
            encoding = 'mp3' | 'linear16' | etc.
            speed = 0.7 – 1.5 ←  ONLY sent for Aura-2 voices

        Returns:
            BytesIO by default, or raw bytes if return_bytes=True.
            None if generation failed.
        """
        if not self.api_key:
            logger.error("Deepgram API key missing")
            return None

        if not text or not text.strip():
            logger.warning("Deepgram TTS: empty text")
            return None

        # ── Voice resolution ──────────────────────────────────────
        voice = voice or self.tts_voice
        if voice not in VALID_DEEPGRAM_VOICES:
            logger.warning(f" Unknown voice '{voice}', using '{self.tts_voice}'")
            voice = self.tts_voice

        # ── Encoding resolution ───────────────────────────────────
        if output_format not in VALID_ENCODINGS:
            logger.warning(f" Invalid encoding '{output_format}', using 'mp3'")
            output_format = 'mp3'

        # ── Build request ─────────────────────────────────────────
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {"text": text}

        params = {
            "model": voice,
            "encoding": output_format,
        }

        # CRITICAL: only Aura-2 supports 'speed'.
        # Sending it to Aura-1 returns:
        # 400 Bad Request: "Requested model does not support the 'speed' parameter."
        if _is_aura2(voice):
            try:
                safe_speed = max(SPEED_MIN, min(SPEED_MAX, float(speed)))
            except (ValueError, TypeError):
                safe_speed = DEFAULT_SPEED
            params["speed"] = safe_speed
        else:
            # Aura-1 — silently ignore speed (do not send the query param)
            if speed is not None and float(speed) != DEFAULT_SPEED:
                logger.debug(
                    f" 'speed' parameter ignored for Aura-1 voice '{voice}'"
                )

        response = None
        try:
            response = requests.post(
                self.tts_url,
                headers=headers,
                params=params,
                json=payload,
                timeout=30
            )
            response.raise_for_status()

            audio_bytes = response.content
            speed_info = (
                f", speed={params['speed']}"
                if 'speed' in params else ", speed=<ignored>"
            )
            logger.info(
                f" Deepgram TTS: {voice}{speed_info}, {len(audio_bytes)} bytes"
            )

            if return_bytes:
                return audio_bytes
            return BytesIO(audio_bytes)

        except requests.exceptions.HTTPError as e:
            err_body = response.text if response is not None else 'No response'
            logger.error(f"Deepgram TTS HTTP error: {e} - {err_body}")
        except requests.exceptions.Timeout:
            logger.error("Deepgram TTS timeout")
        except Exception as e:
            logger.error(f"Deepgram TTS error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────
deepgram_service = DeepgramService()