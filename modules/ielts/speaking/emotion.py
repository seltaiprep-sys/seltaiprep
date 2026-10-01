# modules/ielts/speaking/emotion.py
"""Voice emotion detection for IELTS Speaking module

FIX:
  (1) All default values are now 0.0 (was 0.5) — no fake "medium" mood
  (2) Empty/invalid audio -> empty result with `empty=True` flag
  (3) Exception -> empty result with `error` key
  (4) get_speaking_confidence() returns 0.0 on failure (was 0.5)
  (5) is_fluent() only reads real measurements, not defaults
  (6) Added speech_rate_wpm (words per minute)
  (7) Added self_corrections count (from filler words like "I mean", "sorry")
  (8) Added hesitation_markers count (um, uh, er)
  (9) Removed singleton; use create_emotion_detector() factory
"""

import numpy as np
import librosa
import re
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Empty-result helper
# ============================================================

def _empty_emotion_result(reason: str = 'No speech detected') -> 'EmotionResult':
    """Return a fully-zeroed EmotionResult."""
    return EmotionResult(
        emotions={
            'confidence': 0.0,
            'nervousness': 0.0,
            'hesitation': 0.0,
            'enthusiasm': 0.0,
        },
        primary_emotion='neutral',
        primary_emoji='',
        details={'reason': reason, 'empty': True},
    )


@dataclass
class EmotionResult:
    """Emotion detection result"""
    # FIX: all defaults 0.0 (was 0.5 for most)
    emotions: Dict[str, float] = field(default_factory=lambda: {
        'confidence': 0.0,
        'nervousness': 0.0,
        'hesitation': 0.0,
        'enthusiasm': 0.0,
    })
    primary_emotion: str = 'neutral'
    primary_emoji: str = ''
    details: Dict = field(default_factory=dict)


