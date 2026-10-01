"""Unified audio service for all IELTS/PTE modules with customer-gender-aware voice assignment"""

import hashlib
import asyncio
import logging
import subprocess
import json
import os
import time
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from datetime import datetime

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    print("⚠️ edge-tts not installed. Install with: pip install edge-tts")

try:
    from gtts import gTTS
    GTTS_AVAILABLE = True
except ImportError:
    GTTS_AVAILABLE = False
    print("⚠️ gTTS not installed. Install with: pip install gTTS")

logger = logging.getLogger(__name__)


class UnifiedAudioService:
    """Single service for all audio generation needs across the platform"""
    
    VOICES = {
        "en-GB-SoniaNeural": "en-GB-SoniaNeural",
        "en-GB-RyanNeural": "en-GB-RyanNeural",
        "en-GB-LibbyNeural": "en-GB-LibbyNeural",
        "en-GB-ThomasNeural": "en-GB-ThomasNeural",
        "en-US-JennyNeural": "en-US-JennyNeural",
        "en-US-GuyNeural": "en-US-GuyNeural",
    }
    
    # Section 3 voices – 4 distinct voices for 4 speakers
    SECTION3_VOICES = [
        "en-GB-SoniaNeural",   # clear British female
        "en-GB-RyanNeural",    # British male
        "en-GB-LibbyNeural",   # another British female
        "en-GB-ThomasNeural",  # another British male
    ]
    
    FEMALE_NAMES = {
        'maria', 'sarah', 'emma', 'sonia', 'libby', 'jane', 'anna', 'mary', 'linda',
        'barbara', 'elizabeth', 'jennifer', 'susan', 'jessica', 'karen', 'lisa', 'helen',
        'sandra', 'donna', 'carol', 'ruth', 'sharon', 'michelle', 'laura', 'kimberly',
        'deborah', 'amanda', 'stephanie', 'carolyn', 'christine', 'janet', 'catherine',
        'kathleen', 'ann', 'julia', 'alice', 'carol', 'sophie', 'emily', 'olivia',
        'ava', 'isabella', 'mia', 'charlotte', 'amelia', 'harper', 'evelyn', 'abigail'
    }
    MALE_NAMES = {
        'james', 'david', 'leo', 'amin', 'thomas', 'ryan', 'john', 'robert', 'michael',
        'william', 'joseph', 'charles', 'christopher', 'daniel', 'matthew', 'anthony',
        'donald', 'mark', 'paul', 'steven', 'andrew', 'kenneth', 'joshua', 'kevin',
        'brian', 'george', 'timothy', 'ronald', 'edward', 'jason', 'jeffrey', 'ryan',
        'gary', 'nicholas', 'eric', 'jonathan', 'stephen', 'larry', 'justin', 'scott',
        'brandon', 'benjamin', 'samuel', 'raymond', 'gregory', 'frank', 'alexander'
    }
    
    def __init__(self, cache_dir: str = "static/audio_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.subdirs = ['speaking', 'listening', 'pte', 'ukvi', 'listening/sections', 'listening/questions']
        for subdir in self.subdirs:
            (self.cache_dir / subdir).mkdir(parents=True, exist_ok=True)
        
        self.available = EDGE_TTS_AVAILABLE or GTTS_AVAILABLE
        self.ffmpeg_available = self._check_ffmpeg()
        self._stats = {'total_generated': 0, 'cache_hits': 0, 'cache_misses': 0, 'errors': 0}
        self._generation_locks = {}
        self._lock = threading.Lock()
        
        if EDGE_TTS_AVAILABLE:
            logger.info("✅ UnifiedAudioService initialized with Edge TTS (customer-gender-aware)")
        elif GTTS_AVAILABLE:
            logger.info("✅ UnifiedAudioService initialized with gTTS (fallback)")
        else:
            logger.warning("⚠️ No TTS available")
        if not self.ffmpeg_available:
            logger.warning("⚠️ ffmpeg not available - audio merging will fallback to first file")

    def _check_ffmpeg(self) -> bool:
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5, check=False)
            return True
        except:
            return False

    def _detect_gender_from_name(self, name: str) -> str:
        name_lower = name.lower().strip()
        if name_lower in self.FEMALE_NAMES:
            return 'female'
        if name_lower in self.MALE_NAMES:
            return 'male'
        return 'male'  # default

    def _extract_customer_name(self, turns: List[Dict], section: int) -> Optional[str]:
        if not turns or section not in (1, 3):
            return None
        for turn in turns[:3]:
            text = turn.get('text', '')
            match = re.search(r'(?:my name is|i\'?m|i am|call me)\s+([A-Z][a-z]+)', text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _get_voice_customer_aware(self, speaker: str, section_num: int, customer_gender: Optional[str] = None) -> str:
        """
        Assign a voice based on speaker and section.
        For Section 3, uses a deterministic hash to assign one of 4 distinct voices.
        For other sections, uses gender-based assignment.
        """
        # ----- SECTION 3: four distinct voices -----
        if section_num == 3:
            # Use speaker name to get a deterministic index (0-3)
            # This ensures the same speaker always gets the same voice.
            name_hash = int(hashlib.md5(speaker.encode('utf-8')).hexdigest(), 16)
            idx = name_hash % len(self.SECTION3_VOICES)
            return self.SECTION3_VOICES[idx]

        # ----- OTHER SECTIONS (1, 2, 4) -----
        speaker_lower = speaker.lower().strip()
        
        # If we have customer gender info, use it to choose appropriate voice
        if customer_gender:
            is_customer = any(role in speaker_lower for role in ['customer', 'client', 'caller', 'visitor', 'guest', 'patient'])
            is_agent = any(role in speaker_lower for role in ['agent', 'receptionist', 'officer', 'guide'])
            if is_customer:
                return 'en-GB-RyanNeural' if customer_gender == 'male' else 'en-GB-SoniaNeural'
            if is_agent:
                return 'en-GB-SoniaNeural' if customer_gender == 'male' else 'en-GB-RyanNeural'
        
        # Fallback to gender detection from name
        gender = self._detect_gender_from_name(speaker)
        if gender == 'female':
            # Use a female voice
            if 'student' in speaker_lower:
                return 'en-GB-LibbyNeural'
            else:
                return 'en-GB-SoniaNeural'
        else:
            # Use a male voice
            if 'student' in speaker_lower:
                return 'en-GB-RyanNeural'
            else:
                return 'en-GB-ThomasNeural'

    def generate_listening_section_audio(self, section_data: Dict, section_num: int, custom_script: str = None) -> Dict:
        if custom_script:
            script = custom_script
        else:
            script = section_data.get('audio_script', '') or section_data.get('script', '')
        
        merged_filename = f"listening/sections/section{section_num}_merged_{self._hash(str(section_num) + script[:50])}.mp3"
        merged_path = self.cache_dir / merged_filename
        
        if merged_path.exists() and merged_path.stat().st_size > 5000:
            return {
                'success': True, 'merged_audio': f"/static/audio_cache/{merged_filename}",
                'duration': self._get_audio_duration(merged_path), 'from_cache': True, 'section': section_num,
                'merge_success': True
            }
        
        generation_key = f"section_{section_num}_{self._hash(script[:100] if script else str(section_num))}"
        with self._lock:
            if generation_key in self._generation_locks:
                time.sleep(0.5)
                if merged_path.exists() and merged_path.stat().st_size > 5000:
                    return {'success': True, 'merged_audio': f"/static/audio_cache/{merged_filename}", 'from_cache': True}
                return {'success': False, 'error': 'Previous generation in progress'}
            self._generation_locks[generation_key] = True
        
        try:
            return self._generate_listening_section_audio_smart(script, section_num, merged_path, merged_filename)
        finally:
            with self._lock:
                if generation_key in self._generation_locks:
                    del self._generation_locks[generation_key]

    def _generate_listening_section_audio_smart(self, script: str, section_num: int, merged_path: Path, merged_filename: str) -> Dict:
        """
        Generate complete section audio with:
        - Introduction (only section 1)
        - Section instruction
        - 30 sec silence (reading time)
        - Main script audio (with speaker voices)
        - 30 sec silence (checking answers)
        """
        audio_parts = []
        
        # ----- 1. Introduction (only for section 1) -----
        if section_num == 1:
            intro = self._generate_introduction_audio()
            if intro:
                audio_parts.append(intro)
        
        # ----- 2. Section instruction -----
        instruction_text = self.SECTION_AUDIO_INSTRUCTIONS.get(section_num)
        if instruction_text:
            inst = self._generate_instruction_audio(instruction_text, section_num)
            if inst:
                audio_parts.append(inst)
        
        # ----- 3. 30 sec reading silence -----
        if self.ffmpeg_available:
            silence = self._generate_silence(30000, f"reading_time_s{section_num}")
            if silence:
                audio_parts.append(silence)
        
        # ----- 4. Main script audio -----
        if script:
            # ----- For Section 3, always force 4 speakers -----
            if section_num == 3:
                turns = self._force_section3_speakers(script)
            else:
                turns = self._split_by_speakers_with_context(script, section_num)
            
            # Safety check: if section 3 and we have fewer than 2 distinct speakers, force split again
            if section_num == 3:
                distinct_speakers = set(turn.get('speaker') for turn in turns if turn.get('speaker'))
                if len(distinct_speakers) < 2:
                    logger.warning("Section 3: fewer than 2 speakers detected; forcing split again.")
                    turns = self._force_section3_speakers(script)
            
            logger.info(f"Section {section_num}: split into {len(turns)} turns, speakers: {set(t.get('speaker') for t in turns)}")
            customer_gender = None
            if section_num in (1, 3):
                customer_name = self._extract_customer_name(turns, section_num)
                if customer_name:
                    customer_gender = self._detect_gender_from_name(customer_name)
                    logger.info(f"[Customer-aware] Detected customer gender: {customer_gender} from name: {customer_name}")
            
            speaker_texts = {}
            for turn in turns:
                speaker = turn.get('speaker', 'Unknown')
                text = turn.get('text', '').strip()
                if not text:
                    continue
                speaker_texts[speaker] = speaker_texts.get(speaker, '') + ' ' + text
            
            for speaker, full_text in speaker_texts.items():
                if len(full_text) < 3:
                    continue
                voice = self._get_voice_customer_aware(speaker, section_num, customer_gender)
                # Clean labels before TTS
                clean_text = self._clean_text_for_tts(full_text)
                text_hash = self._hash(clean_text)
                filename = f"listening/sections/section{section_num}_{speaker.replace(' ', '_')}_{text_hash}.mp3"
                filepath = self.cache_dir / filename
                if self._generate_audio_any(clean_text, filepath, voice):
                    audio_parts.append(f"/static/audio_cache/{filename}")
                else:
                    logger.warning(f"Failed to generate audio for speaker: {speaker}")
        
        # ----- 5. 30 sec check silence -----
        if self.ffmpeg_available:
            silence = self._generate_silence(30000, f"review_time_s{section_num}")
            if silence:
                audio_parts.append(silence)
        
        # ----- Merge all parts -----
        if audio_parts:
            file_paths = []
            for part in audio_parts:
                if isinstance(part, str) and part.startswith('/static/audio_cache/'):
                    p = str(self.cache_dir / part.replace('/static/audio_cache/', ''))
                else:
                    p = str(part)
                if os.path.exists(p):
                    file_paths.append(p)
            
            if file_paths:
                if len(file_paths) == 1:
                    return {
                        'success': True,
                        'merged_audio': audio_parts[0],
                        'duration': self._get_audio_duration(Path(file_paths[0])),
                        'merge_success': False,
                        'section': section_num
                    }
                
                if self.ffmpeg_available and self._merge_audio_files(file_paths, merged_path):
                    if merged_path.exists():
                        return {
                            'success': True,
                            'merged_audio': f"/static/audio_cache/{merged_filename}",
                            'duration': self._get_audio_duration(merged_path),
                            'merge_success': True,
                            'section': section_num
                        }
                
                # Fallback: return first audio part
                return {
                    'success': True,
                    'merged_audio': audio_parts[0],
                    'duration': self._get_audio_duration(Path(file_paths[0])),
                    'merge_success': False,
                    'section': section_num
                }
        
        return {'success': False, 'error': 'No audio generated', 'section': section_num}

    def _split_by_speakers_with_context(self, script: str, section_num: int) -> List[Dict]:
        if not script:
            return []
        if section_num == 1:
            return self._split_section1_smart(script)
        if section_num == 2:
            # Monologue – single speaker
            return [{'speaker': 'Guide', 'text': script, 'context': script[:100]}]
        if section_num == 3:
            # This is called only for non‑section3; but we keep it for safety
            return self._split_section3_smart(script)
        # Section 4 – lecture, single speaker
        return [{'speaker': 'Lecturer', 'text': script, 'context': script[:100]}]

    def _split_section1_smart(self, script: str) -> List[Dict]:
        parts = []
        lines = script.split('\n')
        current_speaker = None
        current_text = []
        detected = {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            match = re.match(r'^([A-Za-z]+(?:\s+[A-Za-z]+)?)\s*[:.]\s*(.*)', line)
            if match:
                raw = match.group(1).strip()
                text = match.group(2).strip()
                if raw.lower() in self.FEMALE_NAMES or raw.lower() in self.MALE_NAMES:
                    speaker = raw.title()
                elif raw.lower() in ['agent', 'receptionist', 'officer']:
                    speaker = 'Agent'
                elif raw.lower() in ['customer', 'client', 'patient', 'caller', 'visitor']:
                    speaker = 'Customer'
                else:
                    speaker = 'Customer' if len(detected) % 2 == 0 else 'Agent'
                if speaker not in detected:
                    detected[speaker] = True
                if current_speaker and current_speaker != speaker and current_text:
                    parts.append({'speaker': current_speaker, 'text': ' '.join(current_text), 'context': current_text[0][:100]})
                    current_text = []
                current_speaker = speaker
                if text:
                    current_text.append(text)
            else:
                if current_text:
                    current_text.append(line)
        if current_text and current_speaker:
            parts.append({'speaker': current_speaker, 'text': ' '.join(current_text), 'context': current_text[0][:100]})
        return parts

    # ========== Force Section 3 into 4 speakers ==========
    def _force_section3_speakers(self, script: str) -> List[Dict]:
        """
        Split the script into 4 roughly equal parts and assign speakers:
        Tutor, Student1, Student2, Student3.
        This guarantees multiple distinct voices regardless of labels.
        """
        lines = [line.strip() for line in script.split('\n') if line.strip()]
        if not lines:
            return []
        
        default_speakers = ['Tutor', 'Student1', 'Student2', 'Student3']
        total_lines = len(lines)
        # Split lines into 4 parts by line index
        chunk_size = max(1, total_lines // 4)
        parts = []
        for i in range(4):
            start = i * chunk_size
            end = start + chunk_size if i < 3 else total_lines
            if start >= total_lines:
                break
            chunk_lines = lines[start:end]
            if chunk_lines:
                text = ' '.join(chunk_lines)
                speaker = default_speakers[i % len(default_speakers)]
                parts.append({'speaker': speaker, 'text': text, 'context': chunk_lines[0][:100]})
        logger.info(f"[Section 3] Forced split into {len(parts)} speakers: {[p['speaker'] for p in parts]}")
        return parts

    # Old _split_section3_smart is no longer used; we keep it but not called.
    def _split_section3_smart(self, script: str) -> List[Dict]:
        """Legacy method – not used anymore."""
        return self._force_section3_speakers(script)

    def _generate_audio_any(self, text: str, output_path: Path, voice: str = "en-GB-SoniaNeural") -> bool:
        text = self._clean_text_for_tts(text)
        if len(text) < 3:
            return False
        if EDGE_TTS_AVAILABLE:
            try:
                async def _speak():
                    await edge_tts.Communicate(text, voice).save(str(output_path))
                asyncio.run(_speak())
                if output_path.exists() and output_path.stat().st_size > 5000:
                    return True
            except Exception as e:
                logger.warning(f"Edge TTS failed: {e}")
        if GTTS_AVAILABLE:
            try:
                gTTS(text=text, lang='en', slow=False).save(str(output_path))
                if output_path.exists() and output_path.stat().st_size > 5000:
                    return True
            except Exception as e:
                logger.warning(f"gTTS failed: {e}")
        return False

    def _clean_text_for_tts(self, text: str) -> str:
        if not text:
            return ""
        # Remove speaker labels like "Professor Chen:"
        text = re.sub(r'^[A-Za-z]+(?:\s+[A-Za-z]+)?\s*[:.]\s*', '', text, flags=re.MULTILINE)
        # Remove brackets and parentheses
        text = re.sub(r'\[[A-Za-z\s]+\]|\([A-Za-z\s]+\)', '', text)
        # Remove URLs
        text = re.sub(r'https?://\S+', '', text)
        # Remove non-ASCII characters
        text = re.sub(r'[^\x00-\x7F]+', ' ', text)
        # Replace ellipsis and em-dash
        text = text.replace('…', '...').replace('—', '-')
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        # Limit length for TTS
        return text[:3000]

    # ----- Instruction / Intro generation -----
    SECTION_AUDIO_INSTRUCTIONS = {
        1: "Section 1. You will hear a conversation between two people. Look at questions 1 to 10.",
        2: "Section 2. You will hear a monologue. Look at questions 11 to 20.",
        3: "Section 3. You will hear a conversation between up to four people. Look at questions 21 to 30.",
        4: "Section 4. You will hear a lecture. Look at questions 31 to 40.",
    }
    TEST_INTRODUCTION = "This is the IELTS Listening Test. All recordings will be played once only. Now turn to Section 1."

    def _generate_introduction_audio(self) -> Optional[str]:
        return self._generate_simple_audio(self.TEST_INTRODUCTION, "listening/introduction", "en-GB-SoniaNeural")
    
    def _generate_instruction_audio(self, text: str, section_num: int) -> Optional[str]:
        return self._generate_simple_audio(text, f"listening/instruction_s{section_num}", "en-GB-SoniaNeural")

    def _generate_simple_audio(self, text: str, base_name: str, voice: str) -> Optional[str]:
        hash_val = self._hash(text)
        filename = f"{base_name}_{hash_val}.mp3"
        filepath = self.cache_dir / filename
        if filepath.exists():
            return f"/static/audio_cache/{filename}"
        if self._generate_audio_any(text, filepath, voice):
            return f"/static/audio_cache/{filename}"
        return None

    def _generate_silence(self, duration_ms: int, name: str) -> Optional[str]:
        if not self.ffmpeg_available:
            return None
        silence_path = self.cache_dir / f"silence_{name}_{duration_ms}.mp3"
        if silence_path.exists():
            return str(silence_path)
        try:
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                "-t", str(duration_ms / 1000), "-ar", "44100", "-ac", "1", "-b:a", "128k",
                str(silence_path)
            ], capture_output=True, timeout=30, check=False)
            if silence_path.exists():
                return str(silence_path)
        except:
            pass
        return None

    def _merge_audio_files(self, input_files: List[str], output_path: Path) -> bool:
        if not input_files:
            return False
        if len(input_files) == 1:
            import shutil
            try:
                shutil.copy(input_files[0], output_path)
                return True
            except:
                return False
        try:
            list_file = self.cache_dir / "merge_list.txt"
            with open(list_file, 'w') as f:
                for file_path in input_files:
                    f.write(f"file '{Path(file_path).absolute()}'\n")
            subprocess.run([
                'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                '-i', str(list_file), '-acodec', 'libmp3lame',
                '-ab', '64k', '-ar', '44100', str(output_path)
            ], capture_output=True, timeout=120, check=True)
            try:
                os.remove(list_file)
            except:
                pass
            return output_path.exists() and output_path.stat().st_size > 5000
        except Exception as e:
            logger.error(f"Merge error: {e}")
            return False

    def _get_audio_duration(self, filepath: Path) -> float:
        try:
            result = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(filepath)
            ], capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except:
            pass
        return 0.0

    def _hash(self, text: str, length: int = 8) -> str:
        return hashlib.md5(text.encode('utf-8')).hexdigest()[:length]

    def get_stats(self) -> Dict:
        return {**self._stats, 'ffmpeg': self.ffmpeg_available, 'cache_dir': str(self.cache_dir)}
    
    def clear_cache(self, older_than_days: int = 30) -> int:
        removed = 0
        cutoff = time.time() - (older_than_days * 86400)
        for f in self.cache_dir.glob("**/*.mp3"):
            if f.stat().st_mtime < cutoff:
                try:
                    f.unlink()
                    removed += 1
                except:
                    pass
        return removed

# Singleton instance
audio_service = UnifiedAudioService()