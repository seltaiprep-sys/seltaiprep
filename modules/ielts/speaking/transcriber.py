"""Speech transcription for IELTS Speaking module - Graceful degradation with model caching

FIX:
  (1) `Dict[str, any]` -> `Dict[str, Any]` (typing correctness)
  (2) Added MIN_AUDIO_DURATION_SECONDS = 0.3s guard
  (3) `_resample_audio` returns None on failure (was silently returning wrong-rate audio)
  (4) NEW: `transcribe_detailed()` returns word-level confidence + segments
  (5) Timestamps method now returns richer segments (id, text, start, end, words)
  (6) Better silence detection with duration awareness
  (7) Docstring clarified: transcribe() returns '' on failure
"""

import threading
import numpy as np
import logging
from typing import Optional, List, Dict, Any, Union

logger = logging.getLogger(__name__)

# ---- Constants ----
MIN_AUDIO_DURATION_SECONDS = 0.3
TARGET_SAMPLE_RATE = 16000

# ---- Whisper ----
try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    logger.warning("Whisper not available - install with: pip install openai-whisper")

# ---- Faster-Whisper ----
try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False
    logger.warning("Faster-Whisper not available - install with: pip install faster-whisper")

# ---- Librosa for resampling ----
try:
    import librosa
    LIBROSA_AVAILABLE = True
except ImportError:
    LIBROSA_AVAILABLE = False
    logger.warning("librosa not available - resampling will be disabled")

# ---- Global model caches (thread-safe) ----
_whisper_models: Dict[str, Any] = {}
_faster_whisper_models: Dict[str, Any] = {}
_model_locks = {
    'whisper': threading.Lock(),
    'faster': threading.Lock(),
}


def _get_whisper_model(model_size: str) -> Optional[Any]:
    """Get or load a Whisper model (thread-safe, cached by size)."""
    if not WHISPER_AVAILABLE:
        return None

    if model_size in _whisper_models:
        return _whisper_models[model_size]

    with _model_locks['whisper']:
        if model_size in _whisper_models:
            return _whisper_models[model_size]
        try:
            logger.info(f"Loading Whisper {model_size} model...")
            model = whisper.load_model(model_size)
            _whisper_models[model_size] = model
            logger.info(f"Whisper {model_size} model loaded")
            return model
        except Exception as e:
            logger.error(f"Failed to load Whisper {model_size}: {e}")
            return None


def _get_faster_whisper_model(model_size: str) -> Optional[Any]:
    """Get or load a Faster-Whisper model (thread-safe, cached by size)."""
    if not FASTER_WHISPER_AVAILABLE:
        return None

    if model_size in _faster_whisper_models:
        return _faster_whisper_models[model_size]

    with _model_locks['faster']:
        if model_size in _faster_whisper_models:
            return _faster_whisper_models[model_size]
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
            logger.info(f"Loading Faster-Whisper {model_size} on {device} ({compute_type})...")
            model = WhisperModel(model_size, device=device, compute_type=compute_type)
            _faster_whisper_models[model_size] = model
            logger.info(f"Faster-Whisper {model_size} model loaded")
            return model
        except Exception as e:
            logger.error(f"Failed to load Faster-Whisper {model_size}: {e}")
            return None


def _resample_audio(
    audio: np.ndarray,
    orig_sr: int,
    target_sr: int = TARGET_SAMPLE_RATE,
) -> Optional[np.ndarray]:
    """
    Resample audio to target sample rate.

     FIX: Returns None on failure (was silently returning wrong-rate audio).
    """
    if audio is None or len(audio) == 0:
        return None

    if orig_sr == target_sr:
        return audio.astype(np.float32)

    if not LIBROSA_AVAILABLE:
        logger.warning("librosa not available - cannot resample audio")
        return None

    try:
        return librosa.resample(
            audio.astype(np.float32),
            orig_sr=orig_sr,
            target_sr=target_sr,
        )
    except Exception as e:
        logger.warning(f"Resampling failed: {e}")
        return None


