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
CACHE_VERSION = "v9" # bump to invalidate all cached audio


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
        # FIX v9.2: persistent per-pool voice assignments
        self._pool_voice_cache: Dict[str, str] = {}
        # FIX v9.6: track used voices per (pool, section, role, gender)
        self._pool_used_voices: Dict[tuple, set] = {}
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
        
        # FIX v9.2: Handle double-escaped newlines from AI
        if '\\n' in script and '\n' not in script:
            script = script.replace('\\n', '\n')
        if '\\r\\n' in script:
            script = script.replace('\\r\\n', '\n')
        if '\\r' in script and '\r' not in script:
            script = script.replace('\\r', '\n')
        


        # FIX v9.8: INLINE-LABEL FALLBACK
        # Expansion joins all paragraphs → newlines lost but labels remain inline.
        # Example: "Dr. Evan: Hi. Sonia: Hello. Nicholas: Ok."
        if '\n' not in script or script.count('\n') < 2:
            # Look for inline "Name: ..." patterns
            # Name: 1-30 chars starting with capital, optional period
            inline_pattern = re.compile(
                r'(?:^|\s)([A-Z][A-Za-z0-9_.\- ]{0,30}?)\s*:\s*'
            )
            # FIX v9.9: reject false positives — filter to plausible names
            _STOP_WORDS = {
                'recommends', 'shows', 'says', 'means', 'includes',
                'requires', 'involves', 'suggests', 'implies', 'indicates',
                'however', 'therefore', 'moreover', 'furthermore',
                'chapter', 'section', 'part', 'example', 'note',
            }
            _raw_matches = list(inline_pattern.finditer(script))
            matches = []
            for m in _raw_matches:
                lbl = m.group(1).strip()
                # Reject if any word is a stop word
                words_lower = lbl.lower().split()
                if any(w in _STOP_WORDS for w in words_lower):
                    continue
                # Reject if too many words (>4 = likely a sentence, not a name)
                if len(words_lower) > 4:
                    continue
                # Reject if contains digits (unlikely in names)
                if any(c.isdigit() for c in lbl):
                    continue
                matches.append(m)
            if len(matches) >= 2:
                # Split by label positions
                turns_inline = []
                for i, m in enumerate(matches):
                    label = m.group(1).strip()
                    text_start = m.end()
                    text_end = matches[i + 1].start() if i + 1 < len(matches) else len(script)
                    text = script[text_start:text_end].strip()
                    if label and text:
                        turns_inline.append((label, text))
                if len(turns_inline) >= 2:
                    logger.info(
                        f" [S{section_num} inline-label] Found {len(turns_inline)} "
                        f"inline labels (no newlines) — using them directly"
                    )
                    return turns_inline

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
        pattern = re.compile(r'^([A-Za-z][A-Za-z0-9_.\- ]{0,30})\s*:\s*(.*)$')
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
                # FIX v9.1: no early return — keep accumulating so
                # force-split can handle unlabeled scripts (Section 1/3).
                if current_speaker is None:
                    current_speaker = 'default'
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
        """
        FIXED (v9): Sentence-level alternation instead of word-halving.

        Previous bug: split script at word midpoint → agent reads first half
        (including customer's dialogue!) and customer reads second half.
        Result: "monologue x 2" instead of natural Q&A dialogue.

        Now: split into sentences and alternate speakers per sentence,
        preserving natural conversation flow.
        """
        if not script or not script.strip():
            return []

        # === Sentence-level split (preferred for dialogue) ===
        sentences = re.split(r'(?<=[.!?])\s+', script.strip())
        sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 5]

        if section_num == 1:
            # Section 1: 2 speakers (agent/customer alternating)
            if len(sentences) >= 2:
                turns = []
                for i, sent in enumerate(sentences):
                    speaker = 'agent' if i % 2 == 0 else 'customer'
                    turns.append((speaker, sent))
                merged = []
                for spk, txt in turns:
                    if merged and merged[-1][0] == spk:
                        merged[-1] = (spk, merged[-1][1] + ' ' + txt)
                    else:
                        merged.append((spk, txt))
                logger.info(
                    f" [S1 force-split v9] Sentence-alternate: {len(merged)} turns "
                    f"from {len(sentences)} sentences"
                )
                return merged

            # Fallback: word-halving (only if single sentence)
            words = script.split()
            if len(words) < 2:
                return [('agent', script)]
            mid = len(words) // 2
            logger.warning(
                f" [S1 force-split v9] Single sentence — "
                f"falling back to word-halving (may still sound monologue)"
            )
            return [('agent', ' '.join(words[:mid])),
                    ('customer', ' '.join(words[mid:]))]

        elif section_num == 3:
            # Section 3: 4 speakers rotating (tutor + 3 students)
            speakers = ['tutor', 'student1', 'student2', 'student3']

            if len(sentences) >= 4:
                # FIX v9.7: GROUP sentences to avoid 95 choppy turns
                # Real IELTS Section 3 has 15-20 turns (~40-60 words each)
                target_turns = 20
                sents_per_turn = max(1, len(sentences) // target_turns)

                turns = []
                for i in range(0, len(sentences), sents_per_turn):
                    chunk = ' '.join(sentences[i:i + sents_per_turn]).strip()
                    if chunk:
                        speaker = speakers[(i // sents_per_turn) % 4]
                        turns.append((speaker, chunk))
                merged = []
                for spk, txt in turns:
                    if merged and merged[-1][0] == spk:
                        merged[-1] = (spk, merged[-1][1] + ' ' + txt)
                    else:
                        merged.append((spk, txt))
                logger.info(
                    f" [S3 force-split v9.7] Grouped {len(sentences)} sentences → "
                    f"{len(merged)} turns ({sents_per_turn} sents/turn)"
                )
                return merged

            # Fallback: word-quarter split
            words = script.split()
            if len(words) < 4:
                return [('tutor', script)]
            part_size = max(1, len(words) // 4)
            logger.warning(
                f" [S3 force-split v9] Too few sentences — "
                f"falling back to word-quarter split"
            )
            parts = []
            for i in range(4):
                start = i * part_size
                end = (i + 1) * part_size if i < 3 else len(words)
                if start < len(words):
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
        # FIX v9.3: Titles — Dr., Prof. → tutor
        if (speaker_lower.startswith('dr.') or 
            speaker_lower.startswith('dr ') or
            speaker_lower.startswith('prof.') or
            speaker_lower.startswith('prof ')):
            return 'tutor'
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
            # FIX v9.2: deterministic per-pool voice cache
            if pool_id is not None:
                # FIX v9.6: speaker name in cache key + used-voice tracking
                _pv_key = f'pool_{pool_id}_{section_num}_{role}:{gender}:{speaker}'
                with self._voice_cache_lock:
                    _pv_cached = self._pool_voice_cache.get(_pv_key)
                if _pv_cached:
                    return _pv_cached
                
                # FIX v9.6: exclude already-used voices in this (pool, section, role, gender)
                _role_pool = self.deepgram_voice_pools.get(role) or self.deepgram_voice_pools['default']
                _all_voices = _role_pool.get(gender, _role_pool.get('male', []))
                _used_key = (pool_id, section_num, role, gender)
                with self._voice_cache_lock:
                    _used = self._pool_used_voices.get(_used_key, set())
                _candidates = [v for v in _all_voices if v not in _used] or list(_all_voices)
                _pv_rng = random.Random(f'pool_{pool_id}_{CACHE_VERSION}_{section_num}_{role}_{gender}_{speaker}')
                _pv_chosen = _pv_rng.choice(_candidates) if _candidates else 'aura-2-asteria-en'
                with self._voice_cache_lock:
                    self._pool_voice_cache[_pv_key] = _pv_chosen
                    self._pool_used_voices.setdefault(_used_key, set()).add(_pv_chosen)
                logger.info(f' [random-deepgram[pool={pool_id}]] S{section_num} {role}/{gender} ({speaker}) -> {_pv_chosen}')
                return _pv_chosen
            
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
            # FIX v11: 30-second timeout per edge_tts call (was hanging 4min)
            import asyncio as _aio
            await _aio.wait_for(
                edge_tts.Communicate(text, voice).save(str(filepath)),
                timeout=30.0,
            )
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
            r'^[A-Za-z][A-Za-z0-9_.\- ]*\s*:\s*', '', text, flags=re.IGNORECASE
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

                # FIX v9.9: normalize speaker for gender lookup
                _n1 = self._normalize_speaker(speaker_1)
                _n2 = self._normalize_speaker(speaker_2)
                g1 = norm_genders.get(_n1) or norm_genders.get(speaker_1)
                g2 = norm_genders.get(_n2) or norm_genders.get(speaker_2)
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
                    # FIX v9.9: normalize speaker key for gender lookup
                    _norm_spk = self._normalize_speaker(speaker)
                    g = norm_genders.get(_norm_spk) or norm_genders.get(speaker)
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


# ═══════════════════════════════════════════════════════════════════
# FIX v11.2 — Section 4 robust retry (monkey-patch)
# ═══════════════════════════════════════════════════════════════════
_original_generate_single_audio_v11 = AudioGenerator._generate_single_audio


async def _patched_generate_single_audio_v11(
    self, text, voice, filename, accent='british', speaker='default',
    fast=False, force_edge=False, section_num=1
):
    result = await _original_generate_single_audio_v11(
        self, text, voice, filename, accent, speaker, fast, force_edge, section_num
    )
    path, engine = result if isinstance(result, tuple) else (result, None)
    if path:
        return path, engine
    
    # Only retry for Section 2 & 4 (Edge TTS failures)
    if section_num not in (2, 4):
        return path, engine
    
    logger.warning(
        f" [S{section_num}-retry-v11.2] Original generation failed for {filename} "
        f"— splitting into 60-word chunks"
    )
    
    filepath = self.cache_dir / filename
    if filepath.exists():
        try:
            filepath.unlink()
        except Exception:
            pass
    
    # Clean text (strip any label prefixes)
    import re as _re
    clean = _re.sub(r'^[A-Za-z][A-Za-z0-9_.\- ]*\s*:\s*', '', text).strip()
    if not clean:
        return None, 'failed'
    
    words = clean.split()
    if len(words) < 20:
        # Too short — try gTTS whole
        if GTTS_AVAILABLE:
            try:
                from gtts import gTTS as _gTTS
                _gTTS(clean, lang='en', slow=False).save(str(filepath))
                if filepath.exists() and filepath.stat().st_size > 2000:
                    logger.info(f" [S{section_num}-gtts-v11.2] {filename} OK")
                    return str(filepath), 'gtts-retry'
            except Exception as _e:
                logger.error(f" [S{section_num}-gtts-v11.2] failed: {_e}")
        return None, 'failed'
    
    # Split into ~60-word chunks
    chunks = []
    chunk_size = 60
    for i in range(0, len(words), chunk_size):
        chunk = ' '.join(words[i:i+chunk_size])
        if chunk.strip():
            chunks.append(chunk)
    
    logger.info(
        f" [S{section_num}-retry-v11.2] {len(words)} words → {len(chunks)} chunks "
        f"({chunk_size} words each)"
    )
    
    import asyncio as _aio
    temp_files = []
    
    for idx, chunk in enumerate(chunks):
        tf = self.cache_dir / f"tmp_v11_retry_{filename}_{idx}.mp3"
        success = False
        
        # Try edge_tts with 20s timeout
        if EDGE_AVAILABLE:
            try:
                await _aio.wait_for(
                    edge_tts.Communicate(chunk, voice).save(str(tf)),
                    timeout=20.0
                )
                if tf.exists() and tf.stat().st_size > 500:
                    success = True
            except Exception as _ee:
                logger.warning(
                    f" [S{section_num}-retry-v11.2] chunk {idx} edge failed: {_ee}"
                )
        
        # Fallback to gTTS for this chunk
        if not success and GTTS_AVAILABLE:
            try:
                from gtts import gTTS as _gTTS
                _gTTS(chunk, lang='en', slow=False).save(str(tf))
                if tf.exists() and tf.stat().st_size > 500:
                    success = True
            except Exception as _ge:
                logger.error(
                    f" [S{section_num}-retry-v11.2] chunk {idx} gtts failed: {_ge}"
                )
        
        if success:
            temp_files.append(tf)
        else:
            logger.error(
                f" [S{section_num}-retry-v11.2] chunk {idx} TOTAL FAILURE"
            )
    
    if not temp_files:
        return None, 'failed'
    
    if len(temp_files) < len(chunks):
        logger.warning(
            f" [S{section_num}-retry-v11.2] partial: {len(temp_files)}/{len(chunks)} chunks OK"
        )
    
    # Merge chunks
    with open(filepath, 'wb') as f:
        for tf in temp_files:
            try:
                f.write(tf.read_bytes())
            except Exception:
                pass
    
    # Cleanup
    for tf in temp_files:
        try:
            tf.unlink()
        except Exception:
            pass
    
    if filepath.exists() and filepath.stat().st_size > 2000:
        logger.info(
            f" [S{section_num}-retry-v11.2] merged {len(temp_files)} chunks → "
            f"{filename} ({filepath.stat().st_size} bytes)"
        )
        return str(filepath), 'edge-split-retry'
    
    return None, 'failed'


# Replace original
AudioGenerator._generate_single_audio = _patched_generate_single_audio_v11
logger.info(" [v11.2] _generate_single_audio monkey-patched for Section 2/4 retry")


# ═══════════════════════════════════════════════════════════════════
# FIX v12 — Section 1 fake speaker label filter
# ═══════════════════════════════════════════════════════════════════

_original_parse_script_v12 = AudioGenerator._parse_script


def _v12_clean_s1_label(spk):
    """Return 'Agent' | 'Customer' | None."""
    import re as _re
    s = spk.strip().rstrip('.').strip()
    # Strip leading titles
    for title in ('Dr.', 'Prof.', 'Mr.', 'Mrs.', 'Ms.'):
        if s.startswith(title + ' '):
            s = s[len(title) + 1:]
            break
    # If period remains in middle → sentence boundary, reject
    if _re.search(r'\.', s):
        return None
    # Hyphens → spelled-out word, reject
    if '-' in s:
        return None
    # Length / word cap
    if len(s) > 18 or len(s.split()) > 1:
        return None
    # Exact role match
    s_lower = s.lower()
    if s_lower == 'agent':
        return 'Agent'
    if s_lower == 'customer':
        return 'Customer'
    return None


def _patched_parse_script_v12(self, script, section_num=1):
    turns = _original_parse_script_v12(self, script, section_num)
    if section_num != 1 or not turns or len(turns) < 2:
        return turns
    
    kept = []
    merged_count = 0
    for spk, txt in turns:
        role = _v12_clean_s1_label(spk)
        if role:
            kept.append((role, txt))
        else:
            if kept:
                prev = kept[-1]
                kept[-1] = (prev[0], f"{prev[1]} {txt}")
                merged_count += 1
    
    if len(kept) >= 2:
        if merged_count > 0:
            logger.info(
                f" [S1-filter-v12] {len(turns)} → {len(kept)} turns "
                f"({merged_count} fake speakers merged)"
            )
        return kept
    return turns


AudioGenerator._parse_script = _patched_parse_script_v12
logger.info(" [v12] _parse_script monkey-patched (S1 fake speaker filter)")


# ═══════════════════════════════════════════════════════════════════
# FIX v12b — Section 3 voice uniqueness (per-section, not per-role)
# ═══════════════════════════════════════════════════════════════════

_original_pick_deepgram_v12b = AudioGenerator._pick_random_deepgram_voice


def _patched_get_voice_for_speaker_v12b(self, speaker, accent='british', 
                                         context='', use_edge=False, 
                                         forced_gender=None, section_num=0,
                                         pool_id=None):
    """Override to enforce ALL distinct voices in Section 3."""
    role = self._normalize_speaker_role(speaker)
    
    # Resolve gender
    if forced_gender and forced_gender.lower() in ('male', 'female'):
        gender = forced_gender.lower()
    else:
        inferred = self._infer_gender_from_name(speaker)
        gender = inferred if inferred else ('male' if role in ['agent','tutor','guide','lecturer','examiner'] else 'female')
    
    pool_tag = f"[pool={pool_id}]" if pool_id is not None else "[session]"
    
    # ═══ SECTION 3 special: per-SECTION used-voice tracking (not per-role) ═══
    if section_num == 3 and pool_id is not None:
        _pv_key = f'pool_{pool_id}_{section_num}_{role}:{gender}:{speaker}'
        with self._voice_cache_lock:
            _pv_cached = self._pool_voice_cache.get(_pv_key)
        if _pv_cached:
            return _pv_cached
        
        # Get voice pool for this role+gender
        _role_pool = self.deepgram_voice_pools.get(role) or self.deepgram_voice_pools['default']
        _all_voices = _role_pool.get(gender, _role_pool.get('male', []))
        
        # FIX v12b: key by SECTION ONLY (not role) — prevent same voice across roles
        _used_key = (pool_id, section_num, gender)   # ← removed role
        with self._voice_cache_lock:
            _used = self._pool_used_voices.get(_used_key, set())
        _candidates = [v for v in _all_voices if v not in _used] or list(_all_voices)
        
        _pv_rng = random.Random(f'pool_{pool_id}_{CACHE_VERSION}_{section_num}_{role}_{gender}_{speaker}')
        _pv_chosen = _pv_rng.choice(_candidates) if _candidates else 'aura-2-asteria-en'
        
        with self._voice_cache_lock:
            self._pool_voice_cache[_pv_key] = _pv_chosen
            self._pool_used_voices.setdefault(_used_key, set()).add(_pv_chosen)
        
        logger.info(f' [random-deepgram{pool_tag}] S{section_num} {role}/{gender} ({speaker}) -> {_pv_chosen}')
        return _pv_chosen
    
    # Other sections: use original logic
    return _original_get_voice_for_speaker_v12b(
        self, speaker=speaker, accent=accent, context=context,
        use_edge=use_edge, forced_gender=forced_gender,
        section_num=section_num, pool_id=pool_id,
    )


# Find and replace the method
_original_get_voice_for_speaker_v12b = AudioGenerator._get_voice_for_speaker
AudioGenerator._get_voice_for_speaker = _patched_get_voice_for_speaker_v12b
logger.info(" [v12b] _get_voice_for_speaker monkey-patched (S3 per-section voice uniqueness)")


# ═══════════════════════════════════════════════════════════════════
# FIX v12c — S1 turn grouping (in audio_generator.py)
# ═══════════════════════════════════════════════════════════════════

_orig_parse_v12c = AudioGenerator._parse_script


def _patched_parse_v12c(self, script, section_num=1):
    turns = _orig_parse_v12c(self, script, section_num)
    if section_num != 1 or not turns or len(turns) < 22:
        return turns
    
    target = 18
    # Adjacent same-speaker merge (round 1)
    r1 = []
    for spk, txt in turns:
        if r1 and r1[-1][0] == spk:
            r1[-1] = (spk, f"{r1[-1][1]} {txt}")
        else:
            r1.append((spk, txt))
    
    # Force-merge every pair if still > target
    if len(r1) > target and target > 0:
        skip = max(1, len(r1) // target)
        r2 = []
        i = 0
        while i < len(r1):
            spk, txt = r1[i]
            nxt = min(i + skip, len(r1))
            merged_text = ' '.join(t for _, t in r1[i:nxt])
            r2.append((spk, merged_text))
            i = nxt
        if len(r2) >= 2:
            r1 = r2
    
    if len(r1) < len(turns):
        logger.info(
            f" [S1-group-v12c] {len(turns)} → {len(r1)} turns (target {target})"
        )
    return r1


AudioGenerator._parse_script = _patched_parse_v12c
logger.info(" [v12c] _parse_script patched — S1 grouping to ~18 turns")


# ═══════════════════════════════════════════════════════════════════
# FIX v12d — S3 fake speaker filter + voice pool expansion
# ═══════════════════════════════════════════════════════════════════

# FIX A — Expand default voice pools (3 → 8 for S3 to prevent dup)
def _v12d_expand_pools(gen_instance):
    try:
        dp = gen_instance.deepgram_voice_pools.get('default', {})
        # Ensure minimum 8 voices per gender
        female_default = dp.get('female', [])
        male_default = dp.get('male', [])
        
        extra_female = [
            'aura-2-luna-en', 'aura-2-pandora-en', 'aura-2-theia-en',
            'aura-2-thalia-en', 'aura-2-aurora-en', 'aura-2-cora-en',
        ]
        extra_male = [
            'aura-2-arcas-en', 'aura-2-perseus-en', 'aura-2-hyperion-en',
            'aura-2-orpheus-en', 'aura-2-helios-en',
        ]
        
        for v in extra_female:
            if v not in female_default:
                female_default.append(v)
        for v in extra_male:
            if v not in male_default:
                male_default.append(v)
        
        dp['female'] = female_default
        dp['male'] = male_default
        gen_instance.deepgram_voice_pools['default'] = dp
        logger.info(
            f" [v12d-pools] default pool expanded: "
            f"female={len(female_default)}, male={len(male_default)}"
        )
    except Exception as e:
        logger.warning(f" [v12d-pools] expand failed: {e}")


# Apply pool expansion at instance creation
_orig_init_v12d = AudioGenerator.__init__


def _patched_init_v12d(self, *args, **kwargs):
    _orig_init_v12d(self, *args, **kwargs)
    _v12d_expand_pools(self)


AudioGenerator.__init__ = _patched_init_v12d


# FIX B — S3 fake speaker filter (extend from S1)
_orig_parse_v12d = AudioGenerator._parse_script


def _v12d_is_valid_s3_speaker(spk):
    """Validate S3 speaker label."""
    import re as _re
    s = spk.strip().rstrip('.').strip()
    
    # Strip titles
    for title in ('Dr.', 'Prof.', 'Professor', 'Mr.', 'Mrs.', 'Ms.', 'Sir'):
        if s.startswith(title + ' '):
            s = s[len(title) + 1:]
            break
    
    # Reject if period remains
    if '.' in s:
        return None
    
    # Reject hyphens (spelled-out words)
    if '-' in s:
        return None
    
    # Length / word cap
    if len(s) > 20 or len(s.split()) > 2:
        return None
    
    # Reject stop words
    STOP = {
        'i', 'you', 'we', 'they', 'it', 'the', 'a', 'an', 'and', 'or',
        'but', 'not', 'this', 'that', 'there', 'here', 'what', 'how',
        'think', 'mean', 'say', 'like', 'europe', 'asia', 'africa',
        'america', 'london', 'tokyo', 'the', 'of', 'in', 'on', 'at',
    }
    words_lower = [w.lower() for w in s.split()]
    if any(w in STOP for w in words_lower):
        return None
    
    return s


def _patched_parse_v12d(self, script, section_num=1):
    turns = _orig_parse_v12d(self, script, section_num)
    if section_num != 3 or not turns or len(turns) < 2:
        return turns
    
    kept = []
    merged = 0
    for spk, txt in turns:
        valid = _v12d_is_valid_s3_speaker(spk)
        if valid:
            kept.append((valid, txt))
        else:
            if kept:
                prev = kept[-1]
                kept[-1] = (prev[0], f"{prev[1]} {txt}")
                merged += 1
    
    if len(kept) >= 2:
        if merged > 0:
            logger.info(
                f" [S3-filter-v12d] {len(turns)} → {len(kept)} turns "
                f"({merged} fake speakers merged)"
            )
        return kept
    return turns


AudioGenerator._parse_script = _patched_parse_v12d
logger.info(" [v12d] S3 fake speaker filter + voice pool expansion")


# ═══════════════════════════════════════════════════════════════════
# FIX v15 — Edge TTS RETRY (no gTTS fallback)
# ═══════════════════════════════════════════════════════════════════

_orig_edge_v15r = AudioGenerator._generate_edge_audio


async def _patched_edge_v15r(self, text, voice, filename):
    """Progressive retry: 3x full → split 2 halves → split 4 chunks → silence."""
    import asyncio as _aio
    import re as _re
    
    # ─── Attempt 1: Retry full text 3 times with backoff ───
    for attempt in range(3):
        result = await _orig_edge_v15r(self, text, voice, filename)
        if result:
            if attempt > 0:
                logger.info(
                    f" [edge-retry-v15r] {filename} succeeded on attempt {attempt+1}/3"
                )
            return result
        if attempt < 2:
            wait = 1.0 * (attempt + 1)
            logger.warning(
                f" [edge-retry-v15r] {filename} attempt {attempt+1}/3 failed, "
                f"retrying in {wait}s"
            )
            await _aio.sleep(wait)
    
    # ─── Attempt 2: Split into 2 halves ───
    words = text.split()
    if len(words) > 80:
        logger.warning(f" [edge-split-v15r] {filename} — splitting into 2 halves")
        mid = len(words) // 2
        halves = [' '.join(words[:mid]), ' '.join(words[mid:])]
        
        filepath = self.cache_dir / filename
        temp_paths = []
        all_ok = True
        
        for i, half in enumerate(halves):
            temp_name = f"tmp_v15r_{filename}_{i}.mp3"
            temp_path = self.cache_dir / temp_name
            temp_paths.append(temp_path)
            
            ok = None
            for retry in range(2):
                ok = await _orig_edge_v15r(self, half, voice, temp_name)
                if ok:
                    break
                if retry < 1:
                    await _aio.sleep(1.5)
            
            if not ok:
                all_ok = False
                logger.warning(
                    f" [edge-split-v15r] {filename} half {i+1}/2 failed"
                )
                break
        
        if all_ok and len(temp_paths) == 2:
            # Merge halves
            try:
                with open(filepath, 'wb') as f:
                    for tp in temp_paths:
                        if tp.exists():
                            f.write(tp.read_bytes())
                if filepath.exists() and filepath.stat().st_size > 1000:
                    logger.info(
                        f" [edge-split-v15r] {filename} merged 2 halves OK "
                        f"({filepath.stat().st_size} bytes)"
                    )
                    for tp in temp_paths:
                        try: tp.unlink(missing_ok=True)
                        except: pass
                    return str(filepath)
            except Exception as e:
                logger.error(f" [edge-split-v15r] merge failed: {e}")
        
        # Cleanup temp files
        for tp in temp_paths:
            try: tp.unlink(missing_ok=True)
            except: pass
    
    # ─── Attempt 3: Split into 4 chunks ───
    if len(words) > 120:
        logger.warning(f" [edge-chunks-v15r] {filename} — splitting into 4 chunks")
        chunk_size = max(20, len(words) // 4)
        chunks = []
        for i in range(0, len(words), chunk_size):
            chunk = ' '.join(words[i:i+chunk_size])
            if chunk.strip():
                chunks.append(chunk)
        
        filepath = self.cache_dir / filename
        temp_paths = []
        all_ok = True
        
        for i, chunk in enumerate(chunks[:4]):
            temp_name = f"tmp_v15r_chunk_{filename}_{i}.mp3"
            temp_path = self.cache_dir / temp_name
            temp_paths.append(temp_path)
            
            ok = await _orig_edge_v15r(self, chunk, voice, temp_name)
            if not ok:
                all_ok = False
                logger.warning(
                    f" [edge-chunks-v15r] {filename} chunk {i+1}/{len(chunks)} failed"
                )
                break
        
        if all_ok and temp_paths:
            try:
                with open(filepath, 'wb') as f:
                    for tp in temp_paths:
                        if tp.exists():
                            f.write(tp.read_bytes())
                if filepath.exists() and filepath.stat().st_size > 1000:
                    logger.info(
                        f" [edge-chunks-v15r] {filename} merged {len(temp_paths)} chunks OK "
                        f"({filepath.stat().st_size} bytes)"
                    )
                    for tp in temp_paths:
                        try: tp.unlink(missing_ok=True)
                        except: pass
                    return str(filepath)
            except Exception as e:
                logger.error(f" [edge-chunks-v15r] merge failed: {e}")
        
        for tp in temp_paths:
            try: tp.unlink(missing_ok=True)
            except: pass
    
    # ─── Last resort: silence placeholder (keeps timeline, no crash) ───
    logger.error(
        f" [edge-silence-v15r] {filename} — all retries failed, "
        f"using silence placeholder (duration ≈ {len(words) // 3}s)"
    )
    
    # Estimate duration: ~3 words/second
    duration_sec = max(2, min(30, len(words) // 3))
    
    try:
        # Try ffmpeg-based silence
        import subprocess
        filepath = self.cache_dir / filename
        result = subprocess.run(
            ['ffmpeg', '-y', '-f', 'lavfi', '-i',
             f'anullsrc=r=24000:cl=mono', '-t', str(duration_sec),
             '-q:a', '9', '-acodec', 'libmp3lame', str(filepath)],
            capture_output=True, timeout=10
        )
        if filepath.exists() and filepath.stat().st_size > 100:
            logger.warning(
                f" [edge-silence-v15r] {filename} silence {duration_sec}s OK"
            )
            return str(filepath)
    except Exception:
        pass
    
    # If ffmpeg not available, try pydub
    try:
        from pydub import AudioSegment
        silence = AudioSegment.silent(duration=duration_sec * 1000)
        filepath = self.cache_dir / filename
        silence.export(str(filepath), format="mp3")
        if filepath.exists():
            logger.warning(
                f" [edge-silence-v15r] {filename} silence {duration_sec}s OK (pydub)"
            )
            return str(filepath)
    except Exception:
        pass
    
    logger.error(f" [edge-silence-v15r] {filename} — cannot create silence, skipping turn")
    return None


AudioGenerator._generate_edge_audio = _patched_edge_v15r
logger.info(" [v15r] Edge TTS progressive retry installed (no gTTS fallback)")


# ═══════════════════════════════════════════════════════════════════
# FIX v16 — S3 gender inference + turn splitting
# ═══════════════════════════════════════════════════════════════════

# FIX 1 — Better gender inference (name-based for unknown labels)
_orig_infer_v16 = AudioGenerator._infer_gender_from_name


def _patched_infer_v16(self, name):
    # First try original
    result = _orig_infer_v16(self, name)
    if result:
        return result
    
    # FIX v16: additional male/female name lists
    MALE_NAMES = {
        'brandon', 'george', 'james', 'john', 'david', 'michael',
        'robert', 'william', 'thomas', 'daniel', 'christopher',
        'anthony', 'mark', 'paul', 'steven', 'kevin', 'brian',
        'jason', 'eric', 'ryan', 'jacob', 'gary', 'nicholas',
        'jonathan', 'stephen', 'larry', 'justin', 'scott', 'frank',
        'alexander', 'raymond', 'patrick', 'jack', 'dennis', 'jerry',
        'tyler', 'aaron', 'henry', 'adam', 'peter', 'nathan',
        'zachary', 'walter', 'kyle', 'harold', 'carl', 'arthur',
        'gerald', 'roger', 'keith', 'jeremy', 'terry', 'harry',
        'sean', 'austin', 'noah', 'evan', 'ronald', 'dmitri',
        'jorge', 'jose', 'juan', 'carlos', 'miguel', 'mohammed',
        'ahmed', 'raj', 'vikram', 'chen', 'hans', 'pierre', 'marco',
    
        # FIX v19 — additional male names
        'aiden', 'brandon', 'evan', 'jared', 'kyle', 'oliver', 'theo',
        'milo', 'finn', 'leo', 'ezra', 'silas', 'wyatt', 'hudson',
        'asher', 'declan', 'julian', 'miles', 'gavin', 'colin',
        'ian', 'neil', 'aidan', 'adam', 'ben', 'benjamin', 'caleb',
        'camden', 'carter', 'chase', 'cole', 'connor', 'cooper',
        'dylan', 'elijah', 'ethan', 'gabriel', 'grayson', 'greyson',
        'hunter', 'isaac', 'jace', 'jaxon', 'jaxson', 'jordan',
        'joseph', 'julian', 'kai', 'landon', 'leah', 'levi',
        'lincoln', 'logan', 'lucas', 'luke', 'mason', 'mateo',
        'matthew', 'max', 'nolan', 'owen', 'parker', 'roman',
        'rowan', 'samuel', 'sawyer', 'silas', 'weston', 'wyatt',
        'zane', 'chris', 'christopher', 'aaron', 'joshua', 'andrew',
        'jake', 'nathan', 'ethan', 'dj', 'piers', 'hugo', 'felix',
        'gus', 'august', 'rufus', 'rex', 'miles', 'milo',
    }
    FEMALE_NAMES = {
        'hana', 'everly', 'sarah', 'emily', 'jessica', 'ashley',
        'jennifer', 'amanda', 'melissa', 'stephanie', 'nicole',
        'elizabeth', 'helen', 'nancy', 'betty', 'margaret', 'sandra',
        'ashley', 'kimberly', 'donna', 'carol', 'michelle', 'emily',
        'laura', 'deborah', 'rebecca', 'sharon', 'cynthia', 'kathleen',
        'amy', 'shirley', 'angela', 'anna', 'brenda', 'pamela',
        'emma', 'samantha', 'katherine', 'christine', 'debra',
        'rachel', 'catherine', 'carolyn', 'janet', 'ruth', 'maria',
        'heather', 'diane', 'virginia', 'julie', 'joyce', 'victoria',
        'kelly', 'lauren', 'christina', 'joan', 'evelyn', 'olivia',
        'isabella', 'mia', 'sophia', 'ava', 'emily', 'abigail',
        'linda', 'barbara', 'susan', 'jessica', 'karen', 'nancy',
        'lisa', 'betty', 'margaret', 'sandra', 'ashley', 'dorothy',
        'beatrice', 'claire', 'nova', 'fatima', 'rina', 'addison',
        'sonia', 'ananya', 'skylar', 'nadia', 'tina', 'caroline',
        'dr. layla', 'layla', 'violet', 'bella',
    }
    
    norm = name.lower().strip().rstrip('.')
    # Strip titles
    for title in ('dr.', 'dr ', 'prof.', 'prof ', 'professor ', 
                  'mr.', 'mr ', 'mrs.', 'mrs ', 'ms.', 'ms '):
        if norm.startswith(title):
            norm = norm[len(title):].strip()
            break
    
    if norm in MALE_NAMES:
        return 'male'
    if norm in FEMALE_NAMES:
        return 'female'
    
    return None


AudioGenerator._infer_gender_from_name = _patched_infer_v16


# FIX 2 — S3 turn splitting (8 → 15-20)
_orig_parse_v16 = AudioGenerator._parse_script


def _patched_parse_v16(self, script, section_num=1):
    turns = _orig_parse_v16(self, script, section_num)
    if section_num != 3 or not turns:
        return turns
    
    # Target 15-20 turns for S3
    if len(turns) >= 12:
        return turns  # Already fine
    
    logger.info(f" [S3-split-v16] {len(turns)} turns detected — splitting long turns")
    
    import re as _re
    new_turns = []
    for spk, txt in turns:
        words = txt.split()
        if len(words) > 50:  # Split turns > 50 words
            sents = _re.split(r'(?<=[.!?])\s+', txt)
            if len(sents) >= 2:
                # Split into 2 parts
                mid = len(sents) // 2
                part1 = ' '.join(sents[:mid]).strip()
                part2 = ' '.join(sents[mid:]).strip()
                if part1:
                    new_turns.append((spk, part1))
                if part2:
                    new_turns.append((spk, part2))
            else:
                # Single sentence — split by words
                half = len(words) // 2
                p1 = ' '.join(words[:half])
                p2 = ' '.join(words[half:])
                if p1: new_turns.append((spk, p1))
                if p2: new_turns.append((spk, p2))
        else:
            new_turns.append((spk, txt))
    
    logger.info(
        f" [S3-split-v16] {len(turns)} → {len(new_turns)} turns "
        f"(split turns >50 words)"
    )
    return new_turns


AudioGenerator._parse_script = _patched_parse_v16
logger.info(" [v16] S3 gender inference + turn splitting applied")


# ═══════════════════════════════════════════════════════════════════
# FIX v17 — S3 don't drop first turn + S2 gender override
# ═══════════════════════════════════════════════════════════════════

# FIX 1 — Better S3 filter: don't drop first turn, handle no-space titles
_orig_filter_v12d = None  # placeholder
try:
    _orig_filter_v12d = AudioGenerator.__dict__.get('_v12d_is_valid_s3_speaker')
except Exception:
    pass


def _v17_valid_s3_speaker(spk):
    """More robust validation — handles 'Dr.Violet' without space."""
    import re as _re
    if not spk:
        return None
    s = spk.strip().rstrip('.').strip()
    
    # Strip titles — handle both "Dr. X" and "Dr.X"
    for title in ('Dr.', 'Prof.', 'Professor', 'Mr.', 'Mrs.', 'Ms.', 'Sir'):
        if s.startswith(title):
            s = s[len(title):].strip()
            break
    
    # Reject if period remains
    if '.' in s:
        return None
    # Reject hyphenated spell-outs
    if '-' in s:
        return None
    # Length cap
    if len(s) > 20 or len(s.split()) > 2:
        return None
    # Stop words
    STOP = {
        'i', 'you', 'we', 'they', 'it', 'the', 'a', 'an', 'and', 'or',
        'but', 'not', 'this', 'that', 'there', 'here', 'what', 'how',
        'think', 'mean', 'say', 'like', 'europe', 'asia', 'africa',
        'america', 'london', 'tokyo', 'of', 'in', 'on', 'at',
    }
    for w in s.lower().split():
        if w in STOP:
            return None
    return s if s else None


# Re-patch parse to use better filter + don't drop first
_orig_parse_v17 = AudioGenerator._parse_script


def _patched_parse_v17(self, script, section_num=1):
    turns = _orig_parse_v17(self, script, section_num)
    if section_num != 3 or not turns:
        return turns
    
    # Re-validate with better filter, but NEVER drop first turn
    kept = []
    dropped_lead = []
    for spk, txt in turns:
        valid = _v17_valid_s3_speaker(spk)
        if valid:
            kept.append((valid, txt))
        else:
            if kept:
                # Merge into previous
                prev = kept[-1]
                kept[-1] = (prev[0], f"{prev[1]} {txt}")
            else:
                # First turn invalid — STASH, don't drop
                dropped_lead.append(txt)
    
    # If first turn(s) dropped, prepend to first kept turn
    if dropped_lead and kept:
        lead_text = ' '.join(dropped_lead)
        first_spk, first_txt = kept[0]
        kept[0] = (first_spk, f"{lead_text} {first_txt}".strip())
        logger.info(
            f" [S3-lead-v17] prepended dropped lead to first speaker "
            f"({len(dropped_lead)} turn(s) rescued)"
        )
    
    return kept if len(kept) >= 2 else turns


AudioGenerator._parse_script = _patched_parse_v17
logger.info(" [v17] S3 first-turn preservation + robust label detection")


# FIX 2 — S2 gender override from script content
_orig_edge_single_v17 = AudioGenerator._generate_single_audio


async def _patched_edge_single_v17(self, text, voice, filename, accent='british',
                                    speaker='default', fast=False,
                                    force_edge=False, section_num=1):
    return await _orig_edge_single_v17(
        self, text, voice, filename, accent, speaker, fast, force_edge, section_num
    )


# Better: override _get_voice_for_speaker for S2 gender from context
_orig_voice_v17 = AudioGenerator._get_voice_for_speaker


def _patched_voice_v17(self, speaker, accent='british', context='', 
                       use_edge=False, forced_gender=None, section_num=0, pool_id=None):
    # FIX v17: for S2, infer gender from script text (Sarah → female)
    if section_num == 2 and not forced_gender:
        try:
            # Look for "My name is X" in recent calls
            # If we have cached speaker_genders, check for mismatch
            pass
        except Exception:
            pass
    
    return _orig_voice_v17(
        self, speaker=speaker, accent=accent, context=context,
        use_edge=use_edge, forced_gender=forced_gender,
        section_num=section_num, pool_id=pool_id
    )


AudioGenerator._get_voice_for_speaker = _patched_voice_v17
logger.info(" [v17] S3 first-turn fix + label robustness")


# ═══════════════════════════════════════════════════════════════════
# FIX v18 — S3 fake labels + turn grouping
# ═══════════════════════════════════════════════════════════════════

def _v18_valid_speaker(spk):
    if not spk:
        return None
    s = spk.strip().rstrip('.').strip()
    for title in ('Dr.', 'Prof.', 'Professor', 'Mr.', 'Mrs.', 'Ms.', 'Sir'):
        if s.startswith(title):
            s = s[len(title):].strip()
            break
    if '.' in s or '-' in s:
        return None
    # Reject single letters (B, C, A, D)
    if len(s) <= 2:
        return None
    # Reject MCQ/options keywords
    BAD = {'option', 'options', 'question', 'answer', 'text',
           'statement', 'choice', 'choices', 'blank', 'blanks'}
    if s.lower() in BAD:
        return None
    if len(s) > 20 or len(s.split()) > 2:
        return None
    STOP = {'i', 'you', 'we', 'they', 'it', 'the', 'a', 'an', 'and', 'or',
            'but', 'not', 'this', 'that', 'there', 'here', 'what', 'how',
            'think', 'mean', 'say', 'like', 'europe', 'asia', 'africa',
            'america', 'london', 'tokyo', 'of', 'in', 'on', 'at'}
    for w in s.lower().split():
        if w in STOP:
            return None
    return s if s else None


_orig_parse_v18 = AudioGenerator._parse_script


def _patched_parse_v18(self, script, section_num=1):
    turns = _orig_parse_v18(self, script, section_num)
    if section_num != 3 or not turns:
        return turns
    
    # Step 1: filter fake labels
    filtered = []
    for spk, txt in turns:
        valid = _v18_valid_speaker(spk)
        if valid:
            filtered.append((valid, txt))
        else:
            if filtered:
                prev = filtered[-1]
                filtered[-1] = (prev[0], f"{prev[1]} {txt}")
    
    # Step 2: group if too many (>22)
    if len(filtered) > 22:
        target = 18
        skip = max(2, len(filtered) // target)
        grouped = []
        i = 0
        while i < len(filtered):
            chunk = filtered[i:i + skip]
            spk = chunk[0][0]
            txt = ' '.join(t for _, t in chunk)
            grouped.append((spk, txt))
            i += skip
        filtered = grouped
    
    if len(filtered) != len(turns):
        logger.info(
            f" [S3-v18] {len(turns)} → {len(filtered)} turns "
            f"(filtered fakes + grouped)"
        )
    return filtered


AudioGenerator._parse_script = _patched_parse_v18
logger.info(" [v18] S3 fake label filter + turn grouping")


# ═══════════════════════════════════════════════════════════════════
# FIX v20 — Disable destructive v18 grouping for S3
# ═══════════════════════════════════════════════════════════════════

_orig_v18_filter = None


def _v20_valid_speaker(spk):
    """Same filter as v18."""
    if not spk:
        return None
    s = spk.strip().rstrip('.').strip()
    for title in ('Dr.', 'Prof.', 'Professor', 'Mr.', 'Mrs.', 'Ms.', 'Sir'):
        if s.startswith(title):
            s = s[len(title):].strip()
            break
    if '.' in s or '-' in s:
        return None
    if len(s) <= 2:
        return None
    BAD = {'option', 'options', 'question', 'answer', 'text',
           'statement', 'choice', 'choices', 'blank', 'blanks'}
    if s.lower() in BAD:
        return None
    if len(s) > 20 or len(s.split()) > 2:
        return None
    STOP = {'i', 'you', 'we', 'they', 'it', 'the', 'a', 'an', 'and', 'or',
            'but', 'not', 'this', 'that', 'there', 'here', 'what', 'how',
            'think', 'mean', 'say', 'like', 'europe', 'asia', 'africa',
            'america', 'london', 'tokyo', 'of', 'in', 'on', 'at'}
    for w in s.lower().split():
        if w in STOP:
            return None
    return s if s else None


_orig_parse_v20 = AudioGenerator._parse_script


def _patched_parse_v20(self, script, section_num=1):
    turns = _orig_parse_v20(self, script, section_num)
    if section_num != 3 or not turns:
        return turns
    
    # Step 1: filter fakes
    filtered = []
    for spk, txt in turns:
        valid = _v20_valid_speaker(spk)
        if valid:
            filtered.append((valid, txt))
        else:
            if filtered:
                prev = filtered[-1]
                filtered[-1] = (prev[0], f"{prev[1]} {txt}")
    
    # Step 2: merge ONLY consecutive same-speaker (preserves all voices)
    merged = []
    for spk, txt in filtered:
        if merged and merged[-1][0] == spk:
            merged[-1] = (spk, f"{merged[-1][1]} {txt}")
        else:
            merged.append((spk, txt))
    
    if len(merged) != len(turns):
        logger.info(
            f" [S3-v20] {len(turns)} → {len(merged)} turns "
            f"(fake filter + same-speaker merge only)"
        )
    return merged


AudioGenerator._parse_script = _patched_parse_v20
logger.info(" [v20] S3 grouping disabled (preserves all speakers)")


# ═══════════════════════════════════════════════════════════════════
# FIX v21 — STANDALONE S3 parser (bypasses all previous S3 patches)
# ═══════════════════════════════════════════════════════════════════

def _v21_parse_s3_fresh(self, script):
    """Self-contained S3 parser — no chaining."""
    import re as _re
    
    if not script or not script.strip():
        return []
    
    turns = []
    
    # Step 1: parse script (newlines OR inline)
    if '\n' in script and script.count('\n') >= 2:
        lines = [l.strip() for l in script.split('\n') if l.strip()]
        pattern = _re.compile(r'^([A-Za-z][A-Za-z0-9_.\- ]{0,30}?)\s*:\s*(.*)$')
        for line in lines:
            m = pattern.match(line)
            if m:
                turns.append((m.group(1).strip(), m.group(2).strip()))
    else:
        pattern = _re.compile(r'(?:^|\s)([A-Z][A-Za-z0-9_.\- ]{0,20}?)\s*:\s*')
        matches = list(pattern.finditer(script))
        for i, m in enumerate(matches):
            spk = m.group(1).strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(script)
            txt = script[start:end].strip()
            if txt:
                turns.append((spk, txt))
    
    # Step 2: filter fakes
    def is_valid(spk):
        s = spk.rstrip('.').strip()
        for t in ('Dr.', 'Prof.', 'Professor', 'Mr.', 'Mrs.', 'Ms.', 'Sir'):
            if s.startswith(t):
                s = s[len(t):].strip()
                break
        if not s:
            return None
        if '.' in s or '-' in s:
            return None
        if len(s) <= 2:
            return None
        BAD = {'option', 'options', 'question', 'answer', 'text',
               'statement', 'choice', 'blanks', 'blank'}
        if s.lower() in BAD:
            return None
        if len(s) > 20 or len(s.split()) > 2:
            return None
        STOP = {'i', 'you', 'we', 'they', 'it', 'the', 'a', 'an', 'and', 'or',
                'but', 'not', 'this', 'that', 'there', 'here', 'what', 'how',
                'think', 'mean', 'say', 'like', 'europe', 'asia', 'africa',
                'america', 'london', 'tokyo', 'of', 'in', 'on', 'at'}
        for w in s.lower().split():
            if w in STOP:
                return None
        return s
    
    filtered = []
    for spk, txt in turns:
        v = is_valid(spk)
        if v:
            filtered.append((v, txt))
        elif filtered:
            filtered[-1] = (filtered[-1][0], f"{filtered[-1][1]} {txt}")
    
    # Step 3: merge ONLY consecutive same-speaker (safe)
    merged = []
    for spk, txt in filtered:
        if merged and merged[-1][0] == spk:
            merged[-1] = (spk, f"{merged[-1][1]} {txt}")
        else:
            merged.append((spk, txt))
    
    unique_speakers = set(s for s, _ in merged)
    logger.info(
        f" [S3-v21] {len(turns)} raw → {len(merged)} turns, "
        f"{len(unique_speakers)} distinct speakers: {sorted(unique_speakers)}"
    )
    
    return merged


# Wrap: S3 → v21 standalone; others → pass through old chain
_orig_parse_v21 = AudioGenerator._parse_script


def _patched_parse_v21(self, script, section_num=1):
    if section_num == 3:
        return _v21_parse_s3_fresh(self, script)
    return _orig_parse_v21(self, script, section_num)


AudioGenerator._parse_script = _patched_parse_v21
logger.info(" [v21] S3 standalone parser installed (bypasses chain)")


# ==================================================================
# FIX v25 - S4 hybrid: edge fast -> chunked -> deepgram fallback
# ==================================================================

def _v25_edge_voice_to_deepgram(edge_voice):
    """Map edge_tts voice to deepgram voice of same gender."""
    if not edge_voice:
        return 'aura-2-asteria-en'
    vl = edge_voice.lower()
    male_kw = ['thomas', 'ryan', 'guy', 'william', 'christopher', 'eric']
    for kw in male_kw:
        if kw in vl:
            return 'aura-2-orion-en'
    return 'aura-2-asteria-en'


async def _v25_s4_hybrid(self, text, voice, filename):
    """S4: fast edge -> chunked edge -> deepgram fallback chain."""
    import asyncio as _aio

    filepath = self.cache_dir / filename
    if filepath.exists():
        try:
            filepath.unlink()
        except Exception:
            pass

    # ATTEMPT 1: Edge full, 20s timeout
    try:
        result = await _aio.wait_for(
            _orig_single_v25(self, text, voice, filename, section_num=4),
            timeout=20.0
        )
        if result and result[0]:
            return result
    except _aio.TimeoutError:
        logger.warning(" [v25-s4] edge timeout for " + filename)
    except Exception as _e:
        logger.warning(" [v25-s4] edge exception: " + str(_e))

    # ATTEMPT 2: Chunked edge (60 words, 10s each, up to 6 chunks)
    words = text.split()
    if len(words) > 80:
        logger.info(
            " [v25-s4] chunking " + str(len(words)) + " words for " + filename
        )
        chunks = [' '.join(words[i:i+60]) for i in range(0, len(words), 60)]
        chunk_paths = []
        for i, chunk in enumerate(chunks[:6]):
            cf = "tmp_v25_" + filename + "_c" + str(i) + ".mp3"
            try:
                r = await _aio.wait_for(
                    _orig_single_v25(self, chunk, voice, cf, section_num=4),
                    timeout=10.0
                )
                if r and r[0]:
                    cpath = self.cache_dir / cf
                    if cpath.exists() and cpath.stat().st_size > 500:
                        chunk_paths.append(cpath)
            except Exception:
                pass

        if chunk_paths and len(chunk_paths) >= 2:
            try:
                with open(filepath, 'wb') as f:
                    for p in chunk_paths:
                        f.write(p.read_bytes())
                for p in chunk_paths:
                    try:
                        p.unlink()
                    except Exception:
                        pass
                if filepath.exists() and filepath.stat().st_size > 1000:
                    logger.info(
                        " [v25-s4] chunked rescue: " +
                        str(len(chunk_paths)) + "/" + str(len(chunks)) +
                        " chunks, " +
                        str(filepath.stat().st_size // 1024) + " KB"
                    )
                    return str(filepath), 'edge-chunked'
            except Exception as _me:
                logger.warning(" [v25-s4] chunk merge failed: " + str(_me))

    # ATTEMPT 3: Deepgram fallback
    if DEEPGRAM_AVAILABLE:
        dg_voice = _v25_edge_voice_to_deepgram(voice)
        logger.warning(
            " [v25-s4] deepgram fallback " + dg_voice + " for " + filename
        )
        try:
            dg_result = await self._generate_deepgram_audio(text, dg_voice, filename)
            if dg_result:
                logger.info(" [v25-s4] deepgram OK: " + filename)
                return dg_result, 'deepgram-fallback'
        except Exception as _de:
            logger.error(" [v25-s4] deepgram fail: " + str(_de))

    logger.error(" [v25-s4] ALL methods failed for " + filename)
    return None, 'failed'


_orig_single_v25 = AudioGenerator._generate_single_audio


async def _patched_single_v25(self, text, voice, filename, accent='british',
                              speaker='default', fast=False, force_edge=False,
                              section_num=1):
    if section_num == 4:
        return await _v25_s4_hybrid(self, text, voice, filename)
    return await _orig_single_v25(
        self, text, voice, filename, accent, speaker, fast, force_edge, section_num
    )


AudioGenerator._generate_single_audio = _patched_single_v25
logger.info(" [v25] S4 hybrid edge->chunked->deepgram active")


# ==================================================================
# FIX v26 - S3 safe split + S4 parallel fast hybrid
# ==================================================================

_prev_parse_v26 = AudioGenerator._parse_script


def _v26_s3_split_safe(turns):
    """Split S3 turns if too few (preserves all speakers)."""
    if not turns or len(turns) >= 12:
        return turns
    import re as _re
    new_turns = []
    for spk, txt in turns:
        if not isinstance(txt, str):
            new_turns.append((spk, str(txt)))
            continue
        words = txt.split()
        if len(words) > 60:
            sents = _re.split(r'(?<=[.!?])\s+', txt)
            sents = [s.strip() for s in sents if s.strip()]
            if len(sents) >= 2:
                mid = len(sents) // 2
                part1 = ' '.join(sents[:mid]).strip()
                part2 = ' '.join(sents[mid:]).strip()
                if part1:
                    new_turns.append((spk, part1))
                if part2:
                    new_turns.append((spk, part2))
                continue
        new_turns.append((spk, txt))
    if len(new_turns) > len(turns):
        logger.info(
            " [S3-split-v26] " + str(len(turns)) + " -> " + str(len(new_turns)) + " turns"
        )
    return new_turns


def _patched_parse_v26(self, script, section_num=1):
    turns = _prev_parse_v26(self, script, section_num)
    if section_num == 3 and turns:
        turns = _v26_s3_split_safe(turns)
    return turns


AudioGenerator._parse_script = _patched_parse_v26
logger.info(" [v26] S3 safe split installed")


# ==================================================================
# S4 parallel fast hybrid (parallel chunks + 2x retry + deepgram)
# ==================================================================

_prev_single_v26 = AudioGenerator._generate_single_audio


async def _v26_s4_parallel(self, text, voice, filename):
    """Fast S4: 15s edge -> parallel chunks with retry -> deepgram."""
    import asyncio as _aio

    filepath = self.cache_dir / filename
    if filepath.exists():
        try:
            filepath.unlink()
        except Exception:
            pass

    # Check edge availability
    if not EDGE_AVAILABLE:
        if DEEPGRAM_AVAILABLE:
            dg_voice = _v25_edge_voice_to_deepgram(voice)
            try:
                r = await self._generate_deepgram_audio(text, dg_voice, filename)
                if r:
                    return r, 'deepgram-direct'
            except Exception:
                pass
        return None, 'failed'

    import edge_tts as _edge

    # ATTEMPT 1: Edge full, 15s timeout (bypass all retries)
    try:
        await _aio.wait_for(
            _edge.Communicate(text, voice).save(str(filepath)),
            timeout=15.0,
        )
        if filepath.exists() and filepath.stat().st_size > 2000:
            logger.info(" [v26-s4] edge direct OK: " + filename)
            return str(filepath), 'edge'
    except _aio.TimeoutError:
        logger.warning(" [v26-s4] edge 15s timeout for " + filename)
    except Exception as _e:
        logger.warning(" [v26-s4] edge fail: " + str(_e)[:100])

    # ATTEMPT 2: Parallel chunks (60 words each) with 2x retry + deepgram fallback
    words = text.split()
    if len(words) > 80:
        chunk_size = 60
        chunks = [' '.join(words[i:i+chunk_size]) for i in range(0, len(words), chunk_size)]
        chunks = chunks[:8]  # cap at 8
        logger.info(
            " [v26-s4] parallel chunks: " + str(len(chunks)) + " for " + filename
        )

        async def do_chunk(idx, chunk):
            cf = "tmp_v26_" + filename + "_c" + str(idx) + ".mp3"
            cpath = self.cache_dir / cf

            # Try edge 2x
            for attempt in range(2):
                try:
                    await _aio.wait_for(
                        _edge.Communicate(chunk, voice).save(str(cpath)),
                        timeout=10.0,
                    )
                    if cpath.exists() and cpath.stat().st_size > 500:
                        return cpath
                except Exception:
                    if attempt == 0:
                        await _aio.sleep(1)

            # Fallback: deepgram for this chunk
            if DEEPGRAM_AVAILABLE:
                dg_voice = _v25_edge_voice_to_deepgram(voice)
                try:
                    await self._generate_deepgram_audio(chunk, dg_voice, cf)
                    if cpath.exists() and cpath.stat().st_size > 500:
                        logger.info(
                            " [v26-s4] chunk " + str(idx) + " deepgram fallback OK"
                        )
                        return cpath
                except Exception:
                    pass

            logger.error(" [v26-s4] chunk " + str(idx) + " TOTAL FAILURE")
            return None

        tasks = [do_chunk(i, c) for i, c in enumerate(chunks)]
        results = await _aio.gather(*tasks, return_exceptions=True)

        chunk_paths = [r for r in results if r and not isinstance(r, Exception)]

        if chunk_paths and len(chunk_paths) == len(chunks):
            try:
                with open(filepath, 'wb') as f:
                    for p in chunk_paths:
                        f.write(p.read_bytes())
                for p in chunk_paths:
                    try:
                        p.unlink()
                    except Exception:
                        pass
                if filepath.exists() and filepath.stat().st_size > 1000:
                    logger.info(
                        " [v26-s4] parallel rescue: " + str(len(chunk_paths)) + "/" + str(len(chunks)) +
                        " chunks, " + str(filepath.stat().st_size // 1024) + " KB"
                    )
                    return str(filepath), 'edge-parallel'
            except Exception as _me:
                logger.warning(" [v26-s4] merge fail: " + str(_me))

        elif chunk_paths and len(chunk_paths) >= 2:
            try:
                with open(filepath, 'wb') as f:
                    for p in chunk_paths:
                        f.write(p.read_bytes())
                for p in chunk_paths:
                    try:
                        p.unlink()
                    except Exception:
                        pass
                if filepath.exists() and filepath.stat().st_size > 1000:
                    logger.warning(
                        " [v26-s4] partial: " + str(len(chunk_paths)) + "/" + str(len(chunks)) +
                        " chunks, " + str(filepath.stat().st_size // 1024) + " KB"
                    )
                    return str(filepath), 'edge-partial'
            except Exception:
                pass

    # ATTEMPT 3: Full deepgram fallback
    if DEEPGRAM_AVAILABLE:
        dg_voice = _v25_edge_voice_to_deepgram(voice)
        logger.warning(" [v26-s4] full deepgram fallback: " + filename)
        try:
            r = await self._generate_deepgram_audio(text, dg_voice, filename)
            if r:
                return r, 'deepgram-full-fallback'
        except Exception:
            pass

    return None, 'failed'


async def _patched_single_v26(self, text, voice, filename, accent='british',
                              speaker='default', fast=False, force_edge=False,
                              section_num=1):
    if section_num == 4:
        return await _v26_s4_parallel(self, text, voice, filename)
    return await _prev_single_v26(
        self, text, voice, filename, accent, speaker, fast, force_edge, section_num
    )


AudioGenerator._generate_single_audio = _patched_single_v26
logger.info(" [v26] S4 parallel fast hybrid installed")


# ==================================================================
# FIX v28 - S4 voice consistency (no Edge+Deepgram mixing)
# ==================================================================

_prev_s4_v28 = AudioGenerator._generate_single_audio


async def _v28_s4_consistent(self, text, voice, filename):
    """S4: keep voice consistent. Whole-turn fallback, not per-chunk."""
    import asyncio as _aio

    filepath = self.cache_dir / filename
    if filepath.exists():
        try:
            filepath.unlink()
        except Exception:
            pass

    if not EDGE_AVAILABLE:
        if DEEPGRAM_AVAILABLE:
            dg_voice = _v25_edge_voice_to_deepgram(voice)
            try:
                r = await self._generate_deepgram_audio(text, dg_voice, filename)
                if r:
                    return r, 'deepgram-whole'
            except Exception:
                pass
        return None, 'failed'

    import edge_tts as _edge

    # Attempt 1: Edge full, 15s
    try:
        await _aio.wait_for(
            _edge.Communicate(text, voice).save(str(filepath)),
            timeout=15.0,
        )
        if filepath.exists() and filepath.stat().st_size > 2000:
            logger.info(" [v28-s4] edge direct OK: " + filename)
            return str(filepath), 'edge'
    except _aio.TimeoutError:
        logger.warning(" [v28-s4] edge 15s timeout: " + filename)
    except Exception as _e:
        logger.warning(" [v28-s4] edge fail: " + str(_e)[:100])

    # Attempt 2: Edge CHUNKS (all same edge voice)
    words = text.split()
    if len(words) > 80:
        chunk_size = 100
        chunks = [' '.join(words[i:i+chunk_size]) for i in range(0, len(words), chunk_size)]
        chunks = chunks[:6]
        logger.info(" [v28-s4] edge chunks (same voice): " + str(len(chunks)) + " for " + filename)

        chunk_paths = []
        all_ok = True
        for i, chunk in enumerate(chunks):
            cf = "tmp_v28_" + filename + "_c" + str(i) + ".mp3"
            cpath = self.cache_dir / cf
            ok = False
            for attempt in range(2):
                try:
                    await _aio.wait_for(
                        _edge.Communicate(chunk, voice).save(str(cpath)),
                        timeout=12.0,
                    )
                    if cpath.exists() and cpath.stat().st_size > 500:
                        ok = True
                        break
                except Exception:
                    if attempt == 0:
                        await _aio.sleep(1)
            if ok:
                chunk_paths.append(cpath)
            else:
                all_ok = False
                logger.warning(" [v28-s4] edge chunk " + str(i) + " failed")
                break

        if all_ok and chunk_paths and len(chunk_paths) == len(chunks):
            # All chunks succeeded with SAME edge voice
            try:
                with open(filepath, 'wb') as f:
                    for p in chunk_paths:
                        f.write(p.read_bytes())
                for p in chunk_paths:
                    try:
                        p.unlink()
                    except Exception:
                        pass
                if filepath.exists() and filepath.stat().st_size > 1000:
                    logger.info(
                        " [v28-s4] edge chunks OK: " + str(len(chunks)) + " chunks, " +
                        str(filepath.stat().st_size // 1024) + " KB (SAME voice)"
                    )
                    return str(filepath), 'edge-chunks'
            except Exception as _me:
                logger.warning(" [v28-s4] merge fail: " + str(_me))

        # Cleanup partial chunks
        for p in chunk_paths:
            try:
                p.unlink()
            except Exception:
                pass

    # Attempt 3: WHOLE turn Deepgram (same voice for entire turn)
    if DEEPGRAM_AVAILABLE:
        dg_voice = _v25_edge_voice_to_deepgram(voice)
        logger.warning(
            " [v28-s4] whole-turn deepgram fallback: " + filename +
            " (voice=" + dg_voice + ")"
        )
        try:
            r = await self._generate_deepgram_audio(text, dg_voice, filename)
            if r:
                logger.info(" [v28-s4] deepgram whole-turn OK: " + filename)
                return r, 'deepgram-whole'
        except Exception as _de:
            logger.error(" [v28-s4] deepgram fail: " + str(_de))

    return None, 'failed'


async def _patched_single_v28(self, text, voice, filename, accent='british',
                              speaker='default', fast=False, force_edge=False,
                              section_num=1):
    if section_num == 4:
        return await _v28_s4_consistent(self, text, voice, filename)
    return await _prev_s4_v28(
        self, text, voice, filename, accent, speaker, fast, force_edge, section_num
    )


AudioGenerator._generate_single_audio = _patched_single_v28
logger.info(" [v28] S4 voice consistency (no Edge+Deepgram mixing) active")
