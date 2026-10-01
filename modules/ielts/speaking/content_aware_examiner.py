"""Content-aware examiner that adapts to student responses - Stateful & Integrated

FIX (this version):
  (1) Removed variable-length lookbehind in HESITATION_PATTERN
  (2) "like" and "you know" are counted by a context-aware helper
  (3) assess_confidence() uses word-boundary matching
  (4) _generate_redirection() truncates very long questions
  (5) generate_personalized_transition() guards against None inputs
  (6) reset() method for session reuse
  (7) analyze_response() validates/clamps part to 1/2/3
  (8) _extract_themes() filters common stopwords
  (9) Return dict includes word_count
  (10) _last_assessment preserved on empty-input fallback
  (11) NEW: generate_follow_up() method — generates Part 2 + Part 3
       based on Part 1 answers (called by /speaking/adaptive_continue)
"""

import re
import json
import random
import logging
from typing import Dict, Optional, List, Any
from collections import Counter

from .coherence import create_coherence_analyzer, CoherenceResult

logger = logging.getLogger(__name__)


# ============================================================
# Constants / Patterns
# ============================================================

HESITATION_PATTERN = re.compile(
    r'\b(um+|uh+|er+|ah+|hmm+)\b',
    re.IGNORECASE,
)

LIKE_EXCLUSION_WORDS = {
    'i', 'we', 'they', 'you', 'he', 'she', 'it',
    'would', 'do', 'does', 'did', 'will',
    "don't", "doesn't", "didn't", "won't",
    'feel', 'felt', 'feels',
    'look', 'looks', 'looked',
    'sound', 'sounds', 'sounded',
    'seem', 'seems', 'seemed',
    'am', 'is', 'are', 'was', 'were',
    'be', 'been', 'being',
    'and', 'or', 'but', 'so', 'as',
}

THEME_STOPWORDS = {
    'the', 'this', 'that', 'these', 'those', 'there', 'their', 'them', 'they',
    'have', 'has', 'had', 'having', 'with', 'from', 'what', 'when', 'where',
    'which', 'would', 'could', 'should', 'will', 'shall', 'might', 'must',
    'very', 'just', 'also', 'even', 'still', 'much', 'many', 'most', 'some',
    'other', 'another', 'such', 'than', 'then', 'into', 'onto', 'over', 'under',
    'about', 'after', 'before', 'because', 'while', 'though', 'although',
    'really', 'quite', 'actually', 'basically', 'maybe', 'perhaps', 'kind',
    'sort', 'thing', 'things', 'stuff', 'people', 'person',
}

PART_MIN_WORDS = {
    1: 8,
    2: 15,
    3: 12,
}

# Fallback topic pool for Part 2 when nothing was discussed
DEFAULT_PART2_TOPICS = [
    "a memorable experience",
    "an important person",
    "a place you enjoy",
    "a skill you learned",
    "a piece of technology you use",
    "a book you read",
    "a difficult decision",
    "a tradition in your country",
]


# ============================================================
# Context-aware hesitation counter
# ============================================================

def _count_hesitation_markers(text: str) -> int:
    """Count hesitation markers with context awareness."""
    if not text:
        return 0

    text_lower = text.lower()

    count = len(HESITATION_PATTERN.findall(text_lower))

    words = re.findall(r"\b[\w']+\b", text_lower)
    for i, w in enumerate(words):
        if w == 'like':
            prev = words[i - 1] if i > 0 else ''
            if prev not in LIKE_EXCLUSION_WORDS:
                count += 1

    count += len(re.findall(r'\byou know\b', text_lower))

    return count


