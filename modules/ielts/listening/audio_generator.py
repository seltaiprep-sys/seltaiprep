"""Audio generator for IELTS Listening - Hybrid TTS

═══════════════════════════════════════════════════════════════════════
ENGINE + VOICE POLICY (v8 — POOL-BASED AUDIO CACHE):
═══════════════════════════════════════════════════════════════════════
  Section 1 & 3 → Deepgram + seeded-by-pool_id random voice
  Section 2 & 4 → Edge TTS + seeded-by-pool_id random voice

   NEW in v8:
    • Deterministic voices (seeded by pool_id) → same test = same audio
    • Filename keyed by pool_id → cache hit on 2nd+ user
    • Audio cache check FIRST → skip TTS if file exists
    • Saves ~99% TTS cost when pool reused across users

  Within ONE session: same speaker keeps same voice (session cache).
  Across sessions with same pool_id: same voices, same audio files.
  Across sessions with different pool_id: fresh random voices.
═══════════════════════════════════════════════════════════════════════
"""

import os
import re
import asyncio
import hashlib
import shutil
import uuid
import logging
import json
import time
import threading
import random
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Set

logger = logging.getLogger(__name__)

try:
    import edge_tts
    EDGE_AVAILABLE = True
except ImportError:
    EDGE_AVAILABLE = False
    logger.warning("edge_tts not installed")

try:
    from gtts import gTTS
    GTTS_AVAILABLE = True
except ImportError:
    GTTS_AVAILABLE = False
    logger.warning("gTTS not installed")

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    logger.warning("requests not installed")

try:
    from mutagen.mp3 import MP3
    MUTAGEN_AVAILABLE = True
    logger.info(" mutagen available – will use actual MP3 durations")
except ImportError:
    MUTAGEN_AVAILABLE = False
    logger.warning(" mutagen not installed – duration will be estimated")

DEEPGRAM_API_KEY = os.environ.get('DEEPGRAM_API_KEY')
DEEPGRAM_TTS_URL = "https://api.deepgram.com/v1/speak"
DEEPGRAM_AVAILABLE = bool(DEEPGRAM_API_KEY and REQUESTS_AVAILABLE)

if DEEPGRAM_AVAILABLE:
    logger.info(" Deepgram TTS available")
else:
    logger.warning(" Deepgram TTS disabled")


# ═══════════════════════════════════════════════════════════════════
# POOL-BASED AUDIO CACHE — v8
#
# When a test is served from the pool (pool_id is not None), the
# audio files are named deterministically:
#
# section_{N}_pool_{pool_id}_{accent}_main.mp3
#
# This lets subsequent users who receive the SAME pool_id skip the
# TTS generation entirely (cache hit).
#
# When pool_id is None (fresh generation), we fall back to the old
# session-based unique filename so nothing breaks.
# ═══════════════════════════════════════════════════════════════════
CACHE_VERSION = "v8" # bump to invalidate all cached audio


