# modules/ukvi/audio/recorder.py
"""UKVI Audio Recording using sounddevice and Whisper transcription"""

import os
import logging
import numpy as np
import time

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
    import soundfile as sf
    RECORDING_AVAILABLE = True
except ImportError:
    RECORDING_AVAILABLE = False
    logger.warning("sounddevice not available. Install: pip install sounddevice")


class UKVIRecorder:
    def __init__(self, samplerate=16000, channels=1):
        self.samplerate = samplerate
        self.channels = channels
        self.recording = False
        self.frames = []
        self.filename = None
        self.silence_duration = 2.0
        self.max_recording = 45

    def start_recording(self, filename: str):
        if not RECORDING_AVAILABLE:
            logger.error("Recording not available")
            return
        self.filename = filename
        self.frames = []
        self.recording = True
        logger.info(f"Recording started: {filename}")

        def callback(indata, frames, time, status):
            if status:
                logger.warning(f"Recording status: {status}")
            if self.recording:
                self.frames.append(indata.copy())

        self.stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=self.channels,
            callback=callback,
            dtype='float32'
        )
        self.stream.start()
        self.start_time = time.time()

    def stop_recording(self):
        if not RECORDING_AVAILABLE:
            return
        self.recording = False
        if hasattr(self, 'stream'):
            self.stream.stop()
            self.stream.close()
        logger.info("Recording stopped")
        self._save_audio()

    def _save_audio(self):
        if not self.frames or not self.filename:
            return
        audio_data = np.concatenate(self.frames, axis=0)
        sf.write(self.filename, audio_data, self.samplerate)
        logger.info(f"Audio saved: {self.filename}")

    def get_duration(self) -> float:
        if not hasattr(self, 'start_time'):
            return 0
        return time.time() - self.start_time

    def get_audio_array(self) -> np.ndarray:
        if not self.frames:
            return np.array([])
        return np.concatenate(self.frames, axis=0)