class VoiceEmotionDetector:
    """Detect emotional/fluency signals from voice characteristics.

    All scores are 0.0–1.0. A score of 0.0 means "no signal detected".
    There is no "neutral 0.5" default: an empty result is truly empty.
    """

    # Common English filler/hesitation markers
    HESITATION_MARKERS = ['um', 'uh', 'er', 'ah', 'hmm', 'mmm']
    SELF_CORRECTION_MARKERS = [
        'i mean', 'sorry', 'no wait', 'actually', 'i meant',
        'let me rephrase', 'what i meant', 'rather',
    ]

    # ============================================================
    # MAIN ANALYZE
    # ============================================================

    def analyze(
        self,
        audio: np.ndarray,
        sr: int,
        transcript: Optional[str] = None,
    ) -> EmotionResult:
        """
        Analyze emotions from audio signal.

        Args:
            audio: Audio signal (float32, -1 to 1)
            sr: Sample rate
            transcript: Optional transcript for word-level hesitation detection
                        (if not provided, only acoustic analysis is used)

        Returns:
            EmotionResult with emotion scores (all 0.0 on failure/empty)
        """
        # ---------- Normalize + guard ----------
        if audio is None:
            return _empty_emotion_result('No audio provided')

        try:
            audio = np.asarray(audio, dtype=np.float32)
        except Exception:
            return _empty_emotion_result('Invalid audio format')

        if len(audio) == 0:
            return _empty_emotion_result('Empty audio array')

        if sr is None or sr <= 0:
            return _empty_emotion_result('Invalid sample rate')

        duration = len(audio) / float(sr)
        if duration < 0.3:
            return _empty_emotion_result(f'Audio too short ({duration:.2f}s)')

        try:
            # ---------- Pitch extraction ----------
            pitches, magnitudes = librosa.piptrack(
                y=audio, sr=sr, fmin=75, fmax=600
            )

            pitch_values = []
            for i in range(pitches.shape[1]):
                index = magnitudes[:, i].argmax()
                pitch = pitches[index, i]
                if pitch > 0:
                    pitch_values.append(pitch)

            if not pitch_values:
                return _empty_emotion_result('No voiced frames detected')

            pitch_values = np.array(pitch_values)

            # ---------- Energy ----------
            energy = librosa.feature.rms(
                y=audio, frame_length=2048, hop_length=512
            )[0]

            if len(energy) == 0 or np.mean(energy) <= 1e-6:
                return _empty_emotion_result('Audio is effectively silent')

            # ---------- Silence ratio ----------
            silent_frames = np.sum(energy < np.mean(energy) * 0.2)
            silence_ratio = float(silent_frames / len(energy))

            # If more than 85% silent, treat as no real speech
            if silence_ratio > 0.85:
                return _empty_emotion_result(
                    f'Too much silence ({int(silence_ratio*100)}%)'
                )

            # ---------- Acoustic features ----------
            mean_pitch = float(np.mean(pitch_values))
            std_pitch = float(np.std(pitch_values))

            pitch_variation = (
                float(np.mean(np.abs(np.diff(pitch_values))) / (mean_pitch + 1e-8))
                if len(pitch_values) > 1 else 0.0
            )

            pitch_range = float(
                np.percentile(pitch_values, 90) - np.percentile(pitch_values, 10)
            )

            mean_energy = float(np.mean(energy))

            # ---------- Text-based signals ----------
            hesitation_markers = 0
            self_corrections = 0
            word_count = 0
            speech_rate_wpm = 0.0

            if transcript:
                text_lower = transcript.lower()
                word_count = len(transcript.split())

                # Count hesitation markers with word boundaries
                for marker in self.HESITATION_MARKERS:
                    hesitation_markers += len(
                        re.findall(rf'\b{re.escape(marker)}\b', text_lower)
                    )

                # Count self-correction phrases (multi-word)
                for marker in self.SELF_CORRECTION_MARKERS:
                    self_corrections += text_lower.count(marker)

                # Speech rate
                if duration > 0:
                    speech_rate_wpm = round((word_count / duration) * 60, 1)

            # ---------- Compute emotion scores ----------
            confidence = self._score_confidence(
                std_pitch=std_pitch,
                mean_energy=mean_energy,
                speech_rate_wpm=speech_rate_wpm,
                hesitation_markers=hesitation_markers,
                word_count=word_count,
            )

            nervousness = self._score_nervousness(
                std_pitch=std_pitch,
                pitch_variation=pitch_variation,
                mean_energy=mean_energy,
                silence_ratio=silence_ratio,
                hesitation_markers=hesitation_markers,
                word_count=word_count,
            )

            hesitation = self._score_hesitation(
                silence_ratio=silence_ratio,
                hesitation_markers=hesitation_markers,
                word_count=word_count,
            )

            enthusiasm = self._score_enthusiasm(
                mean_energy=mean_energy,
                pitch_range=pitch_range,
                speech_rate_wpm=speech_rate_wpm,
            )

            # ---------- Primary emotion ----------
            emotions = {
                'confidence': round(confidence, 3),
                'nervousness': round(nervousness, 3),
                'hesitation': round(hesitation, 3),
                'enthusiasm': round(enthusiasm, 3),
            }

            primary = max(emotions, key=emotions.get)

            emoji_map = {
                'confidence': '',
                'nervousness': '',
                'hesitation': '',
                'enthusiasm': '',
            }

            return EmotionResult(
                emotions=emotions,
                primary_emotion=primary,
                primary_emoji=emoji_map.get(primary, ''),
                details={
                    'pitch_variation': round(pitch_variation, 3),
                    'mean_energy': round(mean_energy, 3),
                    'mean_pitch_hz': round(mean_pitch, 1),
                    'std_pitch_hz': round(std_pitch, 1),
                    'pitch_range_hz': round(pitch_range, 1),
                    'speech_duration': round(duration, 2),
                    'silence_ratio': round(silence_ratio, 3),
                    'hesitation_markers': hesitation_markers,
                    'self_corrections': self_corrections,
                    'word_count': word_count,
                    'speech_rate_wpm': speech_rate_wpm,
                },
            )

        except Exception as e:
            logger.error(f"Emotion detection error: {e}", exc_info=True)
            return _empty_emotion_result(f'Analysis failed: {e}')

    # ============================================================
    # SCORING HELPERS
    # ============================================================

    def _score_confidence(
        self,
        std_pitch: float,
        mean_energy: float,
        speech_rate_wpm: float,
        hesitation_markers: int,
        word_count: int,
    ) -> float:
        """
        Confidence = steady pitch + healthy energy + moderate speed +
                     few hesitations.
        Returns 0.0-1.0.
        """
        score = 0.0

        # Steady pitch (<25 Hz std is stable)
        if std_pitch < 25:
            score += 0.25
        elif std_pitch < 40:
            score += 0.15

        # Good energy
        if mean_energy > 0.5:
            score += 0.25
        elif mean_energy > 0.3:
            score += 0.15

        # Speech rate 100-160 wpm is confident/natural
        if speech_rate_wpm == 0:
            score += 0 # no transcript info
        elif 100 <= speech_rate_wpm <= 180:
            score += 0.25
        elif 70 <= speech_rate_wpm < 100:
            score += 0.15
        elif speech_rate_wpm > 180:
            score += 0.10 # rushed
        # < 70 wpm: no bonus

        # Few hesitations
        if word_count > 0:
            hesitation_ratio = hesitation_markers / word_count
            if hesitation_ratio < 0.02:
                score += 0.25
            elif hesitation_ratio < 0.05:
                score += 0.15

        return float(min(1.0, max(0.0, score)))

    def _score_nervousness(
        self,
        std_pitch: float,
        pitch_variation: float,
        mean_energy: float,
        silence_ratio: float,
        hesitation_markers: int,
        word_count: int,
    ) -> float:
        """
        Nervousness = unstable pitch + low energy + long silences +
                      lots of hesitations.
        Returns 0.0-1.0.
        """
        score = 0.0

        if std_pitch > 35:
            score += 0.25
        elif std_pitch > 25:
            score += 0.10

        if pitch_variation > 0.30:
            score += 0.20
        elif pitch_variation > 0.15:
            score += 0.10

        if mean_energy < 0.2:
            score += 0.25
        elif mean_energy < 0.35:
            score += 0.10

        if silence_ratio > 0.4:
            score += 0.20
        elif silence_ratio > 0.25:
            score += 0.10

        if word_count > 0:
            hesitation_ratio = hesitation_markers / word_count
            if hesitation_ratio > 0.06:
                score += 0.20
            elif hesitation_ratio > 0.03:
                score += 0.10

        return float(min(1.0, max(0.0, score)))

    def _score_hesitation(
        self,
        silence_ratio: float,
        hesitation_markers: int,
        word_count: int,
    ) -> float:
        """
        Hesitation = pause density + filler words.
        Returns 0.0-1.0.
        """
        # If we have transcript info, weight fillers more.
        if word_count > 0:
            marker_ratio = hesitation_markers / word_count
            # 0.10 = 10% of words are fillers -> max hesitation
            filler_score = min(1.0, marker_ratio / 0.10)
            # Blend with silence
            return float(min(1.0, 0.7 * filler_score + 0.3 * min(1.0, silence_ratio / 0.5)))

        # No transcript -> use silence only
        return float(min(1.0, max(0.0, silence_ratio / 0.5)))

    def _score_enthusiasm(
        self,
        mean_energy: float,
        pitch_range: float,
        speech_rate_wpm: float,
    ) -> float:
        """
        Enthusiasm = high energy + wide pitch range + lively pace.
        Returns 0.0-1.0.
        """
        score = 0.0

        if mean_energy > 0.6:
            score += 0.4
        elif mean_energy > 0.4:
            score += 0.2

        if pitch_range > 150:
            score += 0.3
        elif pitch_range > 100:
            score += 0.15

        if speech_rate_wpm >= 130:
            score += 0.3
        elif speech_rate_wpm >= 100:
            score += 0.15

        return float(min(1.0, max(0.0, score)))

    # ============================================================
    # QUICK PUBLIC HELPERS
    # ============================================================

    def get_speaking_confidence(
        self,
        audio: np.ndarray,
        sr: int,
        transcript: Optional[str] = None,
    ) -> float:
        """
        Quick confidence score (0.0-1.0).

         FIX: Returns 0.0 on failure/empty (was 0.5).
        """
        result = self.analyze(audio, sr, transcript=transcript)
        if result.details.get('empty'):
            return 0.0
        return float(result.emotions.get('confidence', 0.0))

    def is_fluent(
        self,
        audio: np.ndarray,
        sr: int,
        transcript: Optional[str] = None,
    ) -> bool:
        """
        Check if the speaker sounds fluent.

         FIX: Only returns True when we have a REAL analysis
        (not from 0.5 defaults).
        """
        result = self.analyze(audio, sr, transcript=transcript)
        if result.details.get('empty'):
            return False

        confidence = result.emotions.get('confidence', 0.0)
        hesitation = result.emotions.get('hesitation', 0.0)

        return confidence > 0.5 and hesitation < 0.3


# ============================================================
# Factory function (replaces the removed singleton)
# ============================================================

def create_emotion_detector() -> VoiceEmotionDetector:
    """Factory function to create a VoiceEmotionDetector."""
    return VoiceEmotionDetector()


__all__ = [
    'VoiceEmotionDetector',
    'EmotionResult',
    'create_emotion_detector',
]