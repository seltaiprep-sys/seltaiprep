"""Pronunciation scoring for IELTS Speaking - Graceful degradation with Wav2Vec2 backend

FIX: 
  (1) Empty/short audio -> 0.0 (was 5.0/4.0)
  (2) All sub-scores default to 0.0 (was 5.0)
  (3) Final floor 0.0 (was 2.0)
  (4) Real formant-based vowel analysis (was a stub)
  (5) Minimum word count guard
  (6) Silence-ratio guard
"""

from dataclasses import dataclass, field
import os
import logging
import numpy as np
import librosa
from scipy import signal
from typing import Optional

logger = logging.getLogger(__name__)

# ---- Optional heavy imports ----
try:
    import torch
    import torch.nn.functional as F
    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
    WAV2VEC2_AVAILABLE = True
except ImportError:
    WAV2VEC2_AVAILABLE = False
    logger.warning("PyTorch or transformers not available. Wav2Vec2 features disabled.")

# ---- Global Wav2Vec2 model cache (lazy loading with lock) ----
_wav2vec_model = None
_wav2vec_processor = None
_wav2vec_device = None


def _get_wav2vec_model():
    """Globally cache the Wav2Vec2 model to avoid reloading."""
    global _wav2vec_model, _wav2vec_processor, _wav2vec_device
    if _wav2vec_model is not None:
        return _wav2vec_model, _wav2vec_processor, _wav2vec_device
    if not WAV2VEC2_AVAILABLE:
        return None, None, None
    try:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        processor = Wav2Vec2Processor.from_pretrained("facebook/wav2vec2-base-960h")
        model = Wav2Vec2ForCTC.from_pretrained("facebook/wav2vec2-base-960h").to(device)
        _wav2vec_model = model
        _wav2vec_processor = processor
        _wav2vec_device = device
        logger.info("Wav2Vec2 model loaded globally")
        return model, processor, device
    except Exception as e:
        logger.error(f"Wav2Vec2 loading failed: {e}")
        return None, None, None


# ============================================================
# Constants
# ============================================================
MIN_WORDS_FOR_PRON_SCORE = 10
MIN_AUDIO_DURATION_SECONDS = 1.0
MAX_SILENCE_RATIO = 0.7 # more than 70% silence -> no speech to score


@dataclass
class PronunciationResult:
    """Pronunciation analysis result"""
    # FIX: all defaults 0.0 (was 5.0)
    score: float = 0.0
    phoneme_score: float = 0.0
    vowel_score: float = 0.0
    stress_score: float = 0.0
    rhythm_score: float = 0.0
    intonation_score: float = 0.0
    issues: list = field(default_factory=list)
    details: dict = field(default_factory=dict)


