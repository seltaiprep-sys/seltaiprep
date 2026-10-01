import os
import requests
import json
import logging
from io import BytesIO

logger = logging.getLogger(__name__)

class DeepgramService:
    def __init__(self):
        self.api_key = os.environ.get('DEEPGRAM_API_KEY')
        self.stt_url = "https://api.deepgram.com/v1/listen"
        self.tts_url = "https://api.deepgram.com/v1/speak"
        self.stt_model = os.environ.get('DEEPGRAM_STT_MODEL', 'nova-2')
        self.tts_voice = os.environ.get('DEEPGRAM_TTS_VOICE', 'aura-asteria-en')
        
        if not self.api_key:
            logger.warning("DEEPGRAM_API_KEY not set. Deepgram features disabled.")
    
    def transcribe(self, audio_data, language='en-US', smart_format=True):
        if not self.api_key:
            return {"success": False, "error": "Deepgram API key missing"}
        
        headers = {"Authorization": f"Token {self.api_key}", "Content-Type": "audio/wav"}
        params = {
            "model": self.stt_model,
            "language": language,
            "smart_format": str(smart_format).lower(),
            "punctuate": "true",
            "diarize": "false"
        }
        try:
            response = requests.post(self.stt_url, headers=headers, params=params, data=audio_data, timeout=30)
            response.raise_for_status()
            result = response.json()
            transcript = result.get('results', {}).get('channels', [{}])[0].get('alternatives', [{}])[0].get('transcript', '')
            confidence = result.get('results', {}).get('channels', [{}])[0].get('alternatives', [{}])[0].get('confidence', 0)
            return {"success": True, "transcript": transcript, "confidence": confidence}
        except Exception as e:
            logger.error(f"Deepgram STT error: {e}")
            return {"success": False, "error": str(e)}
    
    def text_to_speech(self, text, voice=None, speed=1.0, output_format='mp3'):
        """
        Deepgram TTS - Text to Speech
         Voice को Query Parameter (params) मा पठाउनु पर्छ, Body मा होइन।
        """
        if not self.api_key:
            logger.error("Deepgram API key missing")
            return None
        
        voice = voice or self.tts_voice
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # Body मा केवल 'text' मात्र
        payload = {"text": text}
        
        # Voice, Encoding, Speed Query Parameters मा
        params = {
            "model": voice,
            "encoding": output_format,
            "speed": speed
        }
        
        try:
            response = requests.post(
                self.tts_url,
                headers=headers,
                params=params, # <-- यहाँ params पठाउनु पर्छ
                json=payload, # <-- Body मा text मात्र
                timeout=30
            )
            response.raise_for_status()
            logger.info(f" Deepgram TTS success: {voice}, {len(response.content)} bytes")
            return BytesIO(response.content)
        except requests.exceptions.HTTPError as e:
            logger.error(f"Deepgram TTS HTTP error: {e} - Response: {response.text if response else 'No response'}")
        except Exception as e:
            logger.error(f"Deepgram TTS error: {e}")
        return None