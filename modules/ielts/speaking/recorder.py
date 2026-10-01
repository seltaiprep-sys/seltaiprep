"""Audio recording with VAD-based silence detection and automatic calibration.

Supports:
- PyAudio and sounddevice backends
- Automatic calibration of silence threshold from ambient noise
- WebRTC VAD (optional) for robust detection
- Timeout callbacks (e.g., for countdown warnings)
- Chunk callbacks for streaming transcription
- Device selection

FIX:
  (1) WebRTC VAD frame size is now computed from sample_rate (was hardcoded 480 = 30ms only at 16kHz)
  (2) Calibration converts int16 to float32 before computing RMS, so the threshold is meaningful
  (3) PyAudio chunk slicing math fixed (was dur*sr/1024*2)
  (4) stop_recording() now waits for the worker thread (join with timeout)
  (5) Sounddevice audio_data access is now thread-safe via a lock
  (6) get_audio_array() guards against empty buffers
  (7) get_duration() returns the last known duration after stopping
  (8) on_silence / on_stop callbacks wrapped in try/except
  (9) PyAudio open failures clean up properly
  (10) Calibration tracks sample count instead of wall-clock time
"""

import os
import time
import wave
import threading
import logging
import numpy as np
from typing import Optional, Callable, Dict, List

logger = logging.getLogger(__name__)

# ---- Audio backends ----
try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    logger.warning("pyaudio not available - install with: pip install pyaudio")

try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False
    logger.warning("sounddevice not available - install with: pip install sounddevice")

# ---- WebRTC VAD (optional) ----
try:
    import webrtcvad
    WEBRTC_AVAILABLE = True
except ImportError:
    WEBRTC_AVAILABLE = False
    logger.warning("webrtcvad not available - install with: pip install webrtcvad")


# ============================================================
# Constants
# ============================================================

# WebRTC VAD only supports 10 / 20 / 30 ms frames at 8/16/32/48 kHz.
WEBRTC_FRAME_MS = 30
CALIBRATION_DURATION_SEC = 0.5
DEFAULT_INT16_SCALE = 32768.0


def _webrtc_frame_samples(sample_rate: int, frame_ms: int = WEBRTC_FRAME_MS) -> int:
    """
    Compute the number of samples per WebRTC VAD frame.

     FIX: Was hardcoded to 480 (only correct at 16 kHz).
    """
    return int(sample_rate * frame_ms / 1000)


def _supports_webrtc_rate(sample_rate: int) -> bool:
    """WebRTC VAD only supports 8k / 16k / 32k / 48k sample rates."""
    return sample_rate in (8000, 16000, 32000, 48000)


def _safe_call(callback: Optional[Callable], *args, **kwargs) -> None:
    """
    Invoke a user-supplied callback and swallow exceptions.

     FIX: Previously a raising callback could kill the recording thread.
    """
    if callback is None:
        return
    try:
        callback(*args, **kwargs)
    except Exception as e:
        logger.error(f"Callback {getattr(callback, '__name__', callback)} failed: {e}")