class PronunciationScorer:
    """
    Pronunciation scoring using acoustic analysis.

    Features:
    - Wav2Vec2 for phoneme confidence (if available)
    - Formant-based vowel analysis (LPC)
    - RMS-peak stress detection
    - Onset-based rhythm analysis
    - Pitch-based intonation analysis
    - Graceful degradation if models missing (returns 0, never 5.0)
    """

    def __init__(self, use_wav2vec: bool = True):
        self.use_wav2vec = use_wav2vec and WAV2VEC2_AVAILABLE
        if self.use_wav2vec:
            # Preload model (blocking, but cached globally)
            _get_wav2vec_model()
        logger.info(f"PronunciationScorer initialized (wav2vec: {self.use_wav2vec})")

    # ============================================================
    # EMPTY RESULT HELPER
    # ============================================================

    def _empty_result(self, reason: str, details: dict = None) -> PronunciationResult:
        """Return a fully-shaped 0.0 result."""
        d = {'reason': reason, 'empty': True}
        if details:
            d.update(details)
        return PronunciationResult(
            score=0.0,
            phoneme_score=0.0,
            vowel_score=0.0,
            stress_score=0.0,
            rhythm_score=0.0,
            intonation_score=0.0,
            issues=[reason],
            details=d,
        )

    # ============================================================
    # MAIN SCORE
    # ============================================================

    def score(self, audio: np.ndarray, ref: str, sr: int) -> PronunciationResult:
        """
        Score pronunciation from audio.

         FIX: Empty/short/silent audio -> 0.0
         FIX: Exception -> 0.0
         FIX: Requires at least MIN_WORDS_FOR_PRON_SCORE words in ref
        """
        # ---------- Normalize input ----------
        if audio is None:
            return self._empty_result("No audio provided")

        try:
            audio = np.asarray(audio, dtype=np.float32)
        except Exception:
            return self._empty_result("Invalid audio format")

        if len(audio) == 0:
            return self._empty_result("Empty audio array")

        if sr is None or sr <= 0:
            return self._empty_result("Invalid sample rate")

        ref = (ref or "").strip()
        word_count = len(ref.split()) if ref else 0

        # ---------- Minimum word guard ----------
        if word_count < MIN_WORDS_FOR_PRON_SCORE:
            return self._empty_result(
                f"BAND 0 — Response too short ({word_count} words). "
                f"At least {MIN_WORDS_FOR_PRON_SCORE} words required for pronunciation scoring.",
                details={'word_count': word_count},
            )

        # ---------- Minimum duration guard ----------
        duration = len(audio) / float(sr)
        if duration < MIN_AUDIO_DURATION_SECONDS:
            return self._empty_result(
                f"BAND 0 — Audio too short ({duration:.2f}s). "
                f"At least {MIN_AUDIO_DURATION_SECONDS:.1f}s required.",
                details={'duration': round(duration, 2)},
            )

        try:
            # ---------- Silence trim ----------
            audio_clean, _ = librosa.effects.trim(audio, top_db=25)
            if len(audio_clean) == 0:
                # Entire clip was silent
                return self._empty_result(
                    "No speech detected — audio is effectively silent.",
                    details={'duration': round(duration, 2)},
                )

            # ---------- Silence-ratio guard ----------
            speech_dur = len(audio_clean) / float(sr)
            silence_ratio = 1.0 - (speech_dur / duration) if duration > 0 else 1.0
            if silence_ratio > MAX_SILENCE_RATIO:
                return self._empty_result(
                    f"BAND 0 — Too much silence ({int(silence_ratio*100)}% of audio).",
                    details={
                        'duration': round(duration, 2),
                        'speech_duration': round(speech_dur, 2),
                        'silence_ratio': round(silence_ratio, 2),
                    },
                )

            # ---------- Compute sub-scores ----------
            phoneme_score = self._score_phonemes(audio_clean, sr)
            vowel_score = self._score_vowels(audio_clean, sr)
            stress_score = self._score_stress(audio_clean, sr)
            rhythm_score = self._score_rhythm(audio_clean, sr)
            intonation_score = self._score_intonation(audio_clean, sr)

            # ---------- Weighted overall ----------
            overall = (
                phoneme_score * 0.30 +
                vowel_score * 0.20 +
                stress_score * 0.20 +
                rhythm_score * 0.15 +
                intonation_score * 0.15
            )

            # FIX: floor 0.0 (was 2.0)
            overall = round(min(9.0, max(0.0, overall)), 1)

            # FIX: If all sub-scores are zero (analyzers failed), return 0
            if overall == 0.0 and all(
                s == 0.0 for s in (
                    phoneme_score, vowel_score, stress_score,
                    rhythm_score, intonation_score
                )
            ):
                return self._empty_result(
                    "Pronunciation analysis returned no valid scores.",
                    details={'duration': round(duration, 2)},
                )

            return PronunciationResult(
                score=overall,
                phoneme_score=round(phoneme_score, 1),
                vowel_score=round(vowel_score, 1),
                stress_score=round(stress_score, 1),
                rhythm_score=round(rhythm_score, 1),
                intonation_score=round(intonation_score, 1),
                details={
                    'duration': round(duration, 2),
                    'speech_duration': round(speech_dur, 2),
                    'word_count': word_count,
                }
            )

        except Exception as e:
            logger.error(f"Pronunciation scoring error: {e}", exc_info=True)
            # FIX: exception -> 0.0 (was 5.0)
            return self._empty_result(
                f"Pronunciation analysis failed: {e}",
                details={'duration': round(duration, 2)},
            )

    # ============================================================
    # PHONEME SCORE (Wav2Vec2)
    # ============================================================

    def _score_phonemes(self, audio: np.ndarray, sr: int) -> float:
        """
        Phoneme confidence via Wav2Vec2.

         FIX: Returns 0.0 on failure (was 5.0).
        """
        if not self.use_wav2vec:
            return 0.0

        model, processor, device = _get_wav2vec_model()
        if model is None:
            return 0.0

        try:
            # Ensure 16 kHz (Wav2Vec2's native rate)
            if sr != 16000:
                audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)

            inputs = processor(audio, sampling_rate=16000, return_tensors="pt", padding=True)
            inputs = {k: v.to(device) for k, v in inputs.items()}

            with torch.no_grad():
                logits = model(**inputs).logits

            probs = F.softmax(logits, dim=-1)
            max_probs = probs.max(dim=-1).values[0].cpu().numpy()

            if len(max_probs) == 0:
                return 0.0

            mean_conf = float(np.mean(max_probs))
            # Fraction of frames the model is confident about (>= 0.6)
            high_conf_ratio = float(np.mean(max_probs >= 0.6))
            # Fraction of frames the model is very uncertain about (< 0.3)
            low_conf_ratio = float(np.mean(max_probs < 0.3))

            # Calibrated scoring:
            # - mean_conf near 1.0 -> high score
            # - high_conf_ratio bonus
            # - low_conf_ratio penalty
            base = mean_conf * 6.0 # 0.0 - 6.0
            high_bonus = high_conf_ratio * 2.0 # 0.0 - 2.0
            low_penalty = low_conf_ratio * 3.0 # 0.0 - 3.0

            score = base + high_bonus - low_penalty

            return float(max(0.0, min(9.0, score)))

        except Exception as e:
            logger.warning(f"Phoneme scoring failed: {e}")
            return 0.0

    # ============================================================
    # VOWEL SCORE (formant-based)
    # ============================================================

    def _score_vowels(self, audio: np.ndarray, sr: int) -> float:
        """
        Vowel quality via formant analysis.

         FIX: Real implementation (was a stub returning 5.0).

        Approach:
        - Estimate the first two formants (F1, F2) via LPC
        - Compute F1/F2 stability across voiced frames
        - Score: stable, well-separated formants = higher score
        """
        try:
            # Need at least 0.3s to compute meaningful formants
            if len(audio) / sr < 0.3:
                return 0.0

            # Pre-emphasis and framing
            frame_length = int(0.025 * sr) # 25 ms
            hop_length = int(0.010 * sr) # 10 ms
            if frame_length < 32 or hop_length < 16:
                return 0.0

            # Use LPC per frame to estimate formants
            lpc_order = 2 + int(sr / 1000) # rule of thumb

            frames = librosa.util.frame(
                audio,
                frame_length=frame_length,
                hop_length=hop_length
            ).T

            if frames.shape[0] < 5:
                return 0.0

            # Apply Hamming window to each frame
            window = np.hamming(frame_length)
            frames = frames * window

            # Compute energy to filter out silent frames
            energies = np.sum(frames ** 2, axis=1)
            if energies.max() <= 0:
                return 0.0

            # Keep voiced frames (top 50% energy)
            threshold = np.percentile(energies, 50)
            voiced_frames = frames[energies >= threshold]

            if len(voiced_frames) < 3:
                return 0.0

            # Compute formants per voiced frame
            formant_pairs = []
            for frame in voiced_frames:
                try:
                    # LPC coefficients
                    lpc_coeffs = librosa.lpc(frame, order=lpc_order)
                    # Roots of LPC polynomial -> formants
                    roots = np.roots(lpc_coeffs)
                    roots = [r for r in roots if np.imag(r) >= 0]
                    if not roots:
                        continue

                    angles = np.arctan2(np.imag(roots), np.real(roots))
                    freqs = angles * (sr / (2 * np.pi))
                    freqs = sorted([f for f in freqs if 90 < f < 4000])

                    if len(freqs) >= 2:
                        formant_pairs.append((freqs[0], freqs[1]))
                except Exception:
                    continue

            if len(formant_pairs) < 3:
                return 0.0

            formant_pairs = np.array(formant_pairs)
            f1 = formant_pairs[:, 0]
            f2 = formant_pairs[:, 1]

            # ---- Score from F1/F2 stability ----
            # Coefficient of variation (lower = more stable = better)
            f1_cv = np.std(f1) / (np.mean(f1) + 1e-8)
            f2_cv = np.std(f2) / (np.mean(f2) + 1e-8)

            # ---- Score from F1/F2 separation ----
            # Well-separated formants (F2 > F1 by a healthy margin) = clearer vowels
            mean_sep = np.mean(f2 - f1)

            # F1/F2 CV maps: 0.05 -> 8.0, 0.10 -> 6.0, 0.20 -> 3.0, >0.30 -> 0
            def cv_to_score(cv):
                if cv <= 0.05: return 8.0
                if cv >= 0.30: return 0.0
                # Linear interpolation
                return max(0.0, 8.0 - (cv - 0.05) / 0.25 * 8.0)

            s1 = cv_to_score(f1_cv)
            s2 = cv_to_score(f2_cv)

            # Separation bonus: >= 500 Hz = good, < 200 Hz = poor
            if mean_sep >= 500:
                sep_bonus = 1.0
            elif mean_sep >= 300:
                sep_bonus = 0.5
            elif mean_sep >= 200:
                sep_bonus = 0.0
            else:
                sep_bonus = -1.0

            score = (s1 + s2) / 2.0 + sep_bonus
            return float(max(0.0, min(9.0, score)))

        except Exception as e:
            logger.warning(f"Vowel scoring failed: {e}")
            return 0.0

    # ============================================================
    # STRESS SCORE
    # ============================================================

    def _score_stress(self, audio: np.ndarray, sr: int) -> float:
        """
        Stress pattern via RMS peaks with relative thresholds.

         FIX: Returns 0.0 on failure (was 5.0).
        """
        try:
            rms = librosa.feature.rms(y=audio, frame_length=2048, hop_length=512)[0]
            if len(rms) < 10:
                return 0.0

            # Normalize RMS relative to its mean
            rms_norm = rms / (np.mean(rms) + 1e-8)

            # Peaks above 1.5x mean
            hop_seconds = 512 / float(sr)
            min_distance_frames = max(1, int(0.15 / hop_seconds))
            peaks, _ = signal.find_peaks(
                rms_norm, height=1.5, distance=min_distance_frames
            )

            if len(peaks) < 2:
                return 0.0

            # Regularity of intervals (lower CV = more even rhythm = higher score)
            intervals = np.diff(peaks) * hop_seconds
            cv = np.std(intervals) / (np.mean(intervals) + 1e-8)

            # Map CV to score: 0.2 -> 8.0, 0.4 -> 6.0, 0.6 -> 4.0, >=0.8 -> 2.0
            if cv < 0.2:
                return 8.0
            elif cv < 0.4:
                return 7.0
            elif cv < 0.6:
                return 5.5
            elif cv < 0.8:
                return 4.0
            else:
                return 2.0

        except Exception as e:
            logger.warning(f"Stress scoring failed: {e}")
            return 0.0

    # ============================================================
    # RHYTHM SCORE
    # ============================================================

    def _score_rhythm(self, audio: np.ndarray, sr: int) -> float:
        """
        Rhythm via onset strength + beat tracking.

         FIX: Returns 0.0 on failure (was 5.0).
        """
        try:
            onset_env = librosa.onset.onset_strength(y=audio, sr=sr, hop_length=512)
            if len(onset_env) < 10:
                return 0.0

            _, beats = librosa.beat.beat_track(
                onset_envelope=onset_env, sr=sr, hop_length=512
            )
            if len(beats) < 3:
                return 0.0

            intervals = np.diff(beats)
            if len(intervals) == 0 or np.mean(intervals) <= 0:
                return 0.0

            cv = np.std(intervals) / (np.mean(intervals) + 1e-8)

            if cv < 0.2:
                return 7.5
            elif cv < 0.4:
                return 6.5
            elif cv < 0.6:
                return 5.5
            else:
                return 4.0

        except Exception as e:
            logger.warning(f"Rhythm scoring failed: {e}")
            return 0.0

    # ============================================================
    # INTONATION SCORE
    # ============================================================

    def _score_intonation(self, audio: np.ndarray, sr: int) -> float:
        """
        Intonation via relative pitch variation.

         FIX: Returns 0.0 on failure (was 5.0).
        """
        try:
            pitches, magnitudes = librosa.piptrack(y=audio, sr=sr, fmin=75, fmax=600)

            pitch_values = []
            for i in range(pitches.shape[1]):
                idx = magnitudes[:, i].argmax()
                p = pitches[idx, i]
                if p > 0:
                    pitch_values.append(p)

            if len(pitch_values) < 10:
                return 0.0

            pitch_values = np.array(pitch_values)
            pitch_median = np.median(pitch_values)
            if pitch_median <= 0:
                return 0.0

            pitch_std_rel = np.std(pitch_values) / (pitch_median + 1e-8)

            # Ideal relative std ~0.15-0.25
            if pitch_std_rel < 0.05:
                return 3.0 # too monotone
            elif pitch_std_rel < 0.10:
                return 5.0
            elif pitch_std_rel < 0.20:
                return 7.0
            elif pitch_std_rel < 0.30:
                return 8.0
            else:
                return 6.5 # too variable / unnatural

        except Exception as e:
            logger.warning(f"Intonation scoring failed: {e}")
            return 0.0


# ---- Factory function ----
def create_pronunciation_scorer(use_wav2vec: bool = True) -> PronunciationScorer:
    """Factory function for PronunciationScorer."""
    return PronunciationScorer(use_wav2vec)