class WhisperTranscriber:
    """
    Transcribe speech using Whisper models with graceful degradation.

    Features:
    - Supports both standard Whisper and Faster-Whisper
    - Thread-safe model caching by model size
    - Auto-detects GPU for Faster-Whisper
    - Configurable silence threshold with calibration support
    - Timestamp transcription for detailed analysis
    - Word-level confidence (NEW)

    All public methods return '' / [] on failure — never raise.
    """

    def __init__(
        self,
        model_size: str = "base",
        silence_threshold: float = 0.01,
        prefer_faster: bool = True,
    ):
        """
        Args:
            model_size: Whisper model size ("tiny", "base", "small", "medium", "large")
            silence_threshold: RMS threshold for silence detection (0-1)
            prefer_faster: If True, try Faster-Whisper first (if available)
        """
        self.model_size = model_size
        self.silence_threshold = silence_threshold
        self.prefer_faster = prefer_faster
        logger.info(
            f"WhisperTranscriber initialized: model={model_size}, faster={prefer_faster}"
        )

    # ============================================================
    # THRESHOLD MANAGEMENT
    # ============================================================

    def set_silence_threshold(self, threshold: float) -> None:
        """Set the silence threshold (can be calibrated from audio)."""
        self.silence_threshold = threshold
        logger.info(f"Silence threshold set to {threshold:.4f}")

    def calibrate_threshold(self, audio: np.ndarray) -> float:
        """
        Calibrate the silence threshold from a short audio segment (noise floor).
        Returns the calibrated threshold.
        """
        if audio is None or len(audio) < 100:
            return self.silence_threshold

        try:
            rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        except Exception as e:
            logger.warning(f"Threshold calibration failed: {e}")
            return self.silence_threshold

        # Set threshold to 1.5x the noise floor, with a minimum of 0.005
        calibrated = max(0.005, rms * 1.5)
        self.silence_threshold = calibrated
        logger.info(
            f"Calibrated silence threshold: {calibrated:.4f} (noise RMS: {rms:.4f})"
        )
        return calibrated

    def is_silence(self, audio: np.ndarray) -> bool:
        """Check if audio is mostly silence using the current threshold."""
        if audio is None or len(audio) == 0:
            return True
        try:
            rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        except Exception:
            return True
        return rms < self.silence_threshold

    # ============================================================
    # INTERNAL HELPERS
    # ============================================================

    def _prepare_audio(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> Optional[np.ndarray]:
        """
        Shared pre-processing: validate, resample to 16kHz.

         FIX: Returns None on any failure (was silently proceeding).
        """
        # ---- Normalize ----
        if audio is None:
            return None

        try:
            audio = np.asarray(audio, dtype=np.float32)
        except Exception as e:
            logger.warning(f"Cannot convert audio to float32: {e}")
            return None

        if len(audio) == 0:
            return None

        if sr is None or sr <= 0:
            logger.warning(f"Invalid sample rate: {sr}")
            return None

        # ---- Duration guard ----
        duration = len(audio) / float(sr)
        if duration < MIN_AUDIO_DURATION_SECONDS:
            logger.debug(
                f"Audio too short ({duration:.2f}s < "
                f"{MIN_AUDIO_DURATION_SECONDS}s) - skipping"
            )
            return None

        # ---- Silence guard ----
        if self.is_silence(audio):
            logger.debug("Silence detected - skipping transcription")
            return None

        # ---- Resample ----
        audio_16k = _resample_audio(audio, sr, TARGET_SAMPLE_RATE)
        if audio_16k is None:
            logger.warning("Resample returned None - cannot transcribe")
            return None

        return audio_16k.astype(np.float32)

    # ============================================================
    # PUBLIC: transcribe (returns str)
    # ============================================================

    def transcribe(self, audio: np.ndarray, sr: int = 16000) -> str:
        """
        Transcribe speech to text.

        Args:
            audio: Audio signal as numpy array
            sr: Sample rate (Hz)

        Returns:
            Transcribed text string.
            Returns '' if silence, too short, or on failure.
        """
        audio_16k = self._prepare_audio(audio, sr)
        if audio_16k is None:
            return ""

        # Try Faster-Whisper first if preferred
        if self.prefer_faster and FASTER_WHISPER_AVAILABLE:
            try:
                model = _get_faster_whisper_model(self.model_size)
                if model:
                    segments, _ = model.transcribe(
                        audio_16k,
                        language="en",
                        task="transcribe",
                        beam_size=5,
                        vad_filter=False,
                    )
                    text = " ".join(s.text.strip() for s in segments if s.text)
                    if text:
                        logger.debug(f"Faster-Whisper: {text[:80]}...")
                        return text
            except Exception as e:
                logger.warning(f"Faster-Whisper failed: {e}")

        # Fallback to standard Whisper
        if WHISPER_AVAILABLE:
            try:
                model = _get_whisper_model(self.model_size)
                if model:
                    result = model.transcribe(
                        audio_16k,
                        language="en",
                        task="transcribe",
                        temperature=0,
                        fp16=False,
                    )
                    text = result.get("text", "").strip()
                    if text:
                        logger.debug(f"Whisper: {text[:80]}...")
                        return text
            except Exception as e:
                logger.error(f"Whisper failed: {e}")

        logger.warning("No transcription available")
        return ""

    # ============================================================
    # PUBLIC: transcribe_with_timestamps
    # ============================================================

    def transcribe_with_timestamps(
        self,
        audio: np.ndarray,
        sr: int = 16000,
        word_timestamps: bool = False,
    ) -> List[Dict]:
        """
        Transcribe with segment-level (or word-level) timestamps.

        Args:
            audio: Audio signal
            sr: Sample rate
            word_timestamps: If True, include per-word timing where available

        Returns:
            List of dicts with keys:
                - 'text': str
                - 'start': float (seconds)
                - 'end': float (seconds)
                - 'words': optional List[Dict] when word_timestamps=True
            Empty list on failure.
        """
        audio_16k = self._prepare_audio(audio, sr)
        if audio_16k is None:
            return []

        # Try Faster-Whisper with timestamps
        if FASTER_WHISPER_AVAILABLE:
            try:
                model = _get_faster_whisper_model(self.model_size)
                if model:
                    segments, _ = model.transcribe(
                        audio_16k,
                        language="en",
                        task="transcribe",
                        beam_size=5,
                        vad_filter=False,
                        word_timestamps=word_timestamps,
                    )
                    result: List[Dict] = []
                    for seg in segments:
                        if not seg.text:
                            continue
                        entry = {
                            "text": seg.text.strip(),
                            "start": round(float(seg.start), 2),
                            "end": round(float(seg.end), 2),
                        }
                        if word_timestamps and hasattr(seg, "words") and seg.words:
                            entry["words"] = [
                                {
                                    "word": w.word.strip(),
                                    "start": round(float(w.start), 2),
                                    "end": round(float(w.end), 2),
                                    "probability": round(float(getattr(w, "probability", 0.0)), 3),
                                }
                                for w in seg.words
                                if w.word and w.word.strip()
                            ]
                        result.append(entry)
                    if result:
                        return result
            except Exception as e:
                logger.warning(f"Timestamp transcription failed: {e}")

        # Fallback: basic transcription without timestamps
        text = self.transcribe(audio, sr)
        if text:
            duration = len(audio) / sr if sr > 0 else 0
            return [{
                "text": text,
                "start": 0.0,
                "end": round(float(duration), 2),
            }]

        return []

    # ============================================================
    # NEW: transcribe_detailed — full structured result
    # ============================================================

    def transcribe_detailed(
        self,
        audio: np.ndarray,
        sr: int = 16000,
    ) -> Dict[str, Any]:
        """
        Full structured transcription with segments and average confidence.

        Returns:
            {
                'text': str,
                'duration': float,
                'segments': [{'text', 'start', 'end', 'confidence'}],
                'avg_confidence': float (0.0-1.0),
                'engine': 'faster-whisper' | 'whisper' | 'none',
                'success': bool,
                'empty': bool,
            }
        """
        result = {
            'text': '',
            'duration': 0.0,
            'segments': [],
            'avg_confidence': 0.0,
            'engine': 'none',
            'success': False,
            'empty': True,
        }

        audio_16k = self._prepare_audio(audio, sr)
        if audio_16k is None:
            return result

        # Duration after normalization
        duration = len(audio_16k) / float(TARGET_SAMPLE_RATE)
        result['duration'] = round(duration, 2)

        # Try Faster-Whisper first
        if self.prefer_faster and FASTER_WHISPER_AVAILABLE:
            try:
                model = _get_faster_whisper_model(self.model_size)
                if model:
                    segments, info = model.transcribe(
                        audio_16k,
                        language="en",
                        task="transcribe",
                        beam_size=5,
                        vad_filter=False,
                        word_timestamps=True,
                    )
                    seg_list = []
                    confidences = []
                    for seg in segments:
                        if not seg.text:
                            continue
                        seg_conf = 0.0
                        if hasattr(seg, "words") and seg.words:
                            probs = [
                                float(getattr(w, "probability", 0.0))
                                for w in seg.words
                                if getattr(w, "probability", None) is not None
                            ]
                            if probs:
                                seg_conf = sum(probs) / len(probs)
                                confidences.append(seg_conf)
                        seg_list.append({
                            'text': seg.text.strip(),
                            'start': round(float(seg.start), 2),
                            'end': round(float(seg.end), 2),
                            'confidence': round(seg_conf, 3),
                        })
                    text = " ".join(s['text'] for s in seg_list if s['text'])
                    if text:
                        result.update({
                            'text': text,
                            'segments': seg_list,
                            'avg_confidence': round(
                                sum(confidences) / len(confidences), 3
                            ) if confidences else 0.0,
                            'engine': 'faster-whisper',
                            'success': True,
                            'empty': False,
                        })
                        return result
            except Exception as e:
                logger.warning(f"Faster-Whisper detailed failed: {e}")

        # Fallback: standard Whisper
        if WHISPER_AVAILABLE:
            try:
                model = _get_whisper_model(self.model_size)
                if model:
                    raw = model.transcribe(
                        audio_16k,
                        language="en",
                        task="transcribe",
                        temperature=0,
                        fp16=False,
                    )
                    text = raw.get("text", "").strip()
                    if text:
                        # Standard Whisper gives segments with 'no_speech_prob' (lower = more confident)
                        raw_segments = raw.get("segments", []) or []
                        seg_list = []
                        confidences = []
                        for seg in raw_segments:
                            no_speech = float(seg.get("no_speech_prob", 0.0))
                            conf = max(0.0, 1.0 - no_speech)
                            confidences.append(conf)
                            seg_list.append({
                                'text': seg.get('text', '').strip(),
                                'start': round(float(seg.get('start', 0.0)), 2),
                                'end': round(float(seg.get('end', 0.0)), 2),
                                'confidence': round(conf, 3),
                            })
                        result.update({
                            'text': text,
                            'segments': seg_list,
                            'avg_confidence': round(
                                sum(confidences) / len(confidences), 3
                            ) if confidences else 0.0,
                            'engine': 'whisper',
                            'success': True,
                            'empty': False,
                        })
                        return result
            except Exception as e:
                logger.error(f"Whisper detailed failed: {e}")

        logger.warning("No detailed transcription available")
        return result


# ============================================================
# FACTORY FUNCTION
# ============================================================

def create_transcriber(
    model_size: str = "base",
    silence_threshold: float = 0.01,
    prefer_faster: bool = True,
) -> WhisperTranscriber:
    """
    Factory function to create a WhisperTranscriber.

    Args:
        model_size: Whisper model size
        silence_threshold: Initial silence threshold (RMS)
        prefer_faster: Prefer Faster-Whisper if available

    Returns:
        WhisperTranscriber instance
    """
    return WhisperTranscriber(
        model_size=model_size,
        silence_threshold=silence_threshold,
        prefer_faster=prefer_faster,
    )


__all__ = [
    'WhisperTranscriber',
    'create_transcriber',
    'MIN_AUDIO_DURATION_SECONDS',
    'TARGET_SAMPLE_RATE',
]