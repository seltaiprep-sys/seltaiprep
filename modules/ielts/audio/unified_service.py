# modules/audio/unified_service.py
"""
Unified Audio Service — Deepgram Aura-2 TTS (primary) + Edge TTS (fallback).

Provides:
  • Listening section audio (via audio_generator with Deepgram + Edge TTS)
  • Speaking audio (via Deepgram Aura-2 TTS, Edge TTS fallback)
  • Cache management

Voice strategy for PTE:
  • Real exam uses mixed accents (US, UK)
  • Each question picks a deterministic voice (stable on retry)
  • Deepgram Aura-2 = studio quality (~48kHz, 128kbps)
  • Edge TTS = fallback (24kHz, 48kbps)

 v3.0 — Voice Pool Fix + Dynamic Blacklisting:
   • Voice pool reduced to VERIFIED working Aura-2 voices only
     (based on actual Deepgram API responses in server logs)
   • Invalid voices (aura-2-helios-en, aura-2-perseus-en,
     aura-2-stella-en, aura-2-angus-en) REMOVED
   • Runtime blacklisting: if a voice returns HTTP 400 "no such
     model", it's added to a process-level blacklist and never
     retried — subsequent requests skip it immediately
   • Edge TTS voice mapping updated to only working entries
"""

import logging
import hashlib
import time
import asyncio
import concurrent.futures
import threading
from pathlib import Path
from typing import Dict, Optional, List, Set

# ─────────────────────────────────────────────────────────────────
# External dependencies
# ─────────────────────────────────────────────────────────────────

# Existing listening audio generator (Deepgram + Edge TTS + gTTS)
try:
    from modules.ielts.listening.audio_generator import audio_generator as _audio_generator
    AUDIO_GENERATOR_AVAILABLE = True
except ImportError:
    _audio_generator = None
    AUDIO_GENERATOR_AVAILABLE = False
    logging.warning("audio_generator not available. Listening features disabled.")

# Edge TTS (fallback for speaking)
try:
    import edge_tts
    EDGE_AVAILABLE = True
except ImportError:
    EDGE_AVAILABLE = False
    logging.warning("edge-tts not available. Edge fallback disabled.")

# Deepgram Aura TTS (primary for speaking)
try:
    from modules.pte.utils.deepgram_service import deepgram_service
    DEEPGRAM_TTS_AVAILABLE = bool(deepgram_service and deepgram_service.api_key)
    if DEEPGRAM_TTS_AVAILABLE:
        logging.getLogger(__name__).info(" Deepgram Aura TTS available (primary)")
    else:
        logging.getLogger(__name__).warning(" Deepgram API key missing — using Edge TTS only")
except ImportError:
    deepgram_service = None
    DEEPGRAM_TTS_AVAILABLE = False
    logging.warning(" deepgram_service not available")

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# PTE Voice Pool — VERIFIED Aura-2 voices only
# ─────────────────────────────────────────────────────────────────
# These voices have been verified working against Deepgram API.
# All invalid voices have been removed (see v3.0 notes above).

PTE_DEEPGRAM_VOICES = [
    # ── US English ────────────────────────────────────────────────
    'aura-2-luna-en', # US female — warm (VERIFIED )
    'aura-2-arcas-en', # US male — steady (VERIFIED )
    'aura-2-orion-en', # US male — deep (VERIFIED )
    'aura-2-asteria-en', # US female — crisp
    'aura-2-thalia-en', # US female — friendly
    'aura-2-apollo-en', # US male — clear
    'aura-2-pandora-en', # US female — calm

    # ── British English ───────────────────────────────────────────
    'aura-2-athena-en', # UK female — formal (VERIFIED )
    'aura-2-hera-en', # UK female — poised
]

# Deepgram Aura-2 → Edge TTS fallback voice mapping
# Only entries for voices that exist in PTE_DEEPGRAM_VOICES
DEEPGRAM_TO_EDGE_VOICE = {
    'aura-2-luna-en': 'en-US-JennyNeural',
    'aura-2-arcas-en': 'en-US-ChristopherNeural',
    'aura-2-orion-en': 'en-US-GuyNeural',
    'aura-2-asteria-en': 'en-US-AriaNeural',
    'aura-2-thalia-en': 'en-US-MichelleNeural',
    'aura-2-apollo-en': 'en-US-EricNeural',
    'aura-2-pandora-en': 'en-US-SaraNeural',
    'aura-2-athena-en': 'en-GB-SoniaNeural',
    'aura-2-hera-en': 'en-GB-LibbyNeural',
}

