# modules/ukvi/audio/__init__.py
# (Minimal – can be extended)
from .tts import UKVIVoice, TTS_AVAILABLE
from .recorder import UKVIRecorder, RECORDING_AVAILABLE

__all__ = ['UKVIVoice', 'TTS_AVAILABLE', 'UKVIRecorder', 'RECORDING_AVAILABLE']