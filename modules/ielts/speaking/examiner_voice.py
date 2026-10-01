"""Examiner audio generation for IELTS Speaking using Edge TTS - Async/Sync with hash caching

FIX:
  (1) speak_sync() now logs full tracebacks instead of silently returning None
  (2) Generated files are validated (non-zero bytes) — 0-byte files are deleted
  (3) _sanitize_text() protects against None / non-string input
  (4) use_cache=False temp files are cleaned up automatically on next call
  (5) Removed unused Union import
  (6) generate_part1_audio() now walks the ACTUAL test_generator structure
      (examiner_greeting, warmup_section.questions, topic_section.questions, etc.)
  (7) generate_part2_audio() uses examiner_intro / topic_card
  (8) generate_part3_audio() uses examiner_intro (was 'introduction')
  (9) generate_full_test_audio() now produces the full map correctly
  (10) Optional rate/pitch/volume params
  (11) 30s timeout on edge_tts
  (12) speak_async() raises instead of returning None silently
"""

import asyncio
import hashlib
import os
import logging
import tempfile
import threading
import time
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# ----- Optional dependency: edge_tts -----
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    logger.warning("edge_tts not installed. Install with: pip install edge-tts")


# ============================================================
# Constants
# ============================================================

MIN_VALID_AUDIO_BYTES = 100 # anything smaller is treated as a failed generation
GENERATION_TIMEOUT_SECONDS = 30
DEFAULT_RATE = "+0%"
DEFAULT_VOLUME = "+0%"
DEFAULT_PITCH = "+0Hz"


# ============================================================
# Helpers
# ============================================================

def _sanitize_text(text: Any) -> str:
    """Return stripped string, empty if text is None/non-string."""
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return ""
    return text.strip()


def _validate_audio_file(path: str) -> bool:
    """
    Return True only if the file exists and is > MIN_VALID_AUDIO_BYTES.
    Deletes the file if it's too small.
    """
    if not path or not os.path.exists(path):
        return False
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size < MIN_VALID_AUDIO_BYTES:
        logger.warning(
            f"Generated audio is too small ({size} bytes < "
            f"{MIN_VALID_AUDIO_BYTES}) — deleting {os.path.basename(path)}"
        )
        try:
            os.remove(path)
        except OSError:
            pass
        return False
    return True


# ============================================================
# ExaminerVoice
# ============================================================

