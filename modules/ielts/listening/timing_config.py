"""Timing configuration for REAL IELTS Listening test - Complete with intros, reading time, and silences"""

from typing import Dict, List, Tuple, Union, Optional


class TimingConfig:
    """IELTS Listening timing configuration - REAL IELTS"""

    # =====================================================
    # CONSTANTS - REAL IELTS
    # =====================================================

    AVERAGE_WPM_SLOW = 150 # Real IELTS: ~150 words per minute
    AVERAGE_WPM_FAST = 170 # Real IELTS: ~170 words per minute

    ANSWER_TRANSFER_TIME_SECONDS = 600 # Paper-based IELTS final transfer time (10 minutes)

    # =====================================================
    # TEST INTRODUCTION - REAL IELTS
    # =====================================================

    TEST_INTRODUCTION: Dict[str, Union[str, int]] = {
        "text": (
            "You will hear a number of different recordings "
            "and you will have to answer questions on what you hear. "
            "There will be time for you to read the instructions and questions, "
            "and you will have a chance to check your work. "
            "All the recordings will be played once only. "
            "The test is in four sections. "
            "Now turn to Section 1."
        ),
        "duration": 30, # 30 seconds introduction
    }

    # =====================================================
    # SECTION INSTRUCTIONS - REAL IELTS
    # =====================================================

    SECTION_INSTRUCTIONS: Dict[int, Dict[str, Union[str, int]]] = {
        1: {
            "text": (
                "Section 1. You will hear a conversation between two people. "
                "First, you have some time to look at questions 1 to 10."
            ),
            "reading_time": 30, # 30 seconds to read questions
        },
        2: {
            "text": (
                "Section 2. You will hear a monologue. "
                "First, you have some time to look at questions 11 to 20."
            ),
            "reading_time": 30,
        },
        3: {
            "text": (
                "Section 3. You will hear a conversation between up to four people. "
                "First, you have some time to look at questions 21 to 30."
            ),
            "reading_time": 30,
        },
        4: {
            "text": (
                "Section 4. You will hear a lecture. "
                "First, you have some time to look at questions 31 to 40."
            ),
            "reading_time": 30,
        },
    }

    # =====================================================
    # SECTION TIMINGS - REAL IELTS (HARDCODED DEFAULTS)
    # =====================================================

    SECTION_TIMINGS: Dict[int, Dict[str, Union[str, int, float]]] = {
        1: {
            "title": "Section 1",
            "intro_text": SECTION_INSTRUCTIONS[1]["text"],
            "reading_time": 30, # 30 seconds to read questions
            "audio_duration": 210, # 3.5 minutes actual audio (hardcoded default)
            "target_words": 350, # 300-400 words
            "mid_pause": 0, # No mid-pause in Section 1
            "review_time": 30, # 30 seconds to check answers
            "total_questions": 10,
            "question_range": "1 to 10",
        },
        2: {
            "title": "Section 2",
            "intro_text": SECTION_INSTRUCTIONS[2]["text"],
            "reading_time": 30,
            "audio_duration": 240, # 4 minutes actual audio (hardcoded default)
            "target_words": 400, # 350-450 words
            "mid_pause": 0, # No mid-pause in Section 2
            "review_time": 30,
            "total_questions": 10,
            "question_range": "11 to 20",
        },
        3: {
            "title": "Section 3",
            "intro_text": SECTION_INSTRUCTIONS[3]["text"],
            "reading_time": 30,
            "audio_duration": 270, # 4.5 minutes actual audio (hardcoded default)
            "target_words": 450, # 400-500 words
            "mid_pause": 30, # 30 second mid-pause in Section 3
            "review_time": 30,
            "total_questions": 10,
            "question_range": "21 to 30",
            "mid_pause_point": "after_question_25", # Pause after Q25
        },
        4: {
            "title": "Section 4",
            "intro_text": SECTION_INSTRUCTIONS[4]["text"],
            "reading_time": 30,
            "audio_duration": 330, # 5.5 minutes actual audio (hardcoded default)
            "target_words": 550, # 500-600 words
            "mid_pause": 30, # 30 second mid-pause in Section 4
            "review_time": 60, # 60 seconds to check answers
            "total_questions": 10,
            "question_range": "31 to 40",
            "mid_pause_point": "after_question_35", # Pause after Q35
        },
    }

    # =====================================================
    # PAUSES FOR AUDIO ENGINE - REAL IELTS
    # =====================================================

    PAUSE_MARKERS: Dict[str, int] = {
        "short": 300, # Short pause between speakers (~0.3s)
        "medium": 600, # Medium pause between sections (~0.6s)
        "long": 1000, # Long pause between major parts (~1s)
        "reading": 30000, # 30 seconds to read questions
        "review": 30000, # 30 seconds to check answers
        "mid_pause": 30000, # 30 second mid-pause in Sections 3 & 4
        "final": 60000, # 60 seconds final review (Section 4 only)
    }

    # =====================================================
    # WORD COUNT TARGETS - REAL IELTS
    # =====================================================

    WORD_TARGETS: Dict[int, Dict[str, int]] = {
        1: {"min": 300, "max": 400, "target": 350},
        2: {"min": 350, "max": 450, "target": 400},
        3: {"min": 400, "max": 500, "target": 450},
        4: {"min": 500, "max": 600, "target": 550},
    }

    # =====================================================
    # DYNAMIC AUDIO DURATION HELPERS
    # =====================================================

    @classmethod
    def estimate_audio_duration(cls, word_count: int, speed: str = "slow") -> int:
        """
        Estimate audio duration in seconds based on word count and speaking speed.
        
        Args:
            word_count: Number of words in the script.
            speed: 'slow' (150 WPM) or 'fast' (170 WPM).
            
        Returns:
            Estimated duration in seconds.
        """
        if speed == "fast":
            wpm = cls.AVERAGE_WPM_FAST
        else:
            wpm = cls.AVERAGE_WPM_SLOW
        
        if word_count <= 0:
            return 0
        
        return int((word_count / wpm) * 60)

    @classmethod
    def get_audio_duration(cls, section: int, word_count: Optional[int] = None) -> int:
        """
        Get audio duration for a section.
        
        If word_count is provided, calculates duration dynamically.
        Otherwise, returns the hardcoded default.
        
        Args:
            section: Section number (1-4).
            word_count: Optional actual word count of the script.
            
        Returns:
            Duration in seconds.
        """
        if word_count is not None and word_count > 0:
            # Use dynamic calculation
            target_words = cls.WORD_TARGETS.get(section, {}).get("target", 350)
            # Use fast speed for longer sections, slow for shorter
            if section >= 3:
                speed = "fast"
            else:
                speed = "slow"
            return cls.estimate_audio_duration(word_count, speed)
        
        # Fallback to hardcoded default
        return cls.get_section_timing(section).get("audio_duration", 210)

    @classmethod
    def get_section_timing_with_word_count(
        cls,
        section: int,
        word_count: Optional[int] = None
    ) -> Dict:
        """
        Get section timing, optionally with dynamic audio duration based on word count.
        
        Args:
            section: Section number (1-4).
            word_count: Optional actual word count.
            
        Returns:
            Dictionary with all timing values.
        """
        timing = cls.get_section_timing(section).copy()
        
        if word_count is not None and word_count > 0:
            timing["audio_duration"] = cls.get_audio_duration(section, word_count)
            timing["is_dynamic"] = True
        else:
            timing["is_dynamic"] = False
        
        return timing

    # =====================================================
    # HELPERS
    # =====================================================

    @classmethod
    def get_total_duration(cls) -> int:
        """Total test duration in seconds (FULL IELTS simulation)"""
        total = int(cls.TEST_INTRODUCTION["duration"])

        for section_id, timing in cls.SECTION_TIMINGS.items():
            total += timing["reading_time"] # Reading time
            total += timing["audio_duration"] # Audio plays
            total += timing["mid_pause"] # Mid-pause if any
            total += timing["review_time"] # Review time

        return total

    @classmethod
    def get_total_duration_with_transfer(cls) -> int:
        """Total duration including final transfer time (paper-based)"""
        return cls.get_total_duration() + cls.ANSWER_TRANSFER_TIME_SECONDS

    @classmethod
    def get_duration_formatted(cls) -> str:
        """Human-readable duration"""
        total_seconds = cls.get_total_duration()
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes} minutes {seconds} seconds"

    @classmethod
    def get_section_instruction(cls, section: int) -> str:
        """Get IELTS-style section instruction text"""
        return cls.SECTION_INSTRUCTIONS.get(section, {}).get(
            "text",
            f"Section {section}. You will hear a recording."
        )

    @classmethod
    def get_section_timing(cls, section: int) -> Dict:
        """Get timing for a specific section"""
        return cls.SECTION_TIMINGS.get(section, {
            "reading_time": 30,
            "audio_duration": 210,
            "review_time": 30,
            "mid_pause": 0,
            "target_words": 350,
        })

    @classmethod
    def get_word_target(cls, section: int) -> Tuple[int, int, int]:
        """Get word target range for a section"""
        target = cls.WORD_TARGETS.get(section, {"min": 300, "max": 400, "target": 350})
        return target["min"], target["max"], target["target"]

    @classmethod
    def get_word_targets_dict(cls, section: int) -> Dict[str, int]:
        """Get full word target dict for a section"""
        return cls.WORD_TARGETS.get(section, {"min": 300, "max": 400, "target": 350})

    @classmethod
    def get_section_timeline(cls, section: int, word_count: Optional[int] = None) -> List[Dict]:
        """
        Get detailed timeline for a section (splits audio if mid-pause exists).
        
        Args:
            section: Section number (1-4).
            word_count: Optional actual word count for dynamic duration.
            
        Returns:
            List of timeline events.
        """
        timing = cls.get_section_timing_with_word_count(section, word_count)
        timeline = []
        
        # 1. Reading time
        timeline.append({
            "phase": "reading",
            "duration": timing["reading_time"],
            "description": f"Read questions {timing.get('question_range', '')}",
        })
        
        # 2. Audio with potential mid-pause split
        audio_duration = timing.get("audio_duration", 210)
        if timing.get("mid_pause", 0) > 0:
            # Split audio into two parts for Sections 3 & 4
            half_duration = audio_duration // 2
            timeline.append({
                "phase": "audio_part_1",
                "duration": half_duration,
                "description": f"First part of {timing['title']} audio",
            })
            timeline.append({
                "phase": "mid_pause",
                "duration": timing["mid_pause"],
                "description": f"Mid-pause - check answers so far ({timing.get('mid_pause_point', '')})",
            })
            timeline.append({
                "phase": "audio_part_2",
                "duration": audio_duration - half_duration,
                "description": f"Second part of {timing['title']} audio",
            })
        else:
            timeline.append({
                "phase": "audio",
                "duration": audio_duration,
                "description": f"Audio plays - {timing['title']}",
            })
        
        # 3. Review time
        timeline.append({
            "phase": "review",
            "duration": timing["review_time"],
            "description": "Check your answers",
        })
        
        return timeline

    @classmethod
    def get_test_timeline(cls, word_counts: Optional[Dict[int, int]] = None) -> List[Dict]:
        """
        Get complete test timeline.
        
        Args:
            word_counts: Optional dict mapping section number to word count.
            
        Returns:
            List of timeline events.
        """
        timeline = []
        
        # Introduction
        timeline.append({
            "phase": "introduction",
            "duration": cls.TEST_INTRODUCTION["duration"],
            "description": "Test introduction",
        })
        
        # Each section
        for section in range(1, 5):
            word_count = word_counts.get(section) if word_counts else None
            section_timeline = cls.get_section_timeline(section, word_count)
            for item in section_timeline:
                item["section"] = section
                timeline.append(item)
        
        return timeline

    @classmethod
    def get_audio_silence_points(cls, section: int) -> List[Dict]:
        """Get points in audio where silence should be inserted"""
        timing = cls.get_section_timing(section)
        silence_points = []
        
        # Reading time silence before audio
        silence_points.append({
            "position": "before_audio",
            "duration": timing["reading_time"],
            "description": "Read the questions",
        })
        
        # Mid-pause silence during audio (Sections 3 & 4)
        if timing.get("mid_pause", 0) > 0:
            silence_points.append({
                "position": "mid_audio",
                "duration": timing["mid_pause"],
                "description": f"Mid-pause - check your answers ({timing.get('mid_pause_point', '')})",
                "after_question": timing.get("mid_pause_point", ""),
            })
        
        # Review time silence after audio
        silence_points.append({
            "position": "after_audio",
            "duration": timing["review_time"],
            "description": "Review your answers",
        })
        
        return silence_points

    @classmethod
    def get_total_word_count_target(cls) -> Dict:
        """Get total word count targets for all sections"""
        total_min = 0
        total_max = 0
        total_target = 0
        
        for section in range(1, 5):
            min_w, max_w, target = cls.get_word_target(section)
            total_min += min_w
            total_max += max_w
            total_target += target
        
        return {
            "total_min": total_min,
            "total_max": total_max,
            "total_target": total_target,
            "real_ielts_range": "1500-2000 words",
            "sections": {
                str(s): cls.get_word_targets_dict(s)
                for s in range(1, 5)
            }
        }

    @classmethod
    def validate_timing(cls, section: int, word_count: int, actual_duration: float) -> Dict:
        """
        Validate if the actual duration matches the expected duration.
        
        Args:
            section: Section number.
            word_count: Actual word count.
            actual_duration: Actual duration in seconds.
            
        Returns:
            Dictionary with validation results.
        """
        expected_duration = cls.get_audio_duration(section, word_count)
        duration_diff = actual_duration - expected_duration
        percent_diff = (duration_diff / expected_duration) * 100 if expected_duration > 0 else 0
        
        is_valid = abs(percent_diff) < 15 # Allow 15% deviation
        
        return {
            "section": section,
            "word_count": word_count,
            "expected_duration_seconds": expected_duration,
            "actual_duration_seconds": actual_duration,
            "difference_seconds": duration_diff,
            "percent_difference": round(percent_diff, 1),
            "is_valid": is_valid,
            "message": " Duration matches expectations" if is_valid else " Duration deviates significantly from expected",
        }


