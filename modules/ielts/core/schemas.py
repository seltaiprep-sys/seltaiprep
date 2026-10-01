"""Shared data models and schemas for all IELTS modules"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# =====================================================
# CORE / SHARED
# =====================================================
@dataclass
class BandScore:
    """IELTS band score with CEFR mapping"""
    overall_band: float = 5.5
    band_descriptor: str = ""
    criteria: Dict[str, float] = field(default_factory=dict)
    cefr_level: str = ""

    def __post_init__(self):
        if not self.cefr_level and self.overall_band >= 0:
            self.cefr_level = self._get_cefr(self.overall_band)

    @staticmethod
    def _get_cefr(band: float) -> str:
        if band >= 8.5:
            return "C2"
        elif band >= 7.0:
            return "C1"
        elif band >= 5.5:
            return "B2"
        elif band >= 4.0:
            return "B1"
        elif band >= 3.0:
            return "A2"
        return "A1"


@dataclass
class TestResult:
    """Generic test result across all modules"""
    id: Optional[int] = None
    user_id: str = "anonymous"
    test_type: str = "" # listening, reading, writing, speaking
    exam_type: str = "ielts"
    score: float = 0.0
    band_score: float = 0.0
    correct_count: int = 0
    total_questions: int = 0
    answers: Dict[str, Any] = field(default_factory=dict)
    feedback: str = ""
    session_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Question:
    """Standard question model"""
    number: int = 0
    text: str = ""
    answer: str = ""
    type: str = "text" # text, multiple_choice, true_false, matching, etc.
    options: List[str] = field(default_factory=list)
    section: int = 1
    points: float = 1.0
    explanation: str = ""


@dataclass
class AudioSegment:
    """Audio segment metadata"""
    path: str = ""
    duration: float = 0.0
    section: int = 0
    voice: str = ""
    start_time: float = 0.0
    end_time: float = 0.0


# =====================================================
# SPEAKING
# =====================================================
@dataclass
class PronunciationResult:
    """Detailed pronunciation analysis"""
    score: float = 5.0
    phoneme_score: float = 5.0
    vowel_score: float = 5.0
    stress_score: float = 5.0
    rhythm_score: float = 5.0
    intonation_score: float = 5.0
    clarity_score: float = 5.0
    issues: List[str] = field(default_factory=list)
    phoneme_details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EmotionResult:
    """Voice emotion analysis"""
    emotions: Dict[str, float] = field(default_factory=lambda: {
        'confidence': 0.5,
        'nervousness': 0.0,
        'hesitation': 0.0,
        'engagement': 0.5,
        'anxiety': 0.0
    })
    primary_emotion: str = "neutral"
    primary_emoji: str = ""
    confidence_trend: str = "stable" # improving, declining, stable


@dataclass
class GrammarResult:
    """Grammar analysis result"""
    score: float = 5.0
    errors: List[Dict[str, Any]] = field(default_factory=list)
    error_density: float = 0.0
    complexity: float = 0.0
    level: str = "adequate" # strong, adequate, developing


@dataclass
class CoherenceResult:
    """Coherence and cohesion analysis"""
    coherence_score: float = 5.0
    logical_flow: float = 5.0
    topic_relevance: Optional[float] = None
    sentence_count: int = 0
    issues: List[str] = field(default_factory=list)
    strengths: List[str] = field(default_factory=list)


@dataclass
class SpeakingResult:
    """Complete speaking test result"""
    overall_band: float = 5.5
    criteria: Dict[str, float] = field(default_factory=dict)
    text: str = ""
    word_count: int = 0
    session_id: str = ""
    processing_time: float = 0.0
    pronunciation: Optional[PronunciationResult] = None
    emotion: Optional[EmotionResult] = None
    grammar: Optional[GrammarResult] = None
    coherence: Optional[CoherenceResult] = None


# =====================================================
# LISTENING
# =====================================================
@dataclass
class ListeningSection:
    """Single listening test section"""
    section: int = 1
    title: str = ""
    type: str = "" # conversation, monologue, discussion, lecture
    speakers: List[str] = field(default_factory=list)
    context: str = ""
    audio_script: str = ""
    audio_path: str = ""
    questions: List[Question] = field(default_factory=list)
    question_count: int = 0
    duration_seconds: int = 0


@dataclass
class ListeningTest:
    """Complete listening test"""
    id: Optional[int] = None
    serial_number: int = 0
    title: str = ""
    topic: str = ""
    difficulty: str = "medium"
    accent: str = "british"
    exam_type: str = "ielts"
    sections: List[ListeningSection] = field(default_factory=list)
    audio_paths: List[str] = field(default_factory=list)
    correct_answers: Dict[str, str] = field(default_factory=dict)
    total_questions: int = 40
    duration_minutes: int = 30
    from_cache: bool = False
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Section1Scenario:
    """Section 1 conversation scenario"""
    id: str = ""
    title: str = ""
    context: str = ""
    speaker_a: str = "Customer"
    speaker_b: str = "Agent"
    fields: List[Dict[str, Any]] = field(default_factory=list)


# =====================================================
# WRITING
# =====================================================
@dataclass
class WritingTask:
    """Single writing task"""
    task_number: int = 1
    prompt: str = ""
    chart_type: str = "" # bar_chart, line_graph, etc.
    chart_data: Dict[str, Any] = field(default_factory=dict)
    word_limit: int = 150
    time_minutes: int = 20


@dataclass
class WritingCriteria:
    """Writing evaluation criteria"""
    task_achievement: float = 5.0
    task_response: float = 5.0
    coherence_cohesion: float = 5.0
    lexical_resource: float = 5.0
    grammar_accuracy: float = 5.0


@dataclass
class WritingResult:
    """Complete writing result"""
    overall_band: float = 5.5
    task1_band: float = 5.5
    task2_band: float = 5.5
    task1_criteria: Optional[WritingCriteria] = None
    task2_criteria: Optional[WritingCriteria] = None
    task1_essay: str = ""
    task2_essay: str = ""
    task1_word_count: int = 0
    task2_word_count: int = 0
    feedback: str = ""
    improvements: List[str] = field(default_factory=list)


# =====================================================
# READING
# =====================================================
@dataclass
class ReadingPassage:
    """Single reading passage"""
    passage_number: int = 1
    title: str = ""
    content: str = ""
    questions: List[Question] = field(default_factory=list)
    word_count: int = 0
    difficulty: str = "medium"
    question_count: int = 0


@dataclass
class ReadingTest:
    """Complete reading test"""
    id: Optional[int] = None
    serial_number: int = 0
    title: str = ""
    topic: str = ""
    difficulty: str = "medium"
    exam_type: str = "ielts"
    passages: List[ReadingPassage] = field(default_factory=list)
    correct_answers: Dict[str, str] = field(default_factory=dict)
    total_questions: int = 40
    time_minutes: int = 60
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    from_cache: bool = False


# =====================================================
# API RESPONSE MODELS
# =====================================================
@dataclass
class APIResponse:
    """Standard API response wrapper"""
    success: bool = True
    data: Optional[Any] = None
    error: Optional[str] = None
    message: str = ""
    processing_time: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class HealthStatus:
    """System health status"""
    status: str = "ok" # ok, degraded, down
    version: str = "2.0.0"
    gpu_available: bool = False
    models_loaded: Dict[str, bool] = field(default_factory=dict)
    cache_healthy: bool = False
    database_healthy: bool = False
    redis_healthy: bool = False
    uptime_seconds: float = 0.0