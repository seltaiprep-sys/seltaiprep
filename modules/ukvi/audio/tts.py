# modules/ukvi/audio/tts.py
"""UKVI Text-to-Speech using Edge TTS (fallback to gTTS)"""

import os
import logging
import asyncio

logger = logging.getLogger(__name__)

try:
    import edge_tts
    EDGE_AVAILABLE = True
except ImportError:
    EDGE_AVAILABLE = False
    logger.warning("Edge TTS not available. Install: pip install edge-tts")

try:
    import gtts
    GTTS_AVAILABLE = True
except ImportError:
    GTTS_AVAILABLE = False
    logger.warning("gTTS not available. Install: pip install gtts")

TTS_AVAILABLE = EDGE_AVAILABLE or GTTS_AVAILABLE


class UKVIVoice:
    def __init__(self):
        self.edge_available = EDGE_AVAILABLE
        self.gtts_available = GTTS_AVAILABLE
        self.voice_pool = [
            'en-GB-SoniaNeural', 'en-GB-RyanNeural', 'en-US-JennyNeural',
            'en-AU-NatashaNeural', 'en-IN-NeerjaNeural'
        ]
        self.current_voice = 'en-GB-SoniaNeural'

    def speak(self, text: str, filename: str) -> str:
        """Generate audio file and return path."""
        if self.edge_available:
            try:
                voice = self.current_voice
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                communicate = edge_tts.Communicate(text, voice)
                loop.run_until_complete(communicate.save(filename))
                loop.close()
                logger.info(f"Edge TTS saved to {filename}")
                return filename
            except Exception as e:
                logger.warning(f"Edge TTS failed: {e}")

        if self.gtts_available:
            try:
                tts = gtts.gTTS(text, lang='en', tld='co.uk')
                tts.save(filename)
                logger.info(f"gTTS saved to {filename}")
                return filename
            except Exception as e:
                logger.warning(f"gTTS failed: {e}")

        return None