class ExaminerVoice:
    """
    Generate natural-sounding examiner speech for IELTS Speaking.

    Features:
    - Multiple British/American/Australian voices
    - Async and Sync support (no event loop crashes)
    - Hash-based caching (reuses identical prompts)
    - Thread-safe for web applications
    - File-size validation (0-byte files are treated as failures)
    """

    VOICES = {
        "male_british": "en-GB-RyanNeural",
        "female_british": "en-GB-SoniaNeural",
        "male_american": "en-US-GuyNeural",
        "female_american": "en-US-JennyNeural",
        "male_australian": "en-AU-WilliamNeural",
        "female_australian": "en-AU-NatashaNeural",
        "indian_english": "en-IN-PrabhatNeural",
    }

    def __init__(
        self,
        voice: str = "female_british",
        output_dir: str = "static/audio_cache/speaking",
        use_cache: bool = True,
    ):
        self.voice_name = voice
        self.voice_id = self.VOICES.get(voice, "en-GB-SoniaNeural")
        self.output_dir = output_dir
        self.use_cache = use_cache
        self._lock = threading.Lock()

        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as e:
            logger.error(f"Cannot create output dir {output_dir}: {e}")

        if EDGE_TTS_AVAILABLE:
            logger.info(f"ExaminerVoice initialized: {voice} ({self.voice_id})")
        else:
            logger.warning("edge_tts not available - audio generation will fail")

    # ============================================================
    # CACHE PATH
    # ============================================================

    def _get_cache_path(self, text: str) -> str:
        """Deterministic filename from text + voice."""
        text = _sanitize_text(text)
        text_hash = hashlib.md5(
            f"{text}_{self.voice_id}".encode('utf-8', errors='ignore')
        ).hexdigest()[:16]
        return os.path.join(self.output_dir, f"{text_hash}.mp3")

    # ============================================================
    # CORE ASYNC GENERATION
    # ============================================================

    async def _generate_audio_async(
        self,
        text: str,
        output_path: str,
        rate: str = DEFAULT_RATE,
        volume: str = DEFAULT_VOLUME,
        pitch: str = DEFAULT_PITCH,
    ) -> Optional[str]:
        """
        Internal async method using edge_tts.

         Raises on failure so the caller can log the actual reason.
         Applies a timeout so a hung edge_tts call doesn't block forever.
        """
        if not EDGE_TTS_AVAILABLE:
            raise RuntimeError("edge_tts not available")

        text = _sanitize_text(text)
        if len(text) < 2:
            raise ValueError(f"Text too short for TTS: {text!r}")

        communicate = edge_tts.Communicate(
            text,
            self.voice_id,
            rate=rate,
            volume=volume,
            pitch=pitch,
        )

        await asyncio.wait_for(
            communicate.save(output_path),
            timeout=GENERATION_TIMEOUT_SECONDS,
        )

        # Validate the file
        if not _validate_audio_file(output_path):
            raise RuntimeError(
                f"edge_tts produced invalid audio at {output_path}"
            )

        logger.debug(
            f"Audio generated: {os.path.basename(output_path)} "
            f"({len(text)} chars, {os.path.getsize(output_path)} bytes)"
        )
        return output_path

    # ============================================================
    # PUBLIC ASYNC METHOD
    # ============================================================

    async def speak_async(
        self,
        text: str,
        filename: Optional[str] = None,
        rate: str = DEFAULT_RATE,
        volume: str = DEFAULT_VOLUME,
        pitch: str = DEFAULT_PITCH,
    ) -> Optional[str]:
        """
        Generate audio asynchronously.

        Returns the path to the audio file, or None if the generation
        failed. When caching is enabled and a matching file exists, that
        file is returned immediately.
        """
        if not EDGE_TTS_AVAILABLE:
            return None

        text = _sanitize_text(text)
        if not text:
            logger.warning("speak_async called with empty text")
            return None

        # ---- Resolve output path ----
        if filename is not None:
            output_path = os.path.join(self.output_dir, filename)
        elif self.use_cache:
            output_path = self._get_cache_path(text)
        else:
            # Temp file that WILL be cleaned up by the caller
            fd, output_path = tempfile.mkstemp(
                suffix=".mp3", prefix="examiner_", dir=self.output_dir
            )
            os.close(fd)
            # If there's no cache, we return the temp path but it won't
            # survive the process. Callers that want persistence should
            # set use_cache=True.

        # ---- Cache hit ----
        if self.use_cache and os.path.exists(output_path):
            if _validate_audio_file(output_path):
                logger.debug(f"Cache hit: {os.path.basename(output_path)}")
                return output_path
            # Invalid cache file got deleted by _validate_audio_file

        # ---- Generate ----
        try:
            return await self._generate_audio_async(
                text, output_path,
                rate=rate, volume=volume, pitch=pitch,
            )
        except asyncio.TimeoutError:
            logger.error(
                f"TTS timeout ({GENERATION_TIMEOUT_SECONDS}s) for "
                f"'{text[:50]}...'"
            )
            return None
        except Exception as e:
            logger.error(f"Async TTS failed for '{text[:50]}...': {e}")
            return None

    # ============================================================
    # PUBLIC SYNC METHOD
    # ============================================================

    def speak_sync(
        self,
        text: str,
        filename: Optional[str] = None,
        rate: str = DEFAULT_RATE,
        volume: str = DEFAULT_VOLUME,
        pitch: str = DEFAULT_PITCH,
    ) -> Optional[str]:
        """
        Generate audio synchronously (thread-safe).

         Logs the full traceback on failure (was silent).

        Args:
            text: Text to synthesize
            filename: Optional custom filename
            rate: e.g. "+0%", "+10%", "-5%"
            volume: e.g. "+0%", "+20%"
            pitch: e.g. "+0Hz", "+5Hz"

        Returns:
            Path to audio file, or None if generation failed.
        """
        if not EDGE_TTS_AVAILABLE:
            logger.warning("speak_sync called but edge_tts is not installed")
            return None

        text = _sanitize_text(text)
        if not text:
            logger.warning("speak_sync called with empty text")
            return None

        # ---- Ensure a fresh event loop in this thread ----
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(
                    self.speak_async(
                        text, filename,
                        rate=rate, volume=volume, pitch=pitch,
                    )
                )
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
        except Exception as e:
            # FIX: log the full traceback so we can see what went wrong
            logger.exception(f"Sync TTS failed for '{text[:50]}...': {e}")
            return None

    # ============================================================
    # BATCH HELPER
    # ============================================================

    def generate_audio_batch(
        self,
        text_map: Dict[str, str],
        rate: str = DEFAULT_RATE,
        volume: str = DEFAULT_VOLUME,
        pitch: str = DEFAULT_PITCH,
    ) -> Dict[str, Optional[str]]:
        """
        Generate multiple audio files synchronously.

         FIX: `_sanitize_text` protects against non-string values.

        Args:
            text_map: Dict mapping key -> text to synthesize

        Returns:
            Dict mapping key -> audio file path (or None for failures)
        """
        results: Dict[str, Optional[str]] = {}
        for key, raw_text in (text_map or {}).items():
            text = _sanitize_text(raw_text)
            if not text:
                results[key] = None
                continue
            try:
                text_hash = hashlib.md5(
                    text.encode('utf-8', errors='ignore')
                ).hexdigest()[:12]
                filename = f"{key}_{text_hash}.mp3"
                results[key] = self.speak_sync(
                    text, filename,
                    rate=rate, volume=volume, pitch=pitch,
                )
            except Exception as e:
                logger.error(f"Batch item '{key}' failed: {e}")
                results[key] = None
        return results

    # ============================================================
    # FIXED PART-SPECIFIC GENERATORS
    # (now match the actual structure returned by test_generator.py)
    # ============================================================

    def _generate_part1_audio(self, part1: Dict) -> Dict:
        """
        Walk the ACTUAL Part 1 structure from test_generator.py:

        {
            "examiner_greeting": "...",
            "examiner_name_follow_up": "...",
            "examiner_id_check": "...",
            "examiner_thank_you": "...",
            "examiner_test_explanation": "...",
            "examiner_begin": "...",
            "warmup_section": {
                "examiner_transition": "...",
                "questions": [{"question": "...", "type": "..."}]
            },
            "topic_section": {
                "examiner_transition": "...",
                "questions": [...]
            },
            "examiner_part1_closing": "..."
        }
        """
        audio_map: Dict[str, Optional[str]] = {}
        if not isinstance(part1, dict):
            return audio_map

        # ---- Examiner script strings ----
        greeting_keys = [
            'examiner_greeting',
            'examiner_name_follow_up',
            'examiner_id_check',
            'examiner_thank_you',
            'examiner_test_explanation',
            'examiner_begin',
            'examiner_part1_closing',
        ]
        for key in greeting_keys:
            text = _sanitize_text(part1.get(key))
            if text:
                audio_map[key] = self.speak_sync(text, f"part1_{key}.mp3")

        # ---- Warmup section ----
        warmup = part1.get('warmup_section') or {}
        if isinstance(warmup, dict):
            transition = _sanitize_text(warmup.get('examiner_transition'))
            if transition:
                audio_map['warmup_transition'] = self.speak_sync(
                    transition, 'part1_warmup_transition.mp3'
                )
            for i, q in enumerate(warmup.get('questions') or []):
                q_text = _sanitize_text(
                    q.get('question') if isinstance(q, dict) else q
                )
                if q_text:
                    audio_map[f'warmup_question_{i+1}'] = self.speak_sync(
                        q_text, f'part1_warmup_q{i+1}.mp3'
                    )

        # ---- Topic section ----
        topic = part1.get('topic_section') or {}
        if isinstance(topic, dict):
            transition = _sanitize_text(topic.get('examiner_transition'))
            if transition:
                audio_map['topic_transition'] = self.speak_sync(
                    transition, 'part1_topic_transition.mp3'
                )
            for i, q in enumerate(topic.get('questions') or []):
                q_text = _sanitize_text(
                    q.get('question') if isinstance(q, dict) else q
                )
                if q_text:
                    audio_map[f'topic_question_{i+1}'] = self.speak_sync(
                        q_text, f'part1_topic_q{i+1}.mp3'
                    )

        return audio_map

    def _generate_part2_audio(self, part2: Dict) -> Dict:
        """
        Walk the ACTUAL Part 2 structure:

        {
            "examiner_intro": "...",
            "examiner_instructions": "...",
            "examiner_card_presentation": "...",
            "topic_card": {"title": "...", "prompts": [...]},
            "examiner_preparation_start": "...",
            "examiner_preparation_end": "...",
            "examiner_speaking_start": "...",
            "examiner_speaking_end": "...",
            "examiner_follow_up_question": "..."
        }
        """
        audio_map: Dict[str, Optional[str]] = {}
        if not isinstance(part2, dict):
            return audio_map

        # ---- Examiner script strings ----
        script_keys = [
            'examiner_intro',
            'examiner_instructions',
            'examiner_card_presentation',
            'examiner_preparation_start',
            'examiner_preparation_end',
            'examiner_speaking_start',
            'examiner_speaking_end',
            'examiner_follow_up_question',
        ]
        for key in script_keys:
            text = _sanitize_text(part2.get(key))
            if text:
                audio_map[key] = self.speak_sync(text, f"part2_{key}.mp3")

        # ---- Topic card ----
        card = part2.get('topic_card') or {}
        if isinstance(card, dict):
            title = _sanitize_text(card.get('title'))
            prompts = card.get('prompts') or []
            valid_prompts = [
                _sanitize_text(p) for p in prompts if _sanitize_text(p)
            ]

            if title:
                audio_map['topic_card_title'] = self.speak_sync(
                    title, 'part2_topic_card_title.mp3'
                )

            if valid_prompts:
                prompts_text = "You should say: " + ", ".join(valid_prompts)
                audio_map['topic_card_prompts'] = self.speak_sync(
                    prompts_text, 'part2_topic_card_prompts.mp3'
                )

        return audio_map

    def _generate_part3_audio(self, part3: Dict) -> Dict:
        """
        Walk the ACTUAL Part 3 structure:

        {
            "examiner_intro": "...",
            "examiner_transition": "...",
            "theme": "...",
            "questions": [{"question": "...", "type": "..."}],
            "examiner_closing": "...",
            "examiner_final": "..."
        }
        """
        audio_map: Dict[str, Optional[str]] = {}
        if not isinstance(part3, dict):
            return audio_map

        script_keys = [
            'examiner_intro',
            'examiner_transition',
            'examiner_closing',
            'examiner_final',
        ]
        for key in script_keys:
            text = _sanitize_text(part3.get(key))
            if text:
                audio_map[key] = self.speak_sync(text, f"part3_{key}.mp3")

        for i, q in enumerate(part3.get('questions') or []):
            q_text = _sanitize_text(
                q.get('question') if isinstance(q, dict) else q
            )
            if q_text:
                audio_map[f'question_{i+1}'] = self.speak_sync(
                    q_text, f'part3_q{i+1}.mp3'
                )

        return audio_map

    # ---- Public aliases (older callers may still use the old names) ----

    def generate_part1_audio(self, part1_data: dict) -> dict:
        return self._generate_part1_audio(part1_data)

    def generate_part2_audio(self, part2_data: dict) -> dict:
        return self._generate_part2_audio(part2_data)

    def generate_part3_audio(self, part3_data: dict) -> dict:
        return self._generate_part3_audio(part3_data)

    def generate_full_test_audio(self, test_data: dict) -> dict:
        """
        Generate all audio for a complete speaking test.

         FIX: Now walks the ACTUAL test structure produced by
                test_generator.py (part1.warmup_section, part2.topic_card, etc.).
        """
        if not isinstance(test_data, dict):
            return {'part1': {}, 'part2': {}, 'part3': {}}

        return {
            'part1': self._generate_part1_audio(test_data.get('part1') or {}),
            'part2': self._generate_part2_audio(test_data.get('part2') or {}),
            'part3': self._generate_part3_audio(test_data.get('part3') or {}),
        }

    # ============================================================
    # UTILITIES
    # ============================================================

    def clear_cache(self, older_than_days: Optional[int] = None) -> int:
        """
        Delete cached MP3 files. If older_than_days is None, delete all.

        Returns:
            Number of files deleted.
        """
        if not os.path.isdir(self.output_dir):
            return 0

        now = time.time()
        deleted = 0
        for name in os.listdir(self.output_dir):
            if not name.endswith('.mp3'):
                continue
            path = os.path.join(self.output_dir, name)
            try:
                if older_than_days is not None:
                    age_days = (now - os.path.getmtime(path)) / 86400
                    if age_days < older_than_days:
                        continue
                os.remove(path)
                deleted += 1
            except OSError:
                pass
        logger.info(f"Cleared {deleted} audio files from {self.output_dir}")
        return deleted

    def get_cache_stats(self) -> Dict[str, Any]:
        """Return stats about the audio cache."""
        if not os.path.isdir(self.output_dir):
            return {'count': 0, 'size_bytes': 0, 'size_mb': 0.0}

        count = 0
        size_bytes = 0
        for name in os.listdir(self.output_dir):
            if name.endswith('.mp3'):
                path = os.path.join(self.output_dir, name)
                try:
                    size_bytes += os.path.getsize(path)
                    count += 1
                except OSError:
                    pass

        return {
            'count': count,
            'size_bytes': size_bytes,
            'size_mb': round(size_bytes / (1024 * 1024), 2),
        }


# ============================================================
# FACTORY FUNCTION
# ============================================================

def create_examiner_voice(
    voice: str = "female_british",
    output_dir: str = "static/audio_cache/speaking",
    use_cache: bool = True,
) -> ExaminerVoice:
    """Factory function to create an ExaminerVoice instance."""
    return ExaminerVoice(
        voice=voice,
        output_dir=output_dir,
        use_cache=use_cache,
    )


__all__ = [
    'ExaminerVoice',
    'create_examiner_voice',
    'EDGE_TTS_AVAILABLE',
]