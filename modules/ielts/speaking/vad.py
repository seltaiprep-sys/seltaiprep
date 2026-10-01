"""Voice Activity Detection for IELTS Speaking module - Graceful degradation with Silero support

FIX:
  (1) calibrate() now returns a valid top_db for librosa.trim (was in the wrong scale)
  (2) Silero VAD requires 16 kHz — auto-resamples or falls back to energy mode
  (3) Silero input is normalized to float32 in [-1, 1] and validated
  (4) get_silence_ratio() no longer recomputes Silero inference 3x per call
  (5) On failure, remove_silence() returns an empty array (was returning original audio)
  (6) Silero speech threshold is now configurable
  (7) Guards against list (non-ndarray) input
  (8) min_speech_duration enforced in the energy path
  (9) Optional lazy loading of the Silero model
  (10) Added has_speech() helper
"""

import logging
from typing import Optional, Tuple, Dict, Any

import numpy as np

logger = logging.getLogger(__name__)

# ---- Silero VAD (optional) ----
try:
    from silero_vad import load_silero_vad, get_speech_timestamps
    SILERO_AVAILABLE = True
except ImportError:
    SILERO_AVAILABLE = False
    logger.info("Silero VAD not available, using energy-based detection")

# ---- Librosa for energy-based fallback ----
try:
    import librosa
    LIBROSA_AVAILABLE = True
except ImportError:
    LIBROSA_AVAILABLE = False
    logger.warning("librosa not available - energy-based VAD disabled")


# ============================================================
# Constants
# ============================================================

SILERO_REQUIRED_SR = 16000
DEFAULT_ENERGY_TOP_DB = 25 # dB below peak — for librosa.trim
DEFAULT_SILERO_THRESHOLD = 0.5
MIN_AUDIO_SAMPLES = 100


