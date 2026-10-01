# modules/ukvi/transcriber.py
"""UKVI Audio Transcription — uses Deepgram API."""

import os
import logging
import requests

logger = logging.getLogger(__name__)


class UKVITranscriber:
    """Transcribe audio files using Deepgram API."""

    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ.get('DEEPGRAM_API_KEY')
        self.url = "https://api.deepgram.com/v1/listen"

    def transcribe(self, audio_path: str) -> str:
        """Transcribe audio file using Deepgram. Returns '' on failure."""
        if not os.path.exists(audio_path):
            logger.error(f"Audio file not found: {audio_path}")
            return ""

        if not self.api_key:
            logger.warning("No Deepgram API key; returning empty transcript.")
            return ""

        try:
            with open(audio_path, 'rb') as f:
                audio_data = f.read()

            headers = {
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "audio/wav",
            }
            params = {
                "model": "nova-2",
                "language": "en-US",
                "smart_format": "true",
                "punctuate": "true",
            }

            response = requests.post(
                self.url, headers=headers, params=params,
                data=audio_data, timeout=30,
            )

            if response.status_code == 200:
                result = response.json()
                transcript = (
                    result.get('results', {})
                    .get('channels', [{}])[0]
                    .get('alternatives', [{}])[0]
                    .get('transcript', '')
                )
                logger.info(f"Transcription successful: {len(transcript)} chars")
                return transcript.strip()

            logger.error(f"Deepgram error: {response.status_code}")
            return ""

        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return ""


# Alias for backward compatibility
Transcriber = UKVITranscriber

__all__ = ['UKVITranscriber', 'Transcriber']