class AudioRecorder:
    """
    Record audio with automatic silence detection and calibration.

    Features:
    - Automatic calibration of silence threshold from ambient noise
    - WebRTC VAD (optional) for robust voice activity detection
    - RMS fallback if WebRTC not available
    - Timeout callbacks (e.g., for countdown warnings)
    - Chunk callbacks for streaming transcription
    - Device selection
    - Dual backend (PyAudio / sounddevice)

    Note:
        on_chunk callbacks in sounddevice mode run on the audio thread.
        Keep them fast (< 20 ms) to avoid audio dropouts.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        use_webrtc: bool = True,
        silence_duration: float = 1.5,
        min_recording: float = 1.0,
        max_recording: float = 120.0,
        chunk_duration: float = 0.5,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.silence_duration = silence_duration
        self.min_recording = min_recording
        self.max_recording = max_recording
        self.chunk_duration = chunk_duration

        # ---- WebRTC VAD ----
        self.use_webrtc = use_webrtc and WEBRTC_AVAILABLE
        self.vad = None
        if self.use_webrtc:
            if not _supports_webrtc_rate(sample_rate):
                logger.warning(
                    f"WebRTC VAD does not support {sample_rate} Hz. "
                    f"Falling back to RMS detection."
                )
                self.use_webrtc = False
            else:
                try:
                    self.vad = webrtcvad.Vad()
                    self.vad.set_mode(3)
                    logger.info(
                        f"WebRTC VAD enabled "
                        f"(frame: {_webrtc_frame_samples(sample_rate)} samples)"
                    )
                except Exception as e:
                    logger.warning(f"WebRTC VAD init failed: {e}")
                    self.use_webrtc = False

        # Silence threshold (RMS in **float32** units — range 0.0 to 1.0)
        self.silence_threshold = 0.02
        self.is_calibrated = False

        # State
        self.recording = False
        self.frames: List[bytes] = []
        self.audio_data: List[np.ndarray] = []
        self.recording_start = 0.0
        self.final_duration = 0.0 # preserve after stop
        self.silence_start: Optional[float] = None
        self.last_chunk_time = 0.0

        # Callbacks
        self.on_start: Optional[Callable] = None
        self.on_silence: Optional[Callable] = None
        self.on_stop: Optional[Callable] = None
        self.on_level: Optional[Callable] = None
        self.on_chunk: Optional[Callable] = None
        self.timeout_callbacks: Dict[float, Callable] = {}

        # Threads + locks
        self._thread: Optional[threading.Thread] = None
        self._data_lock = threading.Lock() # protect audio_data / frames

        self._check_backend()

    # ============================================================
    # BACKEND
    # ============================================================

    def _check_backend(self):
        if PYAUDIO_AVAILABLE:
            self.backend = 'pyaudio'
        elif SOUNDDEVICE_AVAILABLE:
            self.backend = 'sounddevice'
        else:
            self.backend = None
            logger.error("No audio backend available!")

    # ============================================================
    # CALIBRATION
    # ============================================================

    def calibrate(self, audio_chunk: np.ndarray) -> float:
        """
        Calibrate the silence threshold from ambient noise.

         FIX: Converts int16 to float32 before computing RMS so the
                threshold is comparable to float32 audio everywhere.

        Args:
            audio_chunk: mono audio samples as int16 OR float32

        Returns:
            The calibrated threshold (RMS in float32 scale).
        """
        if audio_chunk is None or len(audio_chunk) == 0:
            return self.silence_threshold

        try:
            # ---- Normalize to float32 in [-1, 1] ----
            if audio_chunk.dtype == np.int16:
                audio_f = audio_chunk.astype(np.float32) / DEFAULT_INT16_SCALE
            else:
                audio_f = audio_chunk.astype(np.float32)

            rms = float(np.sqrt(np.mean(audio_f ** 2)))
        except Exception as e:
            logger.warning(f"Calibration failed: {e}")
            return self.silence_threshold

        # 1.5× the noise floor, clamped to a sane range.
        threshold = max(0.005, min(0.2, rms * 1.5))
        self.silence_threshold = threshold
        self.is_calibrated = True
        logger.info(f"Calibrated silence threshold: {threshold:.4f} (RMS)")
        return threshold

    # ============================================================
    # RECORDING
    # ============================================================

    def start_recording(
        self,
        filename: str = None,
        input_device_index: Optional[int] = None,
        timeout_callbacks: Dict[float, Callable] = None,
    ) -> bool:
        """
        Start recording audio.

        Returns True if the recording thread started.
        """
        if not self.backend:
            logger.error("Cannot start recording: no backend")
            return False

        if self.recording:
            logger.warning("Already recording")
            return False

        # ---- Reset state ----
        self.recording = True
        with self._data_lock:
            self.frames = []
            self.audio_data = []
        self.silence_start = None
        self.recording_start = time.time()
        self.last_chunk_time = self.recording_start
        self.final_duration = 0.0
        self.timeout_callbacks = dict(timeout_callbacks or {})
        self.is_calibrated = False

        _safe_call(self.on_start)

        if self.backend == 'pyaudio':
            self._record_pyaudio(filename, input_device_index)
        elif self.backend == 'sounddevice':
            self._record_sounddevice(filename, input_device_index)

        return True

    # ------------------------------------------------------------
    # PyAudio
    # ------------------------------------------------------------

    def _record_pyaudio(self, filename: str, device_index: Optional[int]):
        """Record using PyAudio (runs in a background thread)."""
        p = None
        stream = None
        try:
            p = pyaudio.PyAudio()
            stream = p.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=1024,
            )
            sample_width = p.get_sample_size(pyaudio.paInt16)
        except Exception as e:
            logger.error(f"PyAudio open failed: {e}")
            if stream:
                try: stream.close()
                except Exception: pass
            if p:
                try: p.terminate()
                except Exception: pass
            self.recording = False
            return

        # Compute WebRTC frame samples for the configured rate
        webrtc_frame_len = _webrtc_frame_samples(self.sample_rate)

        # Compute the number of 1024-sample buffers in chunk_duration
        buffers_per_chunk = max(
            1,
            int(self.chunk_duration * self.sample_rate / 1024),
        )
        calibration_samples = int(CALIBRATION_DURATION_SEC * self.sample_rate)

        def record_loop():
            calibration_buffer = []
            calibration_collected = 0

            try:
                while self.recording:
                    try:
                        data = stream.read(1024, exception_on_overflow=False)
                    except Exception as e:
                        logger.error(f"stream.read failed: {e}")
                        break

                    audio_array = np.frombuffer(data, dtype=np.int16)
                    elapsed = time.time() - self.recording_start

                    # ---- Append raw data ----
                    with self._data_lock:
                        self.frames.append(data)

                    # ---- Calibration (first CALIBRATION_DURATION_SEC) ----
                    if not self.is_calibrated:
                        calibration_buffer.append(audio_array)
                        calibration_collected += len(audio_array)
                        if calibration_collected >= calibration_samples:
                            calib_data = np.concatenate(calibration_buffer)
                            self.calibrate(calib_data)
                            calibration_buffer = []

                    # ---- VAD / RMS ----
                    if self.use_webrtc and self.vad is not None:
                        if len(audio_array) >= webrtc_frame_len:
                            frame_bytes = audio_array[:webrtc_frame_len].tobytes()
                            try:
                                is_speech = self.vad.is_speech(
                                    frame_bytes, self.sample_rate
                                )
                            except Exception:
                                is_speech = False
                        else:
                            is_speech = False
                        # Also compute RMS for level callback
                        rms = float(
                            np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
                        )
                    else:
                        rms = float(
                            np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
                        )
                        is_speech = rms > (
                            self.silence_threshold * DEFAULT_INT16_SCALE
                        )

                    # ---- Level callback ----
                    _safe_call(self.on_level, rms)

                    # ---- Silence detection ----
                    if elapsed > self.min_recording:
                        if not is_speech:
                            if self.silence_start is None:
                                self.silence_start = time.time()
                            elif time.time() - self.silence_start > self.silence_duration:
                                _safe_call(self.on_silence)
                                break
                        else:
                            self.silence_start = None

                    # ---- Max duration ----
                    if elapsed > self.max_recording:
                        break

                    # ---- Timeout callbacks ----
                    for t in list(self.timeout_callbacks.keys()):
                        if elapsed >= t:
                            _safe_call(self.timeout_callbacks.get(t))
                            self.timeout_callbacks.pop(t, None)

                    # ---- Chunk callback ----
                    if self.on_chunk and (elapsed - self.last_chunk_time) >= self.chunk_duration:
                        with self._data_lock:
                            recent = self.frames[-buffers_per_chunk:]
                        if recent:
                            try:
                                chunk_np = np.frombuffer(
                                    b''.join(recent), dtype=np.int16
                                ).astype(np.float32) / DEFAULT_INT16_SCALE
                                _safe_call(self.on_chunk, chunk_np, self.sample_rate)
                            except Exception as e:
                                logger.warning(f"Chunk callback failed: {e}")
                        self.last_chunk_time = elapsed

            finally:
                # ---- Cleanup PyAudio ----
                try:
                    if stream is not None:
                        stream.stop_stream()
                        stream.close()
                except Exception as e:
                    logger.warning(f"PyAudio stream close failed: {e}")
                try:
                    if p is not None:
                        p.terminate()
                except Exception as e:
                    logger.warning(f"PyAudio terminate failed: {e}")

                # ---- Save WAV ----
                if filename:
                    try:
                        with self._data_lock:
                            frames_copy = list(self.frames)
                        if frames_copy:
                            self._save_wav(filename, sample_width, frames_copy)
                    except Exception as e:
                        logger.error(f"Failed to save WAV: {e}")

                # ---- Mark stopped ----
                self.final_duration = time.time() - self.recording_start
                self.recording = False
                _safe_call(self.on_stop)

        self._thread = threading.Thread(target=record_loop, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------
    # sounddevice
    # ------------------------------------------------------------

    def _record_sounddevice(self, filename: str, device_index: Optional[int]):
        """Record using sounddevice (blocking)."""
        with self._data_lock:
            self.audio_data = []

        webrtc_frame_len = _webrtc_frame_samples(self.sample_rate)
        buffers_per_chunk = max(
            1,
            int(self.chunk_duration * self.sample_rate / 1024),
        )
        calibration_samples = int(CALIBRATION_DURATION_SEC * self.sample_rate)
        calibration_buffer: List[np.ndarray] = []
        calibration_collected = 0

        def callback(indata, frames, time_info, status):
            nonlocal calibration_buffer, calibration_collected

            if status:
                logger.warning(f"Recording status: {status}")

            # ---- Store a copy (sounddevice reuses the buffer) ----
            with self._data_lock:
                self.audio_data.append(indata.copy())

            # ---- Level ----
            rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
            _safe_call(self.on_level, rms)

            elapsed = time.time() - self.recording_start

            # ---- Calibration ----
            if not self.is_calibrated:
                calibration_buffer.append(indata.copy())
                calibration_collected += len(indata)
                if calibration_collected >= calibration_samples:
                    try:
                        calib_data = np.concatenate(calibration_buffer, axis=0)
                        self.calibrate(calib_data)
                    except Exception as e:
                        logger.warning(f"Calibration failed: {e}")
                        self.is_calibrated = True
                    calibration_buffer = []

            # ---- VAD ----
            if self.use_webrtc and self.vad is not None:
                int16_data = (indata[:, 0] * 32767).astype(np.int16) \
                    if indata.ndim > 1 else (indata * 32767).astype(np.int16)
                if len(int16_data) >= webrtc_frame_len:
                    frame_bytes = int16_data[:webrtc_frame_len].tobytes()
                    try:
                        is_speech = self.vad.is_speech(
                            frame_bytes, self.sample_rate
                        )
                    except Exception:
                        is_speech = False
                else:
                    is_speech = False
            else:
                is_speech = rms > self.silence_threshold

            # ---- Silence detection ----
            if elapsed > self.min_recording:
                if not is_speech:
                    if self.silence_start is None:
                        self.silence_start = time.time()
                    elif time.time() - self.silence_start > self.silence_duration:
                        _safe_call(self.on_silence)
                        raise sd.CallbackStop()
                else:
                    self.silence_start = None

            # ---- Max duration ----
            if elapsed > self.max_recording:
                raise sd.CallbackStop()

            # ---- Timeout callbacks ----
            for t in list(self.timeout_callbacks.keys()):
                if elapsed >= t:
                    _safe_call(self.timeout_callbacks.get(t))
                    self.timeout_callbacks.pop(t, None)

            # ---- Chunk callback (runs on audio thread — keep it fast!) ----
            if self.on_chunk and (elapsed - self.last_chunk_time) >= self.chunk_duration:
                with self._data_lock:
                    recent = self.audio_data[-buffers_per_chunk:]
                if recent:
                    try:
                        chunk_np = np.concatenate(recent, axis=0)
                        if chunk_np.ndim > 1:
                            chunk_np = chunk_np[:, 0]
                        _safe_call(
                            self.on_chunk, chunk_np.astype(np.float32), self.sample_rate
                        )
                    except Exception as e:
                        logger.warning(f"Chunk callback failed: {e}")
                self.last_chunk_time = elapsed

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                callback=callback,
                device=device_index,
                blocksize=1024,
            ):
                while self.recording:
                    sd.sleep(50)
        except sd.CallbackStop:
            pass
        except Exception as e:
            logger.error(f"Sounddevice error: {e}", exc_info=True)

        # ---- Save WAV ----
        if filename:
            try:
                with self._data_lock:
                    data_copy = list(self.audio_data)
                if data_copy:
                    audio = np.concatenate(data_copy, axis=0)
                    self._save_wav_sd(filename, audio)
            except Exception as e:
                logger.error(f"Failed to save sounddevice WAV: {e}")

        self.final_duration = time.time() - self.recording_start
        self.recording = False
        _safe_call(self.on_stop)

    # ============================================================
    # STOP
    # ============================================================

    def stop_recording(self, wait: bool = True, timeout: float = 5.0) -> None:
        """
        Stop recording.

         FIX: By default waits for the worker thread to finish so the
                WAV file is fully written before returning.

        Args:
            wait: If True, block until the thread exits (up to timeout).
            timeout: Max seconds to wait for the thread.
        """
        self.recording = False
        if wait and self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning(
                    f"Recording thread did not stop within {timeout}s"
                )

    # ============================================================
    # SAVE HELPERS
    # ============================================================

    def _save_wav(self, filename: str, sample_width: int, frames: List[bytes]):
        """Save PyAudio raw frames to WAV."""
        dirpath = os.path.dirname(filename)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        with wave.open(filename, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(self.sample_rate)
            wf.writeframes(b''.join(frames))
        logger.info(f"Recording saved: {filename} ({len(frames)} frames)")

    def _save_wav_sd(self, filename: str, audio: np.ndarray):
        """Save sounddevice float audio to WAV."""
        dirpath = os.path.dirname(filename)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        audio_int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
        with wave.open(filename, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_int16.tobytes())
        logger.info(
            f"Recording saved: {filename} "
            f"({len(audio_int16)} samples)"
        )

    # ============================================================
    # UTILITY
    # ============================================================

    def get_duration(self) -> float:
        """
        Return recording duration in seconds.

         FIX: Returns the last known duration after recording stops.
        """
        if self.recording:
            return time.time() - self.recording_start
        return self.final_duration

    def is_speaking(self) -> bool:
        """Return True if the user is currently speaking."""
        return self.recording and self.silence_start is None

    def get_audio_array(self) -> np.ndarray:
        """
        Return recorded audio as float32 array in [-1, 1].

         FIX: Guards against empty buffers and safely copies under lock.
        """
        try:
            if self.backend == 'pyaudio':
                with self._data_lock:
                    frames = list(self.frames)
                if not frames:
                    return np.array([], dtype=np.float32)
                return (
                    np.frombuffer(b''.join(frames), dtype=np.int16).astype(np.float32)
                    / DEFAULT_INT16_SCALE
                )
            elif self.backend == 'sounddevice':
                with self._data_lock:
                    data = list(self.audio_data)
                if not data:
                    return np.array([], dtype=np.float32)
                audio = np.concatenate(data, axis=0)
                if audio.ndim > 1:
                    audio = audio[:, 0]
                return audio.astype(np.float32)
        except Exception as e:
            logger.error(f"get_audio_array failed: {e}")
        return np.array([], dtype=np.float32)

    def cleanup(self) -> None:
        """Ensure the recording thread has stopped and locks are released."""
        try:
            self.stop_recording(wait=True, timeout=3.0)
        except Exception as e:
            logger.warning(f"cleanup failed: {e}")


# ============================================================
# FACTORY
# ============================================================

def create_recorder(
    sample_rate: int = 16000,
    channels: int = 1,
    use_webrtc: bool = True,
    silence_duration: float = 1.5,
    min_recording: float = 1.0,
    max_recording: float = 120.0,
    chunk_duration: float = 0.5,
) -> AudioRecorder:
    """Factory function for AudioRecorder."""
    return AudioRecorder(
        sample_rate=sample_rate,
        channels=channels,
        use_webrtc=use_webrtc,
        silence_duration=silence_duration,
        min_recording=min_recording,
        max_recording=max_recording,
        chunk_duration=chunk_duration,
    )


__all__ = [
    'AudioRecorder',
    'create_recorder',
    'PYAUDIO_AVAILABLE',
    'SOUNDDEVICE_AVAILABLE',
    'WEBRTC_AVAILABLE',
]