# Fallback Edge voice when the Deepgram voice isn't in the map
DEFAULT_EDGE_VOICE = 'en-US-AriaNeural'


# ─────────────────────────────────────────────────────────────────
# Runtime voice blacklist
# ─────────────────────────────────────────────────────────────────
# When Deepgram returns 400 "No such model/version combination",
# we add the voice to this set and never retry it in this process.
# This prevents log spam and wasted API calls.

_voice_blacklist: Set[str] = set()
_voice_blacklist_lock = threading.Lock()


def _is_voice_blacklisted(voice: str) -> bool:
    """Check if a voice has been marked as unavailable."""
    with _voice_blacklist_lock:
        return voice in _voice_blacklist


def _blacklist_voice(voice: str) -> None:
    """Add a voice to the runtime blacklist."""
    with _voice_blacklist_lock:
        if voice not in _voice_blacklist:
            _voice_blacklist.add(voice)
            logger.warning(
                f" Voice '{voice}' blacklisted (Deepgram rejected it). "
                f"Blacklist size: {len(_voice_blacklist)}"
            )


def _get_active_voice_pool() -> List[str]:
    """Return the voice pool minus any blacklisted voices."""
    with _voice_blacklist_lock:
        return [v for v in PTE_DEEPGRAM_VOICES if v not in _voice_blacklist]


def _stable_voice_index(key: str, pool_size: int) -> int:
    """Deterministic voice index from a stable key (e.g. question id)."""
    if not key or pool_size <= 0:
        return 0
    try:
        digest = hashlib.md5(key.encode('utf-8')).hexdigest()
        return int(digest[:8], 16) % pool_size
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────────
# Unified Audio Service
# ─────────────────────────────────────────────────────────────────