class AudioGenerator:
    def __init__(self, cache_dir: str = "static/audio_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # ═══════════════════════════════════════════════════════════
        # SECTION 1 & 3 — DEEPGRAM voice pools (random pick)
        # ═══════════════════════════════════════════════════════════
        self.deepgram_voice_pools = {
            'agent': {
                'male': [
                    'aura-2-apollo-en',
                    'aura-2-orion-en',
                    'aura-2-draco-en',
                    'aura-2-arcas-en',
                ],
                'female': [
                    'aura-2-asteria-en',
                    'aura-2-helena-en',
                    'aura-2-athena-en',
                    'aura-2-aurora-en',
                    'aura-2-pandora-en',
                ],
            },
            'customer': {
                'male': [
                    'aura-2-orion-en',
                    'aura-2-apollo-en',
                    'aura-2-draco-en',
                    'aura-2-hyperion-en',
                ],
                'female': [
                    'aura-2-helena-en',
                    'aura-2-asteria-en',
                    'aura-2-thalia-en',
                    'aura-2-luna-en',
                    'aura-2-theia-en',
                ],
            },
            'tutor': {
                'male': [
                    'aura-2-orion-en',
                    'aura-2-apollo-en',
                    'aura-2-arcas-en',
                ],
                'female': [
                    'aura-2-athena-en',
                    'aura-2-asteria-en',
                    'aura-2-helena-en',
                    'aura-2-aurora-en',
                ],
            },
            'student': {
                'male': [
                    'aura-2-apollo-en',
                    'aura-2-orion-en',
                    'aura-2-draco-en',
                    'aura-2-arcas-en',
                ],
                'female': [
                    'aura-2-thalia-en',
                    'aura-2-luna-en',
                    'aura-2-helena-en',
                    'aura-2-pandora-en',
                    'aura-2-theia-en',
                ],
            },
            'default': {
                'male': ['aura-2-orion-en', 'aura-2-apollo-en', 'aura-2-draco-en'],
                'female': ['aura-2-asteria-en', 'aura-2-helena-en', 'aura-2-athena-en'],
            },
        }

        # ═══════════════════════════════════════════════════════════
        # SECTION 2 & 4 — EDGE TTS voice pools (random pick)
        # ═══════════════════════════════════════════════════════════
        self.edge_monologue_pools = {
            'guide': {
                'male': [
                    'en-GB-ThomasNeural',
                    'en-GB-RyanNeural',
                    'en-US-GuyNeural',
                    'en-AU-WilliamNeural',
                ],
                'female': [
                    'en-GB-SoniaNeural',
                    'en-GB-LibbyNeural',
                    'en-GB-MaisieNeural',
                    'en-US-JennyNeural',
                    'en-US-AriaNeural',
                    'en-AU-NatashaNeural',
                ],
            },
            'lecturer': {
                'male': [
                    'en-GB-ThomasNeural',
                    'en-GB-RyanNeural',
                    'en-US-GuyNeural',
                    'en-AU-WilliamNeural',
                ],
                'female': [
                    'en-GB-SoniaNeural',
                    'en-GB-LibbyNeural',
                    'en-US-JennyNeural',
                    'en-US-AriaNeural',
                    'en-AU-NatashaNeural',
                ],
            },
        }

        # Intro/outro fixed Edge voice
        self.edge_voice_profiles = {
            'default': {'male': 'en-GB-RyanNeural', 'female': 'en-GB-SoniaNeural'},
            'agent': {'male': 'en-GB-ThomasNeural', 'female': 'en-GB-SoniaNeural'},
            'customer': {'male': 'en-GB-RyanNeural', 'female': 'en-GB-LibbyNeural'},
            'tutor': {'male': 'en-GB-ThomasNeural', 'female': 'en-GB-SoniaNeural'},
            'student': {'male': 'en-GB-RyanNeural', 'female': 'en-GB-LibbyNeural'},
            'guide': {'male': 'en-GB-ThomasNeural', 'female': 'en-GB-SoniaNeural'},
            'lecturer': {'male': 'en-GB-ThomasNeural', 'female': 'en-GB-SoniaNeural'},
        }

        self._used_voices = set()
        self._edge_loop = None

        # Session voice cache — (section, role:gender) → chosen voice
        self._session_voice_cache: Dict[Tuple[int, str], str] = {}
        self._voice_cache_lock = threading.Lock()

        # v8 — Pool-seeded RNG cache (deterministic voices per pool_id)
        self._pool_rng_cache: Dict[str, random.Random] = {}
        self._pool_rng_lock = threading.Lock()

        # Semaphore for parallel Deepgram calls
        self._deepgram_limit = threading.Semaphore(24)

        # HTTP connection pooling
        self._http_session = requests.Session() if REQUESTS_AVAILABLE else None
        if self._http_session:
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=24,
                pool_maxsize=24,
                max_retries=0,
                pool_block=False,
            )
            self._http_session.mount('https://', adapter)
            self._http_session.mount('http://', adapter)
            logger.info(" HTTP session with 24-connection pool created")

        if self._http_session:
            try:
                self._http_session.get('https://api.deepgram.com/', timeout=3)
                logger.info(" Pre-warmed Deepgram connection (DNS + TLS ready)")
            except Exception:
                pass

        # Startup
        self._ensure_intro_files(accent='british')
        self._ensure_outro_files(accent='british')

    # ============================================================
    # SESSION VOICE CACHE MANAGEMENT
    # ============================================================
    def start_new_session(self):
        """Call at the start of each new test."""
        with self._voice_cache_lock:
            self._session_voice_cache.clear()
            logger.info(" Voice session cache cleared — new random voices next")

    # v8 — Pool-seeded RNG helpers
    def _get_pool_rng(self, pool_id: int) -> random.Random:
        """
        Return a deterministic Random instance seeded by pool_id.
        Same pool_id → same voices → same audio (cache-friendly).
        """
        key = f"pool_{pool_id}_{CACHE_VERSION}"
        with self._pool_rng_lock:
            if key not in self._pool_rng_cache:
                self._pool_rng_cache[key] = random.Random(key)
            return self._pool_rng_cache[key]

    def _normalize_speaker(self, speaker: str) -> str:
        return re.sub(r'[\s_\-]+', '', (speaker or '')).lower()

    # ============================================================
    # MP3 duration helper
    # ============================================================
    def _get_mp3_duration(self, filepath: Path) -> float:
        if not filepath.exists():
            return 0.0
        if MUTAGEN_AVAILABLE:
            try:
                audio = MP3(filepath)
                return audio.info.length
            except Exception as e:
                logger.warning(f"Mutagen duration failed for {filepath}: {e}")
        size = filepath.stat().st_size
        if size > 0:
            return max(0.5, size / 13500)
        return 0.0

    def _cleanup_temp_files(self, file_paths: List[str], keep: Optional[List[str]] = None):
        keep_set = set(keep or [])
        for path in file_paths:
            if path in keep_set:
                continue
            try:
                if os.path.exists(path) and os.path.isfile(path):
                    os.remove(path)
            except Exception as e:
                logger.warning(f" Could not delete temp file {path}: {e}")

    # ============================================================
    # v8 — POOL-BASED CACHE PATH BUILDER
    # ============================================================
    def _get_cached_pool_audio_path(
        self, pool_id: int, section_num: int, accent: str
    ) -> Tuple[Path, str]:
        """
        Deterministic cache filename for a pool-served test.
        Same (pool_id, section_num, accent) → same file.
        """
        filename = (
            f"{CACHE_VERSION}_section_{section_num}_"
            f"pool_{pool_id}_{accent}_main.mp3"
        )
        return self.cache_dir / filename, filename

    # ============================================================
    # INTRO CACHING
    # ============================================================
    def _get_intro_text(self, section_num: int, total_questions: int = 10) -> str:
        types = {
            1: "a conversation between a customer and an agent",
            2: "a monologue by a tour guide or information officer",
            3: "a discussion between a tutor and students",
            4: "a university lecture"
        }
        type_desc = types.get(section_num, "a listening passage")
        start_q = (section_num - 1) * 10 + 1
        end_q = section_num * 10
        return f"Section {section_num}. You will hear {type_desc}. First, you have 30 seconds to read questions {start_q} to {end_q}."

    async def _get_cached_intro_audio(self, section_num: int, accent: str = 'british') -> Optional[str]:
        static_filename = f"intro_section_{section_num}_{accent}.mp3"
        static_path = self.cache_dir / static_filename

        if static_path.exists() and static_path.stat().st_size > 2000:
            logger.info(f" Intro already cached: {static_filename}")
            return f"/audio_cache/{static_filename}"

        intro_text = self._get_intro_text(section_num, 10)
        edge_voice = self.edge_voice_profiles['default']['female']

        if EDGE_AVAILABLE:
            try:
                await edge_tts.Communicate(intro_text, edge_voice).save(str(static_path))
                if static_path.exists() and static_path.stat().st_size > 2000:
                    logger.info(f" Intro cached: {static_filename}")
                    return f"/audio_cache/{static_filename}"
            except Exception as e:
                logger.error(f"Intro generation failed: {e}")
        return None

    def _ensure_intro_files(self, accent: str = 'british'):
        for section in range(1, 5):
            static_filename = f"intro_section_{section}_{accent}.mp3"
            static_path = self.cache_dir / static_filename
            if not static_path.exists() or static_path.stat().st_size < 2000:
                logger.info(f" Generating intro for Section {section} ({accent}) at startup...")
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    path = loop.run_until_complete(self._get_cached_intro_audio(section, accent))
                    loop.close()
                    if path:
                        logger.info(f" Intro for Section {section} ({accent}) generated.")
                    else:
                        logger.warning(f" Failed to generate intro for Section {section} ({accent})")
                except Exception as e:
                    logger.error(f" Error generating intro for Section {section}: {e}")
            else:
                logger.info(f" Intro for Section {section} ({accent}) already exists.")

    # ============================================================
    # OUTRO CACHING
    # ============================================================
    def _get_outro_text(self, section_num: int) -> str:
        return f"That is the end of Section {section_num}. You now have 30 seconds to check your answers."

    async def _get_cached_outro_audio(self, section_num: int, accent: str = 'british') -> Optional[str]:
        static_filename = f"outro_section_{section_num}_{accent}.mp3"
        static_path = self.cache_dir / static_filename

        if static_path.exists() and static_path.stat().st_size > 2000:
            logger.info(f" Outro already cached: {static_filename}")
            return f"/audio_cache/{static_filename}"

        outro_text = self._get_outro_text(section_num)
        edge_voice = self.edge_voice_profiles['default']['female']

        if EDGE_AVAILABLE:
            try:
                await edge_tts.Communicate(outro_text, edge_voice).save(str(static_path))
                if static_path.exists() and static_path.stat().st_size > 2000:
                    logger.info(f" Outro cached: {static_filename}")
                    return f"/audio_cache/{static_filename}"
            except Exception as e:
                logger.error(f"Outro generation failed: {e}")
        return None

    def _ensure_outro_files(self, accent: str = 'british'):
        for section in range(1, 5):
            static_filename = f"outro_section_{section}_{accent}.mp3"
            static_path = self.cache_dir / static_filename
            if not static_path.exists() or static_path.stat().st_size < 2000:
                logger.info(f" Generating outro for Section {section} ({accent}) at startup...")
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    path = loop.run_until_complete(self._get_cached_outro_audio(section, accent))
                    loop.close()
                    if path:
                        logger.info(f" Outro for Section {section} ({accent}) generated.")
                    else:
                        logger.warning(f" Failed to generate outro for Section {section} ({accent})")
                except Exception as e:
                    logger.error(f" Error generating outro for Section {section}: {e}")
            else:
                logger.info(f" Outro for Section {section} ({accent}) already exists.")

    # ============================================================
    # Merge consecutive same-speaker turns
    # ============================================================
    def _merge_consecutive_turns(self, turns: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        if not turns:
            return turns
        merged = []
        current_speaker, current_text = turns[0]
        for speaker, text in turns[1:]:
            if speaker == current_speaker:
                current_text = f"{current_text} {text}".strip()
            else:
                merged.append((current_speaker, current_text))
                current_speaker, current_text = speaker, text
        merged.append((current_speaker, current_text))
        if len(merged) < len(turns):
            logger.info(f" Merged {len(turns)} turns → {len(merged)} (saved {len(turns) - len(merged)} API calls)")
        return merged

    # ============================================================
    # MONOLOGUE SPLITTING (Section 2 & 4)
    # ============================================================
    def _split_monologue(self, script: str, section_num: int,
                          speaker_label: str) -> List[Tuple[str, str]]:
        text = (script or '').strip()
        if not text:
            return [('default', script or '')]

        MIN_WORDS_PER_CHUNK = 40

        paragraphs = [
            p.strip() for p in re.split(r'\n\s*\n', text)
            if p.strip()
        ]

        merged_paras: List[str] = []
        buffer = ''
        for p in paragraphs:
            buffer = f"{buffer} {p}".strip() if buffer else p
            if len(buffer.split()) >= MIN_WORDS_PER_CHUNK:
                merged_paras.append(buffer)
                buffer = ''
        if buffer:
            if merged_paras:
                merged_paras[-1] = f"{merged_paras[-1]} {buffer}".strip()
            else:
                merged_paras.append(buffer)

        if len(merged_paras) >= 2:
            logger.info(
                f" [S{section_num}] Split monologue into "
                f"{len(merged_paras)} paragraph-chunks "
                f"(sizes: {[len(p.split()) for p in merged_paras]} words)"
            )
            return [(speaker_label, p) for p in merged_paras]

        sentences = re.split(r'(?<=[.!?])\s+', text)
        sentences = [s.strip() for s in sentences if s.strip()]

        if len(sentences) >= 8:
            target_chunks = 4
            chunk_size = max(4, len(sentences) // target_chunks)
            chunks: List[str] = []
            for i in range(0, len(sentences), chunk_size):
                chunk = ' '.join(sentences[i:i + chunk_size]).strip()
                if chunk:
                    chunks.append(chunk)

            if len(chunks) >= 2:
                logger.info(
                    f" [S{section_num}] Split monologue into "
                    f"{len(chunks)} sentence-chunks "
                    f"(sizes: {[len(c.split()) for c in chunks]} words)"
                )
                return [(speaker_label, c) for c in chunks]

        logger.info(
            f" [S{section_num}] Monologue too small to split — "
            f"keeping as single turn ({len(text.split())} words)"
        )
        return [(speaker_label, text)]

    # ============================================================
    # SCRIPT PARSING
    # ============================================================
    def _parse_script(self, script: str, section_num: int = 1) -> List[Tuple[str, str]]:
        if not script:
            return []

        if section_num in (2, 4):
            speaker_label = 'guide' if section_num == 2 else 'lecturer'
            monologue_turns = self._split_monologue(
                script, section_num, speaker_label
            )
            if monologue_turns and len(monologue_turns) > 1:
                return monologue_turns

        lines = script.strip().split('\n')
        turns = []
        current_speaker = None
        current_text = []
        pattern = re.compile(r'^([A-Za-z][A-Za-z0-9_\- ]*)\s*:\s*(.*)$')
        has_label = False

        for line in lines:
            line = line.strip()
            if not line:
                continue
            match = pattern.match(line)
            if match:
                has_label = True
                if current_speaker and current_text:
                    turns.append((current_speaker, ' '.join(current_text).strip()))
                raw_speaker = match.group(1).strip()
                normalised = re.sub(r'[\s_\-]+', '', raw_speaker).lower()
                current_speaker = normalised
                current_text = [match.group(2).strip()]
            else:
                if current_speaker is None:
                    return [('default', script.strip())]
                current_text.append(line)

        if current_speaker and current_text:
            turns.append((current_speaker, ' '.join(current_text).strip()))

        if not has_label or len(turns) <= 1:
            if section_num in (1, 3):
                forced_turns = self._force_split_dialogue(script, section_num)
                if forced_turns and len(forced_turns) > 1:
                    return self._merge_consecutive_turns(forced_turns)
            return [('default', script.strip())]

        turns = self._merge_consecutive_turns(turns)
        return turns if turns else [('default', script.strip())]

    def _force_split_dialogue(self, script: str, section_num: int) -> List[Tuple[str, str]]:
        words = script.split()
        total_words = len(words)
        if total_words < 2:
            return [('default', script)]
        if section_num == 1:
            mid = total_words // 2
            if total_words % 2 == 1:
                mid += 1
            agent_text = ' '.join(words[:mid])
            customer_text = ' '.join(words[mid:]) or words[-1]
            return [('agent', agent_text), ('customer', customer_text)]
        elif section_num == 3:
            part_size = max(1, total_words // 4)
            speakers = ['tutor', 'student1', 'student2', 'student3']
            parts = []
            for i in range(4):
                start = i * part_size
                end = (i + 1) * part_size if i < 3 else total_words
                if start < total_words:
                    parts.append((speakers[i], ' '.join(words[start:end])))
            return parts
        return [('default', script)]

    # ============================================================
    # GENDER INFERENCE
    # ============================================================
    def _infer_gender_from_name(self, name: str) -> Optional[str]:
        norm = re.sub(r'[\s_\-]+', '', name).lower()
        female_names = {'mary', 'sarah', 'emma', 'olivia', 'sophia', 'anna', 'jane',
                        'student1', 'student3', 'customer', 'visitor', 'guest'}
        male_names = {'john', 'james', 'david', 'robert', 'michael', 'william',
                      'agent', 'tutor', 'guide', 'professor', 'lecturer', 'examiner'}
        if norm in female_names:
            return 'female'
        if norm in male_names:
            return 'male'
        if norm.endswith('a'):
            return 'female'
        return None

    def _normalize_speaker_role(self, speaker: str) -> str:
        speaker_lower = speaker.lower()
        roles = ['agent', 'customer', 'tutor', 'student', 'guide', 'lecturer', 'examiner', 'default']
        for role in roles:
            if role in speaker_lower:
                return role
        if 'student' in speaker_lower:
            return 'student'
        return 'default'

    # ============================================================
    # RANDOM VOICE PICKERS (v8 — pool-aware)
    # ============================================================
    def _pick_random_deepgram_voice(
        self, role: str, gender: str, rng: Optional[random.Random] = None
    ) -> str:
        """Random Deepgram voice for Section 1 & 3. Uses pool-seeded RNG if provided."""
        role_pool = self.deepgram_voice_pools.get(role)
        if not role_pool:
            role_pool = self.deepgram_voice_pools['default']

        candidates = list(role_pool.get(gender, role_pool.get('male', [])))
        if not candidates:
            return 'aura-2-asteria-en'
        _rng = rng if rng is not None else random
        return _rng.choice(candidates)

    def _pick_random_edge_voice(
        self, role: str, gender: str, rng: Optional[random.Random] = None
    ) -> str:
        """Random Edge TTS voice for Section 2 & 4. Uses pool-seeded RNG if provided."""
        role_pool = self.edge_monologue_pools.get(role)
        if not role_pool:
            role_pool = self.edge_monologue_pools.get('guide', {})

        candidates = list(role_pool.get(gender, role_pool.get('male', [])))
        if not candidates:
            return 'en-GB-SoniaNeural'
        _rng = rng if rng is not None else random
        return _rng.choice(candidates)

    def _get_voice_for_speaker(
        self,
        speaker: str,
        accent: str = 'british',
        context: str = '',
        use_edge: bool = False,
        forced_gender: Optional[str] = None,
        section_num: int = 0,
        pool_id: Optional[int] = None, # v8
    ) -> str:
        """
        Section-aware voice picker with session consistency.

        • Section 1 & 3 → random Deepgram voice
        • Section 2 & 4 → random Edge TTS voice
        • Section 0 (intro/outro) → fixed Edge voice

         v8: If `pool_id` provided, voices are deterministic (seeded by pool_id).
        """
        role = self._normalize_speaker_role(speaker)

        # Resolve gender
        if forced_gender and forced_gender.lower() in ('male', 'female'):
            gender = forced_gender.lower()
        else:
            inferred = self._infer_gender_from_name(speaker)
            if inferred:
                gender = inferred
            else:
                if role in ['agent', 'tutor', 'guide', 'lecturer', 'examiner']:
                    gender = 'male'
                else:
                    gender = 'female'

        # v8 — pool-seeded RNG if available
        rng = self._get_pool_rng(pool_id) if pool_id is not None else None
        pool_tag = f"[pool={pool_id}]" if pool_id is not None else "[session]"

        # ═══════════════════════════════════════════════════════════
        # SECTION 1 & 3 → Deepgram voice
        # ═══════════════════════════════════════════════════════════
        if section_num in (1, 3):
            cache_key = (section_num, f"{role}:{gender}")
            with self._voice_cache_lock:
                if cache_key in self._session_voice_cache:
                    cached = self._session_voice_cache[cache_key]
                    logger.debug(
                        f" [voice-cache] Reusing {cached} for "
                        f"{role}/{gender} (S{section_num})"
                    )
                    return cached

                chosen = self._pick_random_deepgram_voice(role, gender, rng=rng)
                self._session_voice_cache[cache_key] = chosen

            logger.info(
                f" [random-deepgram{pool_tag}] S{section_num} {role}/{gender} → {chosen}"
            )
            return chosen

        # ═══════════════════════════════════════════════════════════
        # SECTION 2 & 4 → Edge TTS voice
        # ═══════════════════════════════════════════════════════════
        if section_num in (2, 4):
            cache_key = (section_num, f"{role}:{gender}")
            with self._voice_cache_lock:
                if cache_key in self._session_voice_cache:
                    cached = self._session_voice_cache[cache_key]
                    logger.debug(
                        f" [voice-cache] Reusing {cached} for "
                        f"{role}/{gender} (S{section_num})"
                    )
                    return cached

                chosen = self._pick_random_edge_voice(role, gender, rng=rng)
                self._session_voice_cache[cache_key] = chosen

            logger.info(
                f" [random-edge{pool_tag}] S{section_num} {role}/{gender} → {chosen}"
            )
            return chosen

        # Intro/outro (section 0) → fixed Edge default
        profile = self.edge_voice_profiles.get('default', {})
        return profile.get(gender, 'en-GB-SoniaNeural')

    def _get_fresh_voice_from_pool(self, gender: str, accent: str, used_voices: Optional[Set[str]] = None) -> str:
        """Legacy helper."""
        return self._pick_random_deepgram_voice('default', gender)

    def _get_edge_loop(self):
        if self._edge_loop is None or self._edge_loop.is_closed():
            self._edge_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._edge_loop)
        return self._edge_loop

    # ============================================================
    # DEEPGRAM AUDIO (Section 1 & 3)
    # ============================================================
    async def _generate_deepgram_audio(self, text, voice, filename):
        filepath = self.cache_dir / filename
        if filepath.exists():
            filepath.unlink()

        if not DEEPGRAM_AVAILABLE or not self._http_session:
            return None

        chunk_size = 2500
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
        if not chunks:
            return None

        async def request_chunk(chunk_text):
            headers = {
                "Authorization": f"Token {DEEPGRAM_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {"text": chunk_text}
            params = {"model": voice, "encoding": "mp3", "bitrate": "64k"}
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, self._deepgram_limit.acquire)
                try:
                    response = await loop.run_in_executor(
                        None,
                        lambda: self._http_session.post(
                            DEEPGRAM_TTS_URL,
                            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                            headers=headers, params=params, timeout=12
                        )
                    )
                finally:
                    self._deepgram_limit.release()
                if response.status_code == 200:
                    return response.content
                return None
            except Exception as e:
                logger.warning(f"Deepgram chunk failed: {e}")
                return None

        async def request_with_retry(chunk_text, max_retries=3):
            for attempt in range(max_retries):
                try:
                    result = await request_chunk(chunk_text)
                    if result and len(result) > 2000:
                        return result
                except Exception:
                    pass
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))
            return None

        chunk_results = await asyncio.gather(*[
            request_with_retry(chunk) for chunk in chunks
        ], return_exceptions=True)

        chunk_data = [r for r in chunk_results if r and not isinstance(r, Exception)]
        if not chunk_data or len(chunk_data) != len(chunks):
            logger.warning(f" Deepgram: some chunks failed for {filename}")
            return None

        temp_path = self.cache_dir / f"{filepath.stem}_dg_temp.mp3"
        try:
            with open(temp_path, 'wb') as f:
                for data in chunk_data:
                    f.write(data)
            if temp_path.exists() and temp_path.stat().st_size > 2000:
                shutil.copy(temp_path, filepath)
                logger.info(f" Deepgram audio: {filename} ({filepath.stat().st_size} bytes, voice={voice})")
                return str(filepath)
            return None
        finally:
            try: os.remove(temp_path)
            except: pass

    # ============================================================
    # EDGE TTS AUDIO (Section 2 & 4)
    # ============================================================
    async def _generate_edge_audio(self, text, voice, filename):
        filepath = self.cache_dir / filename
        if filepath.exists():
            filepath.unlink()

        if not EDGE_AVAILABLE:
            return None

        try:
            await edge_tts.Communicate(text, voice).save(str(filepath))
            if filepath.exists() and filepath.stat().st_size > 2000:
                logger.info(
                    f" Edge audio: {filename} "
                    f"({filepath.stat().st_size} bytes, voice={voice})"
                )
                return str(filepath)
        except Exception as e:
            logger.error(f"Edge TTS failed for {filename}: {e}")
        return None

    # ============================================================
    # MAIN DISPATCH
    # ============================================================
    async def _generate_single_audio(self, text, voice, filename, accent='british',
                                     speaker='default', fast=False, force_edge=False,
                                     section_num: int = 1):
        filepath = self.cache_dir / filename
        if filepath.exists():
            filepath.unlink()

        clean_text = re.sub(
            r'^[A-Za-z][A-Za-z0-9_\- ]*\s*:\s*', '', text, flags=re.IGNORECASE
        ).strip()
        if not clean_text:
            return None, 'failed'

        # SECTION 2 & 4 → EDGE TTS ONLY
        if section_num in (2, 4):
            logger.info(
                f" [edge-only] S{section_num} speaker={speaker} voice={voice}"
            )
            result = await self._generate_edge_audio(clean_text, voice, filename)
            if result:
                return result, 'edge'

            if GTTS_AVAILABLE:
                try:
                    tts = gTTS(clean_text, lang='en', slow=False)
                    tts.save(str(filepath))
                    if filepath.exists() and filepath.stat().st_size > 2000:
                        return str(filepath), 'gtts'
                except Exception:
                    pass
            return None, 'failed'

        # SECTION 1 & 3 → DEEPGRAM (fallback to Edge)
        logger.info(
            f" [deepgram] S{section_num} speaker={speaker} voice={voice}"
        )

        if DEEPGRAM_AVAILABLE:
            result = await self._generate_deepgram_audio(clean_text, voice, filename)
            if result:
                return result, 'deepgram'
            logger.warning(
                f" Deepgram failed for S{section_num} {speaker}, "
                f"falling back to Edge TTS"
            )

        if EDGE_AVAILABLE:
            role = self._normalize_speaker_role(speaker)
            edge_voice = self.edge_voice_profiles.get(role, self.edge_voice_profiles['default'])
            fallback_voice = edge_voice.get('female', 'en-GB-SoniaNeural')
            try:
                await edge_tts.Communicate(clean_text, fallback_voice).save(str(filepath))
                if filepath.exists() and filepath.stat().st_size > 2000:
                    logger.info(f" Edge fallback: {filename}")
                    return str(filepath), 'edge'
            except Exception as e:
                logger.warning(f"Edge fallback failed: {e}")

        if GTTS_AVAILABLE:
            try:
                tts = gTTS(clean_text, lang='en', slow=False)
                tts.save(str(filepath))
                if filepath.exists() and filepath.stat().st_size > 2000:
                    return str(filepath), 'gtts'
            except Exception:
                pass

        return None, 'failed'

    # ============================================================
    # MERGING
    # ============================================================
    @staticmethod
    def _find_mp3_sync(data: bytes, start: int = 0) -> int:
        for i in range(start, len(data) - 1):
            if data[i] == 0xFF and (data[i+1] & 0xF0) == 0xF0:
                return i
        return -1

    async def _merge_audio_files(self, file_paths, output_filename, cleanup=True):
        if not file_paths:
            return None
        output_path = self.cache_dir / output_filename

        if len(file_paths) == 1:
            shutil.copy(file_paths[0], output_path)
            if cleanup:
                self._cleanup_temp_files(file_paths, keep=[str(output_path)])
            return str(output_path)

        merged = bytearray()
        valid_paths = []
        for idx, path in enumerate(file_paths):
            if not os.path.exists(path):
                continue
            with open(path, 'rb') as f:
                data = f.read()
            if not data:
                continue
            valid_paths.append(path)
            sync_pos = self._find_mp3_sync(data)
            if sync_pos != -1:
                merged.extend(data[sync_pos:])
            else:
                merged.extend(data)

        if not merged:
            return None

        with open(output_path, 'wb') as f:
            f.write(merged)

        if output_path.exists() and output_path.stat().st_size > 1000:
            if cleanup:
                self._cleanup_temp_files(valid_paths, keep=[str(output_path)])
            return str(output_path)
        return None

    # ============================================================
    # MAIN: generate_merged_audio
    # ============================================================
    async def generate_merged_audio(
        self,
        script: str,
        section_num: int = 1,
        test_title: str = "listening_test",
        total_questions: int = 10,
        include_instructions: bool = True,
        accent: str = 'british',
        speaker_voice_map: Optional[Dict[str, str]] = None,
        speaker_engine_map: Optional[Dict[str, str]] = None,
        speaker_genders: Optional[Dict[str, str]] = None,
        fast: bool = False,
        pool_id: Optional[int] = None, # v8
    ) -> Tuple[Optional[Dict[str, str]], Optional[str], dict]:
        """
        Generate (or reuse) audio for one listening section.

         v8 — if pool_id is provided:
          • Filename = `{CACHE_VERSION}_section_{N}_pool_{pool_id}_{accent}_main.mp3`
          • Cache check FIRST → return existing file if present
          • Voices seeded by pool_id → deterministic
        """

        # ─────────────────────────────────────────────────────
        # v8 — POOL CACHE HIT CHECK
        # ─────────────────────────────────────────────────────
        if pool_id is not None:
            cached_path, cached_filename = self._get_cached_pool_audio_path(
                pool_id, section_num, accent
            )
            if cached_path.exists() and cached_path.stat().st_size > 50000:
                logger.info(
                    f" [pool-cache HIT] S{section_num} pool={pool_id} "
                    f"→ {cached_filename} "
                    f"({cached_path.stat().st_size} bytes) — skipping TTS"
                )
                intro_url = await self._get_cached_intro_audio(section_num, accent)
                outro_url = await self._get_cached_outro_audio(section_num, accent)

                intro_duration = self._get_mp3_duration(self.cache_dir / os.path.basename(intro_url)) if intro_url else 3.0
                if intro_duration <= 0: intro_duration = 3.0
                outro_duration = self._get_mp3_duration(self.cache_dir / os.path.basename(outro_url)) if outro_url else 2.0
                if outro_duration <= 0: outro_duration = 2.0
                main_duration = self._get_mp3_duration(cached_path)
                if main_duration <= 0: main_duration = 30.0

                timings = {
                    'intro': [0, intro_duration],
                    'reading': [intro_duration, intro_duration + 30.0],
                    'main_audio': [intro_duration + 30.0, intro_duration + 30.0 + main_duration],
                    'outro': [intro_duration + 30.0 + main_duration,
                              intro_duration + 30.0 + main_duration + outro_duration],
                    'checking': [intro_duration + 30.0 + main_duration + outro_duration,
                                 intro_duration + 30.0 + main_duration + outro_duration + 30.0]
                }
                urls = {
                    'intro': intro_url,
                    'main': f"/audio_cache/{cached_filename}",
                    'outro': outro_url,
                }
                return urls, None, timings

        # ─────────────────────────────────────────────────────
        # Normal generation path
        # ─────────────────────────────────────────────────────
        norm_genders: Dict[str, str] = {}
        if speaker_genders:
            for k, v in speaker_genders.items():
                norm_genders[self._normalize_speaker(k)] = (v or '').lower()
            logger.info(f" Gender map (normalized): {norm_genders}")

        intro_url = await self._get_cached_intro_audio(section_num, accent)
        outro_url = await self._get_cached_outro_audio(section_num, accent)

        turns = self._parse_script(script, section_num)
        if len(turns) <= 1 and section_num in (1, 3):
            turns = self._force_split_dialogue(script, section_num)
            turns = self._merge_consecutive_turns(turns)

        logger.info(f" Section {section_num}: {len(turns)} turns to generate (after merge)")

        # ─────────────────────────────────────────────────────
        # v8 — Filename strategy:
        # • pool_id present → deterministic cache filename
        # • pool_id absent → legacy unique filename
        # ─────────────────────────────────────────────────────
        if pool_id is not None:
            _, merged_filename = self._get_cached_pool_audio_path(
                pool_id, section_num, accent
            )
            # Temp files still get unique prefix to avoid collision during parallel runs
            base_name = f"tmp_{CACHE_VERSION}_pool{pool_id}_sec{section_num}_{uuid.uuid4().hex[:6]}"
            pool_tag = f" [pool={pool_id}]"
        else:
            timestamp = int(time.time())
            script_hash = hashlib.md5(script.encode('utf-8')).hexdigest()[:12]
            safe_title = re.sub(r'[^a-zA-Z0-9_-]', '_', test_title[:30])
            unique_id = uuid.uuid4().hex[:6]
            base_name = f"{safe_title}_sec{section_num}_{script_hash}_{timestamp}_{unique_id}"
            merged_filename = f"{base_name}_main.mp3"
            pool_tag = ""

        section_overrides = speaker_voice_map.copy() if speaker_voice_map else {}

        # ---- SECTION 1: Random (or pool-seeded) Deepgram voices per speaker ----
        if section_num == 1 and turns:
            unique_speakers = []
            for speaker, _ in turns:
                if speaker not in unique_speakers:
                    unique_speakers.append(speaker)
            if len(unique_speakers) >= 2:
                speaker_1 = unique_speakers[0]
                speaker_2 = unique_speakers[1]

                g1 = norm_genders.get(speaker_1)
                g2 = norm_genders.get(speaker_2)
                if not g1:
                    g1 = norm_genders.get('agent') if 'agent' in speaker_1 else norm_genders.get('customer')
                if not g2:
                    g2 = norm_genders.get('customer') if 'customer' in speaker_2 else norm_genders.get('agent')

                has_agent = 'agent' in unique_speakers
                if not has_agent:
                    section_overrides[speaker_1] = self._get_voice_for_speaker(
                        'agent', accent, forced_gender=g1,
                        section_num=section_num, pool_id=pool_id
                    )
                    section_overrides[speaker_2] = self._get_voice_for_speaker(
                        'customer', accent, forced_gender=g2,
                        section_num=section_num, pool_id=pool_id
                    )
                else:
                    section_overrides[speaker_1] = self._get_voice_for_speaker(
                        speaker_1, accent, forced_gender=g1,
                        section_num=section_num, pool_id=pool_id
                    )
                    section_overrides[speaker_2] = self._get_voice_for_speaker(
                        speaker_2, accent, forced_gender=g2,
                        section_num=section_num, pool_id=pool_id
                    )

                logger.info(
                    f" Section 1 voices{pool_tag}: "
                    f"{speaker_1}={section_overrides[speaker_1]} ({g1 or 'auto'}), "
                    f"{speaker_2}={section_overrides[speaker_2]} ({g2 or 'auto'})"
                )

        # ---- SECTION 3: Random (or pool-seeded) Deepgram voices per speaker ----
        if section_num == 3:
            for speaker, _ in turns:
                if speaker not in section_overrides:
                    g = norm_genders.get(speaker)
                    section_overrides[speaker] = self._get_voice_for_speaker(
                        speaker, accent, forced_gender=g,
                        section_num=section_num, pool_id=pool_id
                    )
            logger.info(f" Section 3 voices{pool_tag}: {section_overrides}")

        # ---- Generate all turns in parallel ----
        async def generate_one_turn(idx: int, speaker: str, text: str):
            if speaker in section_overrides:
                voice = section_overrides[speaker]
            else:
                g = norm_genders.get(speaker)
                voice = self._get_voice_for_speaker(
                    speaker, accent, forced_gender=g,
                    section_num=section_num, pool_id=pool_id
                )

            filename = f"{base_name}_turn{idx+1}.mp3"
            audio_path, used_engine = await self._generate_single_audio(
                text, voice, filename, accent, speaker=speaker,
                fast=fast, force_edge=False, section_num=section_num
            )
            return audio_path

        t0 = time.time()
        tasks = [generate_one_turn(idx, sp, tx) for idx, (sp, tx) in enumerate(turns)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = time.time() - t0
        logger.info(f" Section {section_num}: {len(turns)} turns generated in {elapsed:.1f}s (avg {elapsed/max(1,len(turns)):.2f}s/turn)")

        audio_files = []
        seen_files = set()
        for res in results:
            if isinstance(res, Exception):
                logger.warning(f"Turn generation failed: {res}")
                continue
            if res and res not in seen_files:
                audio_files.append(res)
                seen_files.add(res)

        if not audio_files:
            return None, "No audio files generated", {}

        merged_path = await self._merge_audio_files(audio_files, merged_filename, cleanup=True)

        if not merged_path or not os.path.exists(merged_path):
            return None, "Merge failed", {}

        file_size = os.path.getsize(merged_path)
        if file_size < 50000:
            return None, "Merged file too small", {}

        main_url = f"/audio_cache/{os.path.basename(merged_path)}"
        logger.info(f" Main audio saved{pool_tag}: {main_url} ({file_size} bytes)")

        intro_duration = self._get_mp3_duration(self.cache_dir / os.path.basename(intro_url)) if intro_url else 0
        if intro_duration <= 0:
            intro_duration = 3.0

        outro_duration = self._get_mp3_duration(self.cache_dir / os.path.basename(outro_url)) if outro_url else 0
        if outro_duration <= 0:
            outro_duration = 2.0

        main_duration = self._get_mp3_duration(Path(merged_path))
        if main_duration <= 0:
            main_duration = 30.0

        timings = {
            'intro': [0, intro_duration],
            'reading': [intro_duration, intro_duration + 30.0],
            'main_audio': [intro_duration + 30.0, intro_duration + 30.0 + main_duration],
            'outro': [intro_duration + 30.0 + main_duration,
                      intro_duration + 30.0 + main_duration + outro_duration],
            'checking': [intro_duration + 30.0 + main_duration + outro_duration,
                         intro_duration + 30.0 + main_duration + outro_duration + 30.0]
        }

        urls = {'intro': intro_url, 'main': main_url, 'outro': outro_url}
        return urls, None, timings

    # ============================================================
    # SYNCHRONOUS WRAPPER
    # ============================================================
    def generate(
        self,
        script: str,
        section_number: int = 1,
        test_title: str = "listening_test",
        accent: str = "british",
        total_questions: int = 10,
        include_instructions: bool = True,
        speaker_voice_map: Optional[Dict[str, str]] = None,
        speaker_engine_map: Optional[Dict[str, str]] = None,
        speaker_genders: Optional[Dict[str, str]] = None,
        fast: bool = False,
        pool_id: Optional[int] = None, # v8
    ) -> Tuple[Optional[Dict[str, str]], Optional[str], dict]:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                urls, error, timings = loop.run_until_complete(
                    self.generate_merged_audio(
                        script,
                        section_number,
                        test_title,
                        total_questions,
                        include_instructions,
                        accent,
                        speaker_voice_map,
                        speaker_engine_map,
                        speaker_genders,
                        fast,
                        pool_id=pool_id,
                    )
                )
            finally:
                loop.close()
            if urls and urls.get('main'):
                return urls, None, timings
            else:
                return None, error or "Audio generation failed", {}
        except Exception as e:
            logger.error(f"Generation error: {e}", exc_info=True)
            return None, str(e), {}

    def generate_sync(
        self,
        script: str,
        section_number: int = 1,
        test_title: str = "listening_test",
        accent: str = "british",
        total_questions: int = 10,
        include_instructions: bool = True,
        speaker_voice_map: Optional[Dict[str, str]] = None,
        speaker_engine_map: Optional[Dict[str, str]] = None,
        speaker_genders: Optional[Dict[str, str]] = None,
        fast: bool = False,
        pool_id: Optional[int] = None, # v8
    ) -> Tuple[Optional[Dict[str, str]], Optional[str], dict]:
        return self.generate(
            script, section_number, test_title, accent,
            total_questions, include_instructions,
            speaker_voice_map, speaker_engine_map,
            speaker_genders,
            fast,
            pool_id=pool_id,
        )


# ============================================================
# SINGLETON INSTANCE
# ============================================================
logger.info("Creating audio_generator instance...")
audio_generator = AudioGenerator()
logger.info(" audio_generator instance created successfully.")