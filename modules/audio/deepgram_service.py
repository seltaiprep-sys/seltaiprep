"""
Deepgram Service
================
Direct wrapper around Deepgram APIs used across the application.
"""

import os
import logging
from typing import Optional, Dict, Any, List
import requests

logger = logging.getLogger(__name__)

DEEPGRAM_API_KEY = os.environ.get('DEEPGRAM_API_KEY', '').strip()
DEEPGRAM_TTS_URL = "https://api.deepgram.com/v1/speak"
DEEPGRAM_STT_URL = "https://api.deepgram.com/v1/listen"
DEEPGRAM_TTS_TIMEOUT = int(os.environ.get('DEEPGRAM_TTS_TIMEOUT', '30'))
DEEPGRAM_STT_TIMEOUT = int(os.environ.get('DEEPGRAM_STT_TIMEOUT', '30'))

AURA2_VOICES = [
    "aura-2-luna-en", "aura-2-stella-en", "aura-2-athena-en",
    "aura-2-hera-en", "aura-2-orion-en", "aura-2-arcas-en",
    "aura-2-perseus-en", "aura-2-angus-en", "aura-2-orpheus-en",
]

AURA1_VOICES = [
    "aura-asteria-en", "aura-luna-en", "aura-stella-en",
    "aura-orion-en", "aura-arcas-en", "aura-perseus-en",
    "aura-angus-en", "aura-orpheus-en", "aura-helios-en",
]

DEFAULT_TTS_VOICE = "aura-2-luna-en"


class DeepgramService:
    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = (api_key or DEEPGRAM_API_KEY).strip()
        self.available = bool(self.api_key)
        self.stats = {
            'tts_calls': 0, 'tts_failures': 0,
            'stt_calls': 0, 'stt_failures': 0,
            'bytes_sent': 0, 'bytes_received': 0,
        }
        if self.available:
            logger.info(" DeepgramService initialized (API key present)")
        else:
            logger.warning(" DeepgramService: no API key")

    def is_available(self) -> bool:
        return self.available

    def list_tts_voices(self, tier: str = "aura-2") -> List[str]:
        return list(AURA1_VOICES) if tier == "aura-1" else list(AURA2_VOICES)

    def synthesize(self, text: str, voice: str = DEFAULT_TTS_VOICE,
                   speed: float = 1.0, encoding: str = "mp3") -> Optional[bytes]:
        if not self.available or not text or not text.strip():
            return None

        text = text.strip()[:2000]
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
        }
        params = {"model": voice, "encoding": encoding}
        if speed != 1.0 and voice.startswith("aura-2-"):
            params["speed"] = f"{speed:.2f}"

        self.stats['tts_calls'] += 1
        try:
            r = requests.post(DEEPGRAM_TTS_URL, headers=headers,
                              params=params, json={"text": text},
                              timeout=DEEPGRAM_TTS_TIMEOUT)
            if r.status_code != 200:
                self.stats['tts_failures'] += 1
                logger.warning(f"Deepgram TTS HTTP {r.status_code}: {r.text[:200]}")
                return None
            audio = r.content
            if len(audio) < 500:
                self.stats['tts_failures'] += 1
                return None
            self.stats['bytes_received'] += len(audio)
            return audio
        except requests.Timeout:
            self.stats['tts_failures'] += 1
            logger.warning(f"Deepgram TTS timeout")
            return None
        except Exception as e:
            self.stats['tts_failures'] += 1
            logger.warning(f"Deepgram TTS error: {e}")
            return None

    def transcribe(self, audio_bytes: bytes, content_type: str = "audio/webm",
                   model: str = "nova-2", language: str = "en-US",
                   smart_format: bool = True, punctuate: bool = True,
                   diarize: bool = False, utterances: bool = True) -> Dict[str, Any]:
        if not self.available:
            return {'success': False, 'error': 'Deepgram API key not configured'}
        if not audio_bytes:
            return {'success': False, 'error': 'Empty audio data'}

        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": content_type or "audio/webm",
        }
        params = {
            "model": model, "language": language,
            "smart_format": "true" if smart_format else "false",
            "punctuate": "true" if punctuate else "false",
            "diarize": "true" if diarize else "false",
            "utterances": "true" if utterances else "false",
        }

        self.stats['stt_calls'] += 1
        self.stats['bytes_sent'] += len(audio_bytes)

        try:
            r = requests.post(DEEPGRAM_STT_URL, headers=headers,
                              params=params, data=audio_bytes,
                              timeout=DEEPGRAM_STT_TIMEOUT)
            if r.status_code != 200:
                self.stats['stt_failures'] += 1
                return {'success': False,
                        'error': f'Transcription service error: {r.status_code}'}
            return self._normalize_stt_response(r.json())
        except requests.Timeout:
            self.stats['stt_failures'] += 1
            return {'success': False, 'error': 'Transcription timeout'}
        except Exception as e:
            self.stats['stt_failures'] += 1
            logger.warning(f"Deepgram STT error: {e}")
            return {'success': False, 'error': str(e)}

    def _normalize_stt_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            results = payload.get('results', {}) or {}
            channels = results.get('channels', []) or []
            alts = (channels[0] if channels else {}).get('alternatives', []) or []
            alt = alts[0] if alts else {}
            return {
                'success': True,
                'transcript': (alt.get('transcript') or '').strip(),
                'words': alt.get('words') or [],
                'utterances': results.get('utterances') or [],
                'confidence': float(alt.get('confidence') or 0.0),
                'duration': float((payload.get('metadata') or {}).get('duration') or 0.0),
                'word_count': len(alt.get('words') or []),
            }
        except Exception as e:
            return {'success': False, 'error': f'Invalid response format: {e}'}

    def get_client_token(self) -> Optional[Dict[str, Any]]:
        if not self.available:
            return None
        return {
            'key': self.api_key,
            'models': ['nova-3', 'nova-2', 'enhanced', 'base'],
            'features': ['punctuate', 'interim_results', 'diarize', 'smart_format'],
        }

    def get_stats(self) -> Dict[str, Any]:
        return {**self.stats, 'available': self.available,
                'tts_voices': len(AURA2_VOICES)}


try:
    deepgram_service = DeepgramService()
except Exception as _e:
    logger.exception(f"Failed to initialize DeepgramService: {_e}")
    deepgram_service = None