class UnifiedAudioService:
    """
    Unified audio service for all modules.

    Listening: uses audio_generator (which internally uses Deepgram/Edge)
    Speaking : uses Deepgram Aura-2 TTS (primary), Edge TTS (fallback)
    """

    # Minimum valid MP3 size (10 KB ≈ 1 second of clear audio)
    MIN_AUDIO_BYTES = 10000

    def __init__(self):
        self._audio_generator = _audio_generator

        # Cache directory
        self.cache_dir = Path("static/audio_cache")
        if self._audio_generator and hasattr(self._audio_generator, 'cache_dir'):
            self.cache_dir = self._audio_generator.cache_dir

        self.cache_dir.mkdir(parents=True, exist_ok=True)

        if self._audio_generator:
            logger.info(" UnifiedAudioService initialized (Deepgram + Edge + gTTS)")
        else:
            logger.warning(" UnifiedAudioService: audio_generator unavailable")

        logger.info(
            f" Speaking TTS — Deepgram: {DEEPGRAM_TTS_AVAILABLE}, "
            f"Edge: {EDGE_AVAILABLE}"
        )
        logger.info(f" Voice pool size: {len(PTE_DEEPGRAM_VOICES)}")

    # ═══════════════════════════════════════════════════════════════
    # LISTENING AUDIO (unchanged — uses existing audio_generator)
    # ═══════════════════════════════════════════════════════════════
    def generate_listening_section_audio(
        self,
        section_data: Dict,
        section_num: int,
        custom_script: str = None,
        include_instructions: bool = True,
    ) -> Dict:
        """
        Generate audio for a listening section using audio_generator.
        Returns: {'success', 'merged_audio', 'duration', 'section'}.
        """
        if not self._audio_generator:
            return {"success": False, "error": "audio_generator not available"}

        script = custom_script or section_data.get('script', '')
        if not script:
            return {"success": False, "error": "No script provided"}

        try:
            accent = section_data.get('accent', 'british')
            audio_url, error, timings = self._audio_generator.generate(
                script=script,
                section_number=section_num,
                test_title=f"listening_sec{section_num}",
                accent=accent,
                total_questions=10,
                include_instructions=include_instructions,
                fast=True,
            )
            if audio_url:
                duration = timings.get('duration', 30)
                return {
                    "success": True,
                    "merged_audio": audio_url,
                    "duration": duration,
                    "section": section_num,
                }
            return {"success": False, "error": error or "Unknown error"}
        except Exception as e:
            logger.error(f"generate_listening_section_audio error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # SPEAKING AUDIO — Deepgram Aura-2 (primary) + Edge TTS (fallback)
    # ═══════════════════════════════════════════════════════════════
    def generate_speaking_audio(
        self,
        text: str,
        part: int = 1,
        question_num: int = 0,
        custom_filename: Optional[str] = None,
    ) -> Optional[str]:
        """
        Generate speaking audio.

        Priority:
          1. Deepgram Aura-2 TTS (highest quality — 48kHz/128kbps)
          2. Edge TTS (fallback — 24kHz/48kbps)

        Returns:
            URL like '/static/audio_cache/speaking/q3_abc12345.mp3'
            or None on total failure.
        """
        if not text or not text.strip():
            logger.warning("generate_speaking_audio: empty text")
            return None

        # ── Deterministic voice selection from active pool ────────
        active_pool = _get_active_voice_pool()
        if not active_pool:
            # All voices blacklisted — go straight to Edge TTS
            logger.warning(
                " All Deepgram voices blacklisted — using Edge TTS only"
            )
            voice = None
        else:
            voice = active_pool[question_num % len(active_pool)]

        # ── Filename resolution ───────────────────────────────────
        if custom_filename:
            filename = custom_filename
        else:
            text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()[:8]
            filename = f"speaking/q{question_num}_{text_hash}.mp3"

        filepath = self.cache_dir / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # ── PRIMARY: Deepgram Aura TTS ────────────────────────────
        if voice and DEEPGRAM_TTS_AVAILABLE and deepgram_service:
            result_url = self._generate_with_deepgram(
                text=text, voice=voice, filepath=filepath, filename=filename,
            )
            if result_url:
                return result_url
            logger.info(f" Deepgram failed for voice '{voice}' → falling back to Edge TTS")

        # ── FALLBACK: Edge TTS ────────────────────────────────────
        if EDGE_AVAILABLE:
            # If voice is None (all blacklisted), fall back to default
            fallback_voice = voice or DEFAULT_EDGE_VOICE
            result_url = self._generate_with_edge(
                text=text, deepgram_voice=fallback_voice,
                filepath=filepath, filename=filename,
            )
            if result_url:
                return result_url

        logger.error(f" All TTS engines failed for: {text[:60]}...")
        return None

    # ─────────────────────────────────────────────────────────────
    # Deepgram Aura TTS (primary)
    # ─────────────────────────────────────────────────────────────
    def _generate_with_deepgram(
        self, text: str, voice: str, filepath: Path, filename: str,
    ) -> Optional[str]:
        """Generate audio using Deepgram Aura TTS. Returns URL or None."""
        try:
            audio_buffer = deepgram_service.text_to_speech(
                text=text,
                voice=voice,
                speed=1.0,
                output_format='mp3',
            )
            if not audio_buffer:
                return None

            # Write BytesIO → disk
            with open(filepath, 'wb') as f:
                f.write(audio_buffer.read())

            # Validate size
            if not filepath.exists():
                logger.warning(f"Deepgram file not written: {filepath}")
                return None

            size = filepath.stat().st_size
            if size < self.MIN_AUDIO_BYTES:
                logger.warning(
                    f" Deepgram file too small ({size} bytes < "
                    f"{self.MIN_AUDIO_BYTES}) — deleting"
                )
                try: filepath.unlink()
                except: pass
                return None

            size_kb = size // 1024
            logger.info(f" Deepgram Aura-2: {filename} ({size_kb} KB, voice={voice})")
            return f"/static/audio_cache/{filename}"

        except Exception as e:
            err_msg = str(e)

            # Detect "voice not found" → blacklist it
            is_voice_error = (
                'no such model' in err_msg.lower()
                or 'no such model/version' in err_msg.lower()
                or ('400' in err_msg and 'bad request' in err_msg.lower())
            )

            if is_voice_error and voice:
                _blacklist_voice(voice)
                logger.warning(
                    f" Deepgram rejected voice '{voice}' (400). "
                    f"Blacklisted for this process. Falling back to Edge TTS."
                )
            else:
                logger.error(f"Deepgram TTS error: {e}", exc_info=True)

            try:
                if filepath.exists():
                    filepath.unlink()
            except: pass
            return None

    # ─────────────────────────────────────────────────────────────
    # Edge TTS (fallback)
    # ─────────────────────────────────────────────────────────────
    def _generate_with_edge(
        self, text: str, deepgram_voice: str, filepath: Path, filename: str,
    ) -> Optional[str]:
        """Generate audio using Edge TTS as fallback. Returns URL or None."""
        try:
            edge_voice = DEEPGRAM_TO_EDGE_VOICE.get(deepgram_voice, DEFAULT_EDGE_VOICE)

            # Thread-safe asyncio — dedicated event loop
            def _run_edge_tts():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(
                        edge_tts.Communicate(text, edge_voice).save(str(filepath))
                    )
                finally:
                    loop.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(_run_edge_tts).result(timeout=30)

            # Validate size
            if not filepath.exists():
                logger.warning(f"Edge TTS file not written: {filepath}")
                return None

            size = filepath.stat().st_size
            if size < self.MIN_AUDIO_BYTES:
                logger.warning(
                    f" Edge TTS file too small ({size} bytes) — deleting"
                )
                try: filepath.unlink()
                except: pass
                return None

            size_kb = size // 1024
            logger.info(f" Edge TTS fallback: {filename} ({size_kb} KB, voice={edge_voice})")
            return f"/static/audio_cache/{filename}"

        except concurrent.futures.TimeoutError:
            logger.error(f"Edge TTS timeout for: {text[:60]}...")
            try:
                if filepath.exists():
                    filepath.unlink()
            except: pass
            return None
        except Exception as e:
            logger.error(f"Edge TTS error: {e}", exc_info=True)
            try:
                if filepath.exists():
                    filepath.unlink()
            except: pass
            return None

    # ═══════════════════════════════════════════════════════════════
    # TRANSCRIPTION (STT) — delegates to Deepgram
    # ═══════════════════════════════════════════════════════════════
    def transcribe_audio(self, audio_bytes: bytes, content_type: str = 'audio/webm') -> Dict:
        """
        Transcribe audio using Deepgram STT.
        Returns dict with {success, transcript, confidence, words, utterances}.
        """
        if not DEEPGRAM_TTS_AVAILABLE or not deepgram_service:
            return {"success": False, "error": "Deepgram service not available"}

        try:
            return deepgram_service.transcribe(
                audio_data=audio_bytes,
                language='en-US',
                smart_format=True,
                content_type=content_type,
            )
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return {"success": False, "error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # CACHE MANAGEMENT
    # ═══════════════════════════════════════════════════════════════
    def clear_cache(self, older_than_days: int = 30) -> int:
        """Remove cached audio files older than the specified number of days."""
        removed = 0
        cutoff = time.time() - (older_than_days * 86400)
        for f in self.cache_dir.glob("**/*.mp3"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except Exception:
                pass
        logger.info(f" Cleared {removed} audio files older than {older_than_days} days")
        return removed

    def clear_speaking_cache(self) -> int:
        """Remove all cached speaking audio files."""
        removed = 0
        speaking_dir = self.cache_dir / "speaking"
        if not speaking_dir.exists():
            return 0
        for f in speaking_dir.glob("*.mp3"):
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass
        logger.info(f" Cleared {removed} speaking audio files")
        return removed

    # ═══════════════════════════════════════════════════════════════
    # STATISTICS
    # ═══════════════════════════════════════════════════════════════
    def get_stats(self) -> Dict:
        """Return basic statistics about the service."""
        with _voice_blacklist_lock:
            blacklisted = sorted(_voice_blacklist)

        active_pool = _get_active_voice_pool()

        stats = {
            "cache_dir": str(self.cache_dir),
            "audio_generator_available": AUDIO_GENERATOR_AVAILABLE,
            "edge_available": EDGE_AVAILABLE,
            "deepgram_tts_available": DEEPGRAM_TTS_AVAILABLE,
            "primary_engine": "deepgram" if DEEPGRAM_TTS_AVAILABLE else ("edge" if EDGE_AVAILABLE else "none"),
            "voice_model": "aura-2",
            "voice_pool_total": len(PTE_DEEPGRAM_VOICES),
            "voice_pool_active": len(active_pool),
            "voice_blacklist": blacklisted,
            "cache_size_mb": 0,
            "cache_file_count": 0,
        }

        total_size = 0
        count = 0
        for f in self.cache_dir.glob("**/*.mp3"):
            try:
                total_size += f.stat().st_size
                count += 1
            except Exception:
                pass

        stats["cache_size_mb"] = round(total_size / (1024 * 1024), 2)
        stats["cache_file_count"] = count

        # Speaking-specific stats
        speaking_dir = self.cache_dir / "speaking"
        if speaking_dir.exists():
            speaking_files = list(speaking_dir.glob("*.mp3"))
            stats["speaking_file_count"] = len(speaking_files)
            stats["speaking_size_mb"] = round(
                sum(f.stat().st_size for f in speaking_files if f.exists()) / (1024 * 1024), 2
            )
        else:
            stats["speaking_file_count"] = 0
            stats["speaking_size_mb"] = 0.0

        return stats


# ─────────────────────────────────────────────────────────────────
# Singleton instance
# ─────────────────────────────────────────────────────────────────
audio_service = UnifiedAudioService()