class ProductionVAD:
    """
    Voice Activity Detection with Silero VAD (optional) and energy-based fallback.

    Features:
    - Silero VAD (high quality) if installed
    - Energy-based fallback using librosa.trim
    - Configurable thresholds
    - Calibration from noise floor
    - Automatic sample-rate handling for Silero
    - Returns empty array (not original) on unrecoverable failure
    """

    def __init__(
        self,
        use_silero: bool = True,
        energy_threshold_db: int = DEFAULT_ENERGY_TOP_DB,
        silero_threshold: float = DEFAULT_SILERO_THRESHOLD,
        min_speech_duration: float = 0.3,
        min_silence_duration: float = 0.3,
        lazy_load: bool = False,
    ):
        """
        Args:
            use_silero: Attempt to use Silero VAD (if available)
            energy_threshold_db: dB below peak for librosa.trim (energy VAD)
            silero_threshold: Speech probability threshold for Silero (0.0-1.0)
            min_speech_duration: Minimum speech segment duration (seconds)
            min_silence_duration: Minimum silence duration to split (seconds)
            lazy_load: If True, load the Silero model on first use (faster startup)
        """
        # ---- Normalize / validate args ----
        try:
            self.energy_threshold_db = int(max(1, min(80, energy_threshold_db)))
        except (TypeError, ValueError):
            self.energy_threshold_db = DEFAULT_ENERGY_TOP_DB

        try:
            self.silero_threshold = float(max(0.1, min(0.9, silero_threshold)))
        except (TypeError, ValueError):
            self.silero_threshold = DEFAULT_SILERO_THRESHOLD

        try:
            self.min_speech_duration = max(0.05, float(min_speech_duration))
        except (TypeError, ValueError):
            self.min_speech_duration = 0.3

        try:
            self.min_silence_duration = max(0.05, float(min_silence_duration))
        except (TypeError, ValueError):
            self.min_silence_duration = 0.3

        self._calibrated_threshold: Optional[float] = None

        # ---- Silero decision ----
        self.use_silero = bool(use_silero) and SILERO_AVAILABLE
        self._silero_model = None
        self._lazy_load = lazy_load

        if self.use_silero and not lazy_load:
            self._load_silero()

        if self.use_silero:
            logger.info("Silero VAD enabled")
        else:
            if not SILERO_AVAILABLE:
                logger.info("Silero VAD not available — energy-based VAD active")
            else:
                logger.info("Silero disabled by caller — energy-based VAD active")

    # ============================================================
    # SILERO MODEL LOADING
    # ============================================================

    def _load_silero(self) -> bool:
        """Load Silero model if not already loaded. Returns success flag."""
        if not self.use_silero:
            return False
        if self._silero_model is not None:
            return True
        try:
            self._silero_model = load_silero_vad(onnx=False)
            logger.info("Silero VAD model loaded")
            return True
        except Exception as e:
            logger.warning(f"Silero VAD failed to load: {e}, using energy-based")
            self.use_silero = False
            self._silero_model = None
            return False

    # ============================================================
    # CALIBRATION
    # ============================================================

    def calibrate(self, audio: np.ndarray, sr: int) -> float:
        """
        Calibrate the energy threshold from a silent audio segment.

         FIX: Now computes a *meaningful* top_db for librosa.trim.
                (The old version mixed dBFS and dB-below-peak scales.)

        Returns:
            The calibrated `top_db` value (use with librosa.trim).
        """
        # If Silero is active, calibration isn't needed.
        if self.use_silero:
            return float(self.energy_threshold_db)

        if audio is None or len(audio) < MIN_AUDIO_SAMPLES:
            return float(self.energy_threshold_db)

        if not LIBROSA_AVAILABLE:
            return float(self.energy_threshold_db)

        try:
            arr = np.asarray(audio, dtype=np.float32)
            if arr.ndim > 1:
                arr = arr[:, 0]

            rms = float(np.sqrt(np.mean(arr ** 2)))
            if rms <= 1e-6:
                # Silent clip — stay with the default
                self._calibrated_threshold = float(self.energy_threshold_db)
                return self._calibrated_threshold

            peak = float(np.max(np.abs(arr)))
            if peak <= 1e-9:
                self._calibrated_threshold = float(self.energy_threshold_db)
                return self._calibrated_threshold

            # dBFS of the noise floor
            noise_db = 20.0 * np.log10(max(rms, 1e-9))
            peak_db = 20.0 * np.log10(max(peak, 1e-9))

            # top_db = how far below peak the speech threshold is
            # We want the noise floor to be *above* the threshold, so:
            # top_db >= peak_db - noise_db + 3 (3 dB safety margin)
            top_db = (peak_db - noise_db) + 3.0

            # Clamp to a sane range for librosa.trim
            top_db = float(max(15.0, min(60.0, top_db)))

            self._calibrated_threshold = top_db
            logger.info(
                f"Calibrated VAD top_db={top_db:.1f} "
                f"(noise RMS={rms:.4f}, noise={noise_db:.1f} dBFS, "
                f"peak={peak_db:.1f} dBFS)"
            )
            return top_db
        except Exception as e:
            logger.warning(f"Calibration failed: {e}")
            self._calibrated_threshold = float(self.energy_threshold_db)
            return self._calibrated_threshold

    # ============================================================
    # SILERO HELPERS
    # ============================================================

    @staticmethod
    def _normalize_audio(audio: np.ndarray, sr: int) -> Optional[np.ndarray]:
        """
        Convert input to mono float32 in [-1, 1]. Returns None on failure.
        """
        try:
            arr = np.asarray(audio)
        except Exception as e:
            logger.warning(f"Cannot convert audio: {e}")
            return None

        if arr.size == 0:
            return None

        # ---- To float32 ----
        if arr.dtype == np.int16:
            arr = arr.astype(np.float32) / 32768.0
        elif arr.dtype == np.int32:
            arr = arr.astype(np.float32) / 2147483648.0
        else:
            arr = arr.astype(np.float32)

        # ---- To mono ----
        if arr.ndim > 1:
            arr = arr[:, 0] if arr.shape[1] == 1 else arr.mean(axis=1)

        # ---- Clamp to [-1, 1] ----
        peak = float(np.max(np.abs(arr))) if arr.size else 0.0
        if peak > 1.0:
            arr = arr / peak

        return arr

    def _silero_prepare(self, audio: np.ndarray, sr: int) -> Optional[np.ndarray]:
        """
        Prepare audio for Silero (16 kHz, float32, mono).

         FIX: Auto-resamples to 16 kHz if needed.
        """
        if not self.use_silero:
            return None
        if not self._load_silero():
            return None

        arr = self._normalize_audio(audio, sr)
        if arr is None:
            return None

        # Resample to 16 kHz if needed
        if sr != SILERO_REQUIRED_SR:
            if not LIBROSA_AVAILABLE:
                logger.warning(
                    f"Silero requires {SILERO_REQUIRED_SR} Hz but got {sr} Hz, "
                    f"and librosa is unavailable — skipping Silero"
                )
                return None
            try:
                arr = librosa.resample(
                    arr, orig_sr=sr, target_sr=SILERO_REQUIRED_SR
                )
            except Exception as e:
                logger.warning(f"Resample for Silero failed: {e}")
                return None

        return arr.astype(np.float32)

    # ============================================================
    # SILERO / ENERGY DETECTION
    # ============================================================

    def _silero_segments(self, audio: np.ndarray, sr: int):
        """
        Run Silero VAD. Returns list of (start, end) sample indices, or None on failure.
        """
        arr = self._silero_prepare(audio, sr)
        if arr is None:
            return None

        try:
            import torch
            tensor = torch.from_numpy(arr).float()

            timestamps = get_speech_timestamps(
                tensor,
                self._silero_model,
                sampling_rate=SILERO_REQUIRED_SR,
                threshold=self.silero_threshold,
                min_speech_duration_ms=int(self.min_speech_duration * 1000),
                min_silence_duration_ms=int(self.min_silence_duration * 1000),
            )

            if not timestamps:
                return []

            # Scale back to original sample rate if resampled
            scale = (len(audio) / len(arr)) if len(arr) else 1.0
            segments = []
            for t in timestamps:
                s = int(t['start'] * scale)
                e = int(t['end'] * scale)
                # Clamp to valid range
                s = max(0, min(s, len(audio)))
                e = max(s, min(e, len(audio)))
                if e > s:
                    segments.append((s, e))
            return segments
        except Exception as e:
            logger.warning(f"Silero VAD failed: {e}")
            return None

    def _energy_segments(self, audio: np.ndarray, sr: int):
        """
        Energy-based VAD via librosa.trim.
        Returns list of (start, end) sample indices, or None on failure.
        """
        if not LIBROSA_AVAILABLE:
            return None
        try:
            arr = self._normalize_audio(audio, sr)
            if arr is None:
                return []

            top_db = (
                self._calibrated_threshold
                if self._calibrated_threshold is not None
                else float(self.energy_threshold_db)
            )
            _, (start, end) = librosa.effects.trim(arr, top_db=top_db)
            if end <= start:
                return []
            return [(int(start), int(end))]
        except Exception as e:
            logger.warning(f"Energy VAD failed: {e}")
            return None

    # ============================================================
    # PUBLIC API — SEGMENTS
    # ============================================================

    def get_speech_segments(
        self, audio: np.ndarray, sr: int
    ) -> Tuple[list, str]:
        """
        Return (segments, engine) where segments is a list of (start, end)
        sample index tuples and engine is 'silero' | 'energy' | 'none'.

         FIX: Callers can now see which engine was used.
        """
        if audio is None or len(audio) < MIN_AUDIO_SAMPLES:
            return [], 'none'

        if self.use_silero:
            segments = self._silero_segments(audio, sr)
            if segments is not None:
                return segments, 'silero'

        segments = self._energy_segments(audio, sr)
        if segments is not None:
            return segments, 'energy'

        return [], 'none'

    # ============================================================
    # PUBLIC API — REMOVE SILENCE
    # ============================================================

    def remove_silence(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """
        Remove silence from audio and return speech-only samples concatenated.

         FIX: Returns an EMPTY array on failure (was returning original audio,
                which made callers think silence was removed when it wasn't).
        """
        # ---- Validate input ----
        try:
            arr = np.asarray(audio)
        except Exception:
            return np.array([], dtype=np.float32)

        if arr.size == 0:
            return np.array([], dtype=arr.dtype)

        if sr is None or sr <= 0:
            logger.warning(f"Invalid sample rate: {sr}")
            return np.array([], dtype=arr.dtype)

        segments, engine = self.get_speech_segments(arr, sr)
        if not segments:
            logger.debug(f"VAD ({engine}): no speech detected")
            return np.array([], dtype=arr.dtype)

        # Concatenate
        try:
            pieces = [arr[s:e] for s, e in segments]
            result = np.concatenate(pieces) if pieces else np.array([], dtype=arr.dtype)

            # Enforce min_speech_duration
            min_samples = int(self.min_speech_duration * sr)
            if len(result) < min_samples:
                logger.debug(
                    f"VAD: speech shorter than "
                    f"{self.min_speech_duration:.2f}s — discarded"
                )
                return np.array([], dtype=arr.dtype)

            logger.debug(
                f"VAD ({engine}): {len(segments)} segment(s), "
                f"{len(result)/sr:.2f}s speech"
            )
            return result
        except Exception as e:
            logger.warning(f"Concatenating speech segments failed: {e}")
            return np.array([], dtype=arr.dtype)

    # ============================================================
    # PUBLIC API — DURATION / RATIO / HELPERS
    # ============================================================

    def get_speech_duration(self, audio: np.ndarray, sr: int) -> float:
        """Return total speech duration in seconds."""
        if audio is None or sr is None or sr <= 0:
            return 0.0
        try:
            total_samples = len(audio)
        except TypeError:
            return 0.0
        if total_samples == 0:
            return 0.0

        speech = self.remove_silence(audio, sr)
        return (len(speech) / sr) if len(speech) > 0 else 0.0

    def get_silence_ratio(self, audio: np.ndarray, sr: int) -> float:
        """
        Return silence ratio in [0, 1].

         FIX: Single VAD pass (was calling remove_silence twice via
                get_speech_duration).
        """
        if audio is None or sr is None or sr <= 0:
            return 1.0

        try:
            total_samples = len(audio)
        except TypeError:
            return 1.0

        if total_samples == 0:
            return 1.0

        # ---- Single-pass VAD ----
        try:
            arr = np.asarray(audio)
        except Exception:
            return 1.0

        segments, _ = self.get_speech_segments(arr, sr)
        if not segments:
            return 1.0

        speech_samples = sum(max(0, e - s) for s, e in segments)
        total_duration = total_samples / float(sr)
        speech_duration = speech_samples / float(sr)

        if total_duration <= 0:
            return 1.0
        ratio = 1.0 - (speech_duration / total_duration)
        return float(max(0.0, min(1.0, ratio)))

    def is_speaking(
        self, audio: np.ndarray, sr: int, threshold: float = 0.3
    ) -> bool:
        """
        Return True if the audio contains meaningful speech.

        Args:
            threshold: maximum silence ratio to still be considered speech.
        """
        try:
            threshold = float(max(0.0, min(1.0, threshold)))
        except (TypeError, ValueError):
            threshold = 0.3
        return self.get_silence_ratio(audio, sr) < threshold

    def has_speech(self, audio: np.ndarray, sr: int) -> bool:
        """
         NEW: Simple boolean helper. Returns True if any speech was detected.
        """
        if audio is None or sr is None or sr <= 0:
            return False
        segments, _ = self.get_speech_segments(audio, sr)
        return bool(segments)

    def get_stats(self, audio: np.ndarray, sr: int) -> Dict[str, Any]:
        """
         NEW: Return a full diagnostic dict for a clip.
        Useful for logging and frontend debugging.
        """
        out: Dict[str, Any] = {
            'duration': 0.0,
            'speech_duration': 0.0,
            'silence_ratio': 1.0,
            'speech_segments': 0,
            'engine': 'none',
            'has_speech': False,
        }
        if audio is None or sr is None or sr <= 0:
            return out
        try:
            total_samples = len(audio)
        except TypeError:
            return out

        if total_samples == 0:
            return out

        duration = total_samples / float(sr)
        segments, engine = self.get_speech_segments(audio, sr)
        speech_samples = sum(max(0, e - s) for s, e in segments)
        speech_duration = speech_samples / float(sr)
        silence_ratio = 1.0 - (speech_duration / duration) if duration > 0 else 1.0

        out.update({
            'duration': round(duration, 3),
            'speech_duration': round(speech_duration, 3),
            'silence_ratio': round(max(0.0, min(1.0, silence_ratio)), 3),
            'speech_segments': len(segments),
            'engine': engine,
            'has_speech': bool(segments),
        })
        return out


# ============================================================
# FACTORY
# ============================================================

def create_vad(
    use_silero: bool = True,
    energy_threshold_db: int = DEFAULT_ENERGY_TOP_DB,
    silero_threshold: float = DEFAULT_SILERO_THRESHOLD,
    min_speech_duration: float = 0.3,
    min_silence_duration: float = 0.3,
    lazy_load: bool = False,
) -> ProductionVAD:
    """Factory function to create a ProductionVAD instance."""
    return ProductionVAD(
        use_silero=use_silero,
        energy_threshold_db=energy_threshold_db,
        silero_threshold=silero_threshold,
        min_speech_duration=min_speech_duration,
        min_silence_duration=min_silence_duration,
        lazy_load=lazy_load,
    )


__all__ = [
    'ProductionVAD',
    'create_vad',
    'SILERO_AVAILABLE',
    'LIBROSA_AVAILABLE',
]