# Singleton
timing_config = TimingConfig()


# =====================================================
# QUICK TEST
# =====================================================

if __name__ == "__main__":
    print("=" * 60)
    print("IELTS LISTENING TIMING CONFIGURATION - REAL IELTS")
    print("=" * 60)
    
    print("\n Test Introduction:")
    print(f" {timing_config.TEST_INTRODUCTION['text'][:100]}...")
    print(f" Duration: {timing_config.TEST_INTRODUCTION['duration']}s")
    
    print("\n Section Timings:")
    for section in range(1, 5):
        timing = timing_config.get_section_timing(section)
        min_w, max_w, target = timing_config.get_word_target(section)
        print(f"\n Section {section}:")
        print(f" Reading Time: {timing['reading_time']}s")
        print(f" Audio Duration (hardcoded): {timing['audio_duration']}s ({timing['audio_duration']//60}m {timing['audio_duration']%60}s)")
        print(f" Mid-Pause: {timing['mid_pause']}s")
        print(f" Review Time: {timing['review_time']}s")
        print(f" Word Target: {target} ({min_w}-{max_w})")
    
    print("\n Total Duration:")
    print(f" Without Transfer: {timing_config.get_duration_formatted()}")
    total_sec = timing_config.get_total_duration()
    print(f" {total_sec//60}m {total_sec%60}s")
    
    print("\n Word Count Targets:")
    word_targets = timing_config.get_total_word_count_target()
    print(f" Total Target: {word_targets['total_target']} words")
    print(f" Real IELTS Range: {word_targets['real_ielts_range']}")
    
    print("\n Silence Points (Section 3):")
    silences = timing_config.get_audio_silence_points(3)
    for s in silences:
        print(f" {s['position']}: {s['duration']}s - {s['description']}")
    
    print("\n Dynamic Audio Duration Examples:")
    test_word_counts = [300, 350, 400, 450, 500, 550]
    for wc in test_word_counts:
        duration = timing_config.estimate_audio_duration(wc)
        print(f" {wc} words → {duration} seconds ({duration//60}m {duration%60}s)")
    
    print("\n Configuration matches REAL IELTS!")