class ContentAwareExaminer:
    """
    Stateful IELTS examiner that analyzes responses, adapts in real-time,
    and remembers the conversation.
    """

    def __init__(
        self,
        ai_engine: Optional[Any] = None,
        voice: Optional[Any] = None,
        coherence_analyzer: Optional[Any] = None,
        use_semantic: bool = True,
    ):
        self.ai = ai_engine
        self.voice = voice

        if coherence_analyzer is not None:
            self.coherence_analyzer = coherence_analyzer
        else:
            self.coherence_analyzer = create_coherence_analyzer(use_semantic=use_semantic)

        self.conversation_history: List[Dict] = []
        self.student_profile: Dict = self._fresh_profile()
        self._last_assessment: Dict = {}

        logger.info("ContentAwareExaminer initialized with stateful memory")

    # ============================================================
    # SESSION MANAGEMENT
    # ============================================================

    def _fresh_profile(self) -> Dict:
        return {
            'confidence': 'medium',
            'fluency': 'medium',
            'vocabulary_level': 'intermediate',
            'common_themes': [],
            'avg_response_length': 0,
            'total_responses': 0,
            'hesitation_trend': 0,
        }

    def reset(self) -> None:
        self.conversation_history = []
        self.student_profile = self._fresh_profile()
        self._last_assessment = {}
        logger.info("ContentAwareExaminer state reset")

    # ============================================================
    # PRIMARY PUBLIC METHOD
    # ============================================================

    def analyze_response(self, transcript: str, question: str, part: int) -> Dict:
        # ---- 0. Normalize ----
        transcript = (transcript or "").strip()
        question = (question or "").strip()

        try:
            part = int(part)
        except (TypeError, ValueError):
            part = 1
        if part not in (1, 2, 3):
            part = 1

        if not transcript:
            return self._fallback_response("empty_response")

        word_count = len(transcript.split())

        # ---- 1. Coherence analysis ----
        try:
            coherence_result = self.coherence_analyzer.analyze(transcript, topic=question)
            details = coherence_result.details if hasattr(coherence_result, 'details') else {}
            topical_score = float(details.get('topical_relevance', 5.0))
            coherence_score = float(getattr(coherence_result, 'score', 0.0))
        except Exception as e:
            logger.warning(f"Coherence analysis failed: {e}")
            topical_score = 5.0
            coherence_score = 0.0

        # ---- 2. Hesitation count ----
        hesitation_count = _count_hesitation_markers(transcript)

        # ---- 3. Confidence assessment ----
        confidence = self.assess_confidence(transcript)

        # ---- 4. Themes extraction ----
        themes = self._extract_themes(transcript)

        # ---- 5. Decision tree ----
        min_words = PART_MIN_WORDS.get(part, 10)

        if topical_score < 4.0 and word_count > 10:
            action = 'redirect'
            reason = 'off_topic'
            follow_up = self._generate_redirection(question)
            assessment = {
                'length': 'adequate' if word_count > 10 else 'short',
                'confidence': confidence,
                'relevance': 'off_topic',
                'coherence_band': coherence_score,
                'topical_score': topical_score,
            }
        elif word_count < min_words:
            action = 'probe'
            reason = 'too_short'
            follow_up = self._generate_expansion_probe(question, transcript, part=part)
            assessment = {
                'length': 'very_short',
                'confidence': confidence,
                'relevance': 'on_topic' if topical_score >= 5.0 else 'marginal',
                'coherence_band': coherence_score,
                'topical_score': topical_score,
            }
        elif hesitation_count > 3 or (word_count > 0 and hesitation_count / word_count > 0.15):
            action = 'encourage'
            reason = 'hesitation'
            follow_up = random.choice([
                "Take your time, there's no rush.",
                "Don't worry about pauses—just speak naturally.",
                "You're doing fine, continue when you're ready.",
                "It's okay to think aloud. Just keep going.",
            ])
            assessment = {
                'length': 'good' if word_count > 30 else 'adequate',
                'confidence': confidence,
                'relevance': 'on_topic' if topical_score >= 5.0 else 'marginal',
                'hesitation_markers': hesitation_count,
                'coherence_band': coherence_score,
            }
        elif part == 3 and word_count > 30 and coherence_score >= 6.0:
            action = 'deepen'
            reason = 'good_answer'
            follow_up = self._generate_content_probe(transcript, question, part=part)
            assessment = {
                'length': 'good',
                'confidence': confidence,
                'relevance': 'on_topic',
                'coherence_band': coherence_score,
                'topical_score': topical_score,
            }
        else:
            action = 'move_on'
            reason = 'adequate'
            follow_up = None
            assessment = {
                'length': 'adequate' if word_count > 10 else 'short',
                'confidence': confidence,
                'relevance': 'on_topic' if topical_score >= 5.0 else 'marginal',
                'coherence_band': coherence_score,
                'topical_score': topical_score,
            }

        # ---- 6. Update state ----
        self._update_state(
            transcript, question, part, action, assessment,
            themes, hesitation_count,
        )
        self._last_assessment = assessment

        return {
            'action': action,
            'reason': reason,
            'follow_up': follow_up,
            'word_count': word_count,
            'assessment': assessment,
        }

    # ============================================================
    # NEW: ADAPTIVE FOLLOW-UP GENERATION
    # Used by POST /speaking/adaptive_continue
    # ============================================================

    def generate_follow_up(
        self,
        answers: List[Dict],
        difficulty: str = "medium",
    ) -> Dict:
        """
        Given Part 1 answers, generate a Part 2 cue card and Part 3 questions
        that extend the student's discussed themes.

        Args:
            answers: List of dicts with {'question': str, 'answer': str}
            difficulty: 'easy' | 'medium' | 'hard'

        Returns:
            {
                'part2': {
                    'topic_card': {'title': str, 'prompts': [str, ...]},
                    'examiner_follow_up': str
                },
                'part3': {
                    'questions': [{'question': str, 'type': str}, ...]
                },
                'topic': str
            }
            or {} on failure (caller falls back to defaults).
        """
        if not isinstance(answers, list):
            answers = []

        # ---- Extract themes from Part 1 answers ----
        combined_text = " ".join(
            (a.get('answer') or '')
            for a in answers
            if isinstance(a, dict)
        )
        themes = self._extract_themes(combined_text) if combined_text else []
        topic_context = themes[0] if themes else None

        if not topic_context:
            topic_context = random.choice(DEFAULT_PART2_TOPICS)

        # ---- No AI → return {} so caller uses fallback ----
        if not self.ai:
            return {}

        # ---- Build the prompt ----
        prompt = f"""You are an IELTS Speaking examiner. The student just finished Part 1 and discussed: {topic_context}.

Generate Part 2 (a long turn) and Part 3 (abstract discussion) questions that extend this theme.

Difficulty: {difficulty}

Return ONLY valid JSON with this EXACT structure:
{{
    "part2": {{
        "topic_card": {{
            "title": "Describe [something related to '{topic_context}']",
            "prompts": [
                "what it is",
                "when it happened",
                "what happened",
                "and explain why it was memorable"
            ]
        }},
        "examiner_follow_up": "Just one quick question about that"
    }},
    "part3": {{
        "questions": [
            {{"question": "Analytical question 1 about {topic_context}", "type": "analysis"}},
            {{"question": "Comparison question 2 about past vs present", "type": "comparison"}},
            {{"question": "Evaluation question 3 about advantages/disadvantages", "type": "evaluation"}},
            {{"question": "Prediction question 4 about the future", "type": "prediction"}}
        ]
    }},
    "topic": "{topic_context}"
}}

Generate realistic, natural questions. Return ONLY valid JSON."""

        try:
            response = self.ai.generate(prompt, max_tokens=800, temperature=0.8)
            match = re.search(r'\{[\s\S]*\}', response or '', re.DOTALL)
            if not match:
                logger.warning("generate_follow_up: no JSON found in AI response")
                return {}

            parsed = json.loads(match.group())
            if not isinstance(parsed, dict):
                return {}
            if not parsed.get('part2') or not parsed.get('part3'):
                logger.warning("generate_follow_up: AI response missing part2/part3")
                return {}

            parsed.setdefault('topic', topic_context)

            # ---- Validate part2 structure ----
            part2 = parsed.get('part2') or {}
            topic_card = part2.get('topic_card') or {}
            if not topic_card.get('title') or not topic_card.get('prompts'):
                logger.warning("generate_follow_up: part2 topic_card incomplete")
                return {}

            # ---- Validate part3 structure ----
            part3 = parsed.get('part3') or {}
            if not isinstance(part3.get('questions'), list) or not part3['questions']:
                logger.warning("generate_follow_up: part3 has no questions")
                return {}

            logger.info(
                f"generate_follow_up OK: topic='{topic_context}', "
                f"part2='{topic_card.get('title', '')[:40]}', "
                f"{len(part3['questions'])} part3 questions"
            )
            return parsed

        except Exception as e:
            logger.warning(f"generate_follow_up failed: {e}")
            return {}

    # ============================================================
    # STATE MANAGEMENT
    # ============================================================

    def _update_state(
        self,
        transcript: str,
        question: str,
        part: int,
        action: str,
        assessment: Dict,
        themes: List[str],
        hesitation_count: int,
    ) -> None:
        word_count = len(transcript.split())

        self.conversation_history.append({
            'timestamp': len(self.conversation_history),
            'question': question,
            'transcript': transcript,
            'part': part,
            'action': action,
            'assessment': assessment,
            'themes': themes or [],
            'hesitation_count': hesitation_count,
            'word_count': word_count,
        })

        total = len(self.conversation_history)
        if total == 1:
            self.student_profile['total_responses'] = 1
            self.student_profile['avg_response_length'] = word_count
        else:
            prev_avg = self.student_profile.get('avg_response_length', 0)
            n = self.student_profile.get('total_responses', 0)
            new_avg = (prev_avg * n + word_count) / (n + 1)
            self.student_profile['avg_response_length'] = round(new_avg, 1)
            self.student_profile['total_responses'] = n + 1

        recent_confidences = [
            h['assessment'].get('confidence', 'medium')
            for h in self.conversation_history[-3:]
            if 'assessment' in h
        ]
        if recent_confidences:
            if all(c == 'high' for c in recent_confidences):
                self.student_profile['confidence'] = 'high'
            elif any(c == 'low' for c in recent_confidences):
                self.student_profile['confidence'] = 'low'
            else:
                self.student_profile['confidence'] = 'medium'

        recent_hesitations = [
            h.get('hesitation_count', 0)
            for h in self.conversation_history[-3:]
        ]
        if recent_hesitations:
            avg_hes = sum(recent_hesitations) / len(recent_hesitations)
            self.student_profile['hesitation_trend'] = round(avg_hes, 1)
            if avg_hes > 3:
                self.student_profile['fluency'] = 'low'
            elif avg_hes > 1:
                self.student_profile['fluency'] = 'medium'
            else:
                self.student_profile['fluency'] = 'high'

        all_themes = []
        for h in self.conversation_history:
            all_themes.extend(h.get('themes', []))
        theme_counts = Counter(all_themes)
        self.student_profile['common_themes'] = [
            t for t, _ in theme_counts.most_common(5)
        ]

    def _extract_themes(self, transcript: str) -> List[str]:
        if not transcript:
            return []

        proper = re.findall(r'\b[A-Z][a-z]{3,}\b', transcript)

        possessive = re.findall(
            r'\b(?:my|our|the|this|that)\s+([a-z]{4,})\b',
            transcript.lower(),
        )

        all_words = re.findall(r'\b[a-z]{4,}\b', transcript.lower())
        common = [
            w for w, c in Counter(all_words).most_common(5)
            if c > 1 and w not in THEME_STOPWORDS
        ]

        seen = set()
        themes: List[str] = []
        for w in proper + possessive + common:
            key = w.lower()
            if key in THEME_STOPWORDS or key in seen:
                continue
            seen.add(key)
            themes.append(w)
            if len(themes) >= 3:
                break

        return themes

    # ============================================================
    # PROBE GENERATORS
    # ============================================================

    def _generate_expansion_probe(
        self,
        question: str,
        partial_answer: str,
        part: int = 1,
    ) -> str:
        if part == 1:
            probes = [
                "Can you tell me more about that?",
                "Why is that important to you?",
                "Can you give me a specific example from your own experience?",
                "How does that affect your daily life?",
                "What exactly do you mean by that?",
            ]
        else:
            probes = [
                "Can you elaborate on that point?",
                "Why do you think that is the case?",
                "Can you give me a concrete example?",
                "What might be the reasons behind that?",
                "How does that compare to other situations?",
            ]

        if self.ai:
            try:
                part_context = (
                    "personal and familiar topics"
                    if part == 1
                    else "abstract discussions, society, and trends"
                )
                prompt = (
                    f"Student gave a very short answer to this IELTS Part {part} question:\n"
                    f"Question: {question}\n"
                    f"Short answer: {partial_answer}\n\n"
                    f"This is Part {part}, which focuses on {part_context}.\n"
                    f"Generate ONE natural follow-up question to get the student to expand "
                    f"their answer. Make it specific to what they said, not generic.\n"
                    f"Return just the question, nothing else."
                )
                response = self.ai.generate(prompt, max_tokens=100, temperature=0.7)
                if response and len(response.strip()) > 10:
                    return response.strip()
            except Exception as e:
                logger.warning(f"AI probe generation failed: {e}")

        return random.choice(probes)

    def _generate_content_probe(
        self,
        transcript: str,
        question: str,
        part: int = 3,
    ) -> str:
        if self.ai:
            try:
                if part == 1:
                    style = (
                        "personal experience, feelings, and daily routines. "
                        "Sound warm and conversational."
                    )
                else:
                    style = (
                        "abstract implications, societal trends, and future predictions. "
                        "Sound academic and probing."
                    )
                prompt = (
                    f"Based on this IELTS Part {part} student response:\n"
                    f"Question: {question}\n"
                    f"Student's answer: {transcript[:400]}...\n\n"
                    f"This is Part {part}, so focus on {style}\n\n"
                    f"Generate ONE natural follow-up question that:\n"
                    f"1. References something specific the student said\n"
                    f"2. Challenges or extends their point\n"
                    f"3. Sounds like a real IELTS examiner\n\n"
                    f"Return just the question, nothing else."
                )
                response = self.ai.generate(prompt, max_tokens=150, temperature=0.8)
                if response and len(response.strip()) > 10:
                    return response.strip()
            except Exception as e:
                logger.warning(f"AI content probe failed: {e}")

        if part == 1:
            return random.choice([
                "That's interesting—tell me more about that.",
                "How does that make you feel?",
                "Is that something you do often?",
            ])
        return random.choice([
            "Can you elaborate on that point?",
            "Why do you think that is the case?",
            "How does that compare to other countries or cultures?",
            "What implications does that have for the future?",
        ])

    def _generate_redirection(self, original_question: str) -> str:
        q = (original_question or "").strip()
        if not q:
            return "Let's come back to the question — could you try answering it directly?"

        if len(q) > 120:
            short_q = q[:120].rsplit(' ', 1)[0] + "..."
        else:
            short_q = q

        redirects = [
            f"That's interesting, but let's focus on the question: {short_q}",
            f"I'd like to hear your thoughts specifically about this: {short_q}",
            f"Let's return to the question: {short_q}",
            f"Good point, but can you answer this directly: {short_q}",
        ]
        return random.choice(redirects)

    def _fallback_response(self, reason: str) -> Dict:
        return {
            'action': 'probe',
            'reason': reason,
            'follow_up': "Could you please say that again? I didn't quite catch that.",
            'word_count': 0,
            'assessment': {
                'length': 'empty',
                'confidence': 'low',
                'relevance': 'unknown',
            },
        }

    # ============================================================
    # TRANSITIONS & CLOSING
    # ============================================================

    def generate_personalized_transition(
        self,
        prev_topic: Optional[str] = None,
        prev_answer: Optional[str] = None,
        next_topic: Optional[str] = None,
    ) -> str:
        prev_topic = (prev_topic or "").strip()
        prev_answer = (prev_answer or "").strip()
        next_topic = (next_topic or "").strip() or "the next topic"

        if not prev_answer and self.conversation_history:
            last = self.conversation_history[-1]
            prev_answer = last.get('transcript', '')
            prev_topic = prev_topic or last.get('question', '')

        if self.ai and prev_answer and len(prev_answer) > 10:
            try:
                prompt = (
                    f"The student just talked about {prev_topic or 'a topic'} and said: "
                    f"\"{prev_answer[:200]}...\"\n"
                    f"Now transition to a new topic: {next_topic}\n\n"
                    f"Generate ONE natural transition sentence (like a real IELTS examiner).\n"
                    f"Optionally reference what they said, but smoothly move to the new topic.\n\n"
                    f"Return just the transition sentence."
                )
                response = self.ai.generate(prompt, max_tokens=100, temperature=0.7)
                if response and len(response.strip()) > 10:
                    return response.strip()
            except Exception as e:
                logger.warning(f"AI transition failed: {e}")

        nice_topic = next_topic.replace('_', ' ').title()
        return f"Now, let's talk about {nice_topic}."

    def generate_closing_remark(self) -> str:
        if self.ai and len(self.conversation_history) >= 2:
            try:
                themes = self.student_profile.get('common_themes', [])
                theme_str = ', '.join(themes[:3]) if themes else 'several interesting ideas'

                prompt = (
                    f"The IELTS Speaking test is ending. The student discussed: {theme_str}.\n"
                    f"Generate a warm, natural closing remark (1 sentence) that references "
                    f"the conversation. Must end with: "
                    f"\"That is the end of the speaking test. Thank you.\"\n\n"
                    f"Return just the closing remark."
                )
                response = self.ai.generate(prompt, max_tokens=100, temperature=0.7)
                if response and len(response.strip()) > 20:
                    return response.strip()
            except Exception as e:
                logger.warning(f"AI closing failed: {e}")

        return "Thank you. That is the end of the speaking test."

    # ============================================================
    # ASSESSMENT HELPERS
    # ============================================================

    def assess_confidence(self, transcript: str) -> str:
        if not transcript:
            return 'low'

        text = transcript.lower()

        patterns = {
            'high': [
                r'\bdefinitely\b', r'\bcertainly\b', r'\babsolutely\b',
                r'\bi believe\b', r'\bin my opinion\b', r'\bclearly\b',
                r'\bconfident\b', r'\bstrongly\b',
            ],
            'medium': [
                r'\bi think\b', r'\bprobably\b', r'\bmaybe\b', r'\bperhaps\b',
                r'\bi guess\b', r'\bmight\b', r'\bit seems\b',
            ],
            'low': [
                r'\bum+\b', r'\buh+\b', r'\ber+\b', r'\bah+\b',
                r"\bi don't know\b", r'\bsort of\b', r'\bkind of\b',
                r'\bmaybe not\b', r"\bi'm not sure\b",
            ],
        }

        scores = {}
        for level, pats in patterns.items():
            scores[level] = sum(
                1 for pat in pats if re.search(pat, text)
            )

        word_count = len(transcript.split())
        if word_count > 40:
            scores['high'] += 1
        elif word_count < 10:
            scores['low'] += 1

        if scores['high'] > scores['low']:
            return 'high'
        if scores['low'] > scores['high']:
            return 'low'
        return 'medium'

    def get_summary(self) -> Dict:
        return {
            'total_responses': len(self.conversation_history),
            'profile': self.student_profile,
            'last_assessment': self._last_assessment,
            'themes_discussed': self.student_profile.get('common_themes', []),
            'average_word_count': self.student_profile.get('avg_response_length', 0),
        }

    def get_short_history(self, n: int = 3) -> List[Dict]:
        return list(self.conversation_history[-max(1, n):])


__all__ = [
    'ContentAwareExaminer',
    'HESITATION_PATTERN',
    '_count_hesitation_markers',
    'LIKE_EXCLUSION_WORDS',
    'PART_MIN_WORDS',
    'DEFAULT_PART2_TOPICS',
]