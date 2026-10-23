# modules/ukvi/generators/interview_generator.py
"""
UKVI Interview Generator — Real UKVI Pattern (7 Phases)

v3.0 — REAL UKVI PATTERN:
  • 7-phase interview structure (ID → Purpose → Course → Financial → Ties → Post-Study → Credibility)
  • 40 questions matching real UKVI order
  • Per-question time allocation
  • Phase-specific drill-downs
  • Financial questions require exact figures
  • Trick questions for credibility check
  • Profile-driven personalization
"""

import json
import re
import random
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# TTS (optional)
try:
    from ..audio.tts import UKVIVoice, TTS_AVAILABLE
except ImportError:
    TTS_AVAILABLE = False
    UKVIVoice = None


# ═══════════════════════════════════════════════════════════════════════
# REAL UKVI INTERVIEW STRUCTURE — 7 Phases (35-40 questions)
# ═══════════════════════════════════════════════════════════════════════
REAL_UKVI_PHASES = [
    # ─── Phase 1: ID & Introduction (2 questions) ───
    {
        'phase': 1,
        'name': 'ID & Introduction',
        'duration_seconds': (120, 180),
        'questions': [
            {
                'q': "Can you confirm your full name and date of birth?",
                'time_seconds': 30,
                'mandatory': True,
                'type': 'id_check',
            },
            {
                'q': "Can you tell me why you're here today?",
                'time_seconds': 30,
                'mandatory': True,
                'type': 'purpose',
            },
        ],
    },
    # ─── Phase 2: Purpose of Visit (4 questions) ───
    {
        'phase': 2,
        'name': 'Purpose of Visit',
        'duration_seconds': (180, 240),
        'questions': [
            {
                'q': "Why did you choose to study in the UK rather than your home country?",
                'time_seconds': 60,
                'type': 'comparison',
            },
            {
                'q': "Why did you choose {university} specifically over other UK universities?",
                'time_seconds': 60,
                'type': 'justification',
            },
            {
                'q': "Have you applied to any other universities in the UK or elsewhere?",
                'time_seconds': 45,
                'type': 'factual',
            },
            {
                'q': "Why did you reject those universities and choose {university}?",
                'time_seconds': 60,
                'type': 'comparison',
            },
        ],
    },
    # ─── Phase 3: Course & University (7 questions) ───
    {
        'phase': 3,
        'name': 'Course & University',
        'duration_seconds': (360, 480),
        'questions': [
            {
                'q': "What is the exact name of your course?",
                'time_seconds': 20,
                'drill_down': True,
                'type': 'factual_specific',
            },
            {
                'q': "When does your course start, and how long is it?",
                'time_seconds': 20,
                'drill_down': True,
                'type': 'factual_dates',
            },
            {
                'q': "What are the specific modules in your first semester?",
                'time_seconds': 60,
                'drill_down': True,
                'type': 'detailed_knowledge',
            },
            {
                'q': "Who is the head of your department at {university}?",
                'time_seconds': 30,
                'trick': True,
                'type': 'research_test',
            },
            {
                'q': "What is the ranking of {university} in your subject area?",
                'time_seconds': 30,
                'trick': True,
                'type': 'research_test',
            },
            {
                'q': "How did you find out about this course?",
                'time_seconds': 45,
                'type': 'factual',
            },
            {
                'q': "Have you researched the university's campus and facilities?",
                'time_seconds': 45,
                'type': 'research_depth',
            },
        ],
    },
    # ─── Phase 4: Financial (10 questions — MOST IMPORTANT) ───
    {
        'phase': 4,
        'name': 'Financial',
        'duration_seconds': (480, 600),
        'questions': [
            {
                'q': "What is the exact annual tuition fee for your course?",
                'time_seconds': 15,
                'requires_number': True,
                'type': 'specific_number',
            },
            {
                'q': "How much is your living cost per year in the UK?",
                'time_seconds': 15,
                'requires_number': True,
                'type': 'specific_number',
            },
            {
                'q': "So what is the total cost per year?",
                'time_seconds': 15,
                'requires_number': True,
                'type': 'math_check',
            },
            {
                'q': "Who is sponsoring your studies?",
                'time_seconds': 20,
                'type': 'factual',
            },
            {
                'q': "What does {sponsor} do for a living?",
                'time_seconds': 30,
                'type': 'factual',
            },
            {
                'q': "What is {sponsor}'s annual income?",
                'time_seconds': 20,
                'requires_number': True,
                'type': 'specific_number',
            },
            {
                'q': "How long has {sponsor} been in this job?",
                'time_seconds': 20,
                'requires_number': True,
                'type': 'specific_number',
            },
            {
                'q': "How much savings does {sponsor} have?",
                'time_seconds': 20,
                'requires_number': True,
                'type': 'specific_number',
            },
            {
                'q': "How will you prove {sponsor}'s income and savings?",
                'time_seconds': 45,
                'requires_evidence': True,
                'type': 'evidence',
            },
            {
                'q': "Have you already paid a tuition deposit to {university}?",
                'time_seconds': 20,
                'requires_evidence': True,
                'type': 'evidence',
            },
        ],
    },
    # ─── Phase 5: Ties to Home (6 questions) ───
    {
        'phase': 5,
        'name': 'Ties to Home Country',
        'duration_seconds': (180, 240),
        'questions': [
            {
                'q': "Do you or your family own any property in {country}?",
                'time_seconds': 30,
                'sensitive': True,
                'type': 'ties_check',
            },
            {
                'q': "Do you have a job waiting for you in {country}?",
                'time_seconds': 30,
                'sensitive': True,
                'type': 'ties_check',
            },
            {
                'q': "What does {father} do?",
                'time_seconds': 20,
                'type': 'factual',
            },
            {
                'q': "What does {mother} do?",
                'time_seconds': 20,
                'type': 'factual',
            },
            {
                'q': "Do you have siblings? Where do they live?",
                'time_seconds': 25,
                'type': 'factual',
            },
            {
                'q': "Why will you return to {country} after your studies?",
                'time_seconds': 60,
                'critical': True,
                'type': 'critical_justification',
            },
        ],
    },
    # ─── Phase 6: Post-Study Plans (5 questions) ───
    {
        'phase': 6,
        'name': 'Post-Study Plans',
        'duration_seconds': (120, 180),
        'questions': [
            {
                'q': "What are your plans after completing your course?",
                'time_seconds': 45,
                'type': 'future_plan',
            },
            {
                'q': "What job do you hope to get in {country}?",
                'time_seconds': 30,
                'type': 'future_plan',
            },
            {
                'q': "What salary do you expect in {country}?",
                'time_seconds': 20,
                'requires_number': True,
                'type': 'specific_number',
            },
            {
                'q': "Do you already have any job offer in {country}?",
                'time_seconds': 30,
                'type': 'factual',
            },
            {
                'q': "How will your UK course help your career in {country}?",
                'time_seconds': 60,
                'type': 'justification',
            },
        ],
    },
    # ─── Phase 7: Credibility Challenges (3-4 questions) ───
    {
        'phase': 7,
        'name': 'Credibility Challenges',
        'duration_seconds': (300, 480),
        'questions': [
            {
                'q': "Can you be more specific about that?",
                'time_seconds': 45,
                'trick': True,
                'dynamic': True,
                'type': 'probe',
            },
            {
                'q': "How can you prove that?",
                'time_seconds': 45,
                'trick': True,
                'type': 'evidence_request',
            },
            {
                'q': "Is there anything else you would like to add?",
                'time_seconds': 60,
                'type': 'open_ended',
            },
        ],
    },
]


# ─── Follow-up probes ─────────────────────────────────────────────
FOLLOW_UP_PROBES = [
    "Can you explain that in more detail?",
    "Are you sure about that?",
    "How can you prove that?",
    "What evidence do you have?",
    "That doesn't quite answer my question. Let me ask again.",
    "Why should I believe that?",
    "Is there anything else you want to add?",
    "Can you be more specific?",
    "Can you give me an example?",
    "What makes you say that?",
]


class UKVIInterviewGenerator:
    """Generate real UKVI interview with 7-phase pattern."""

    def __init__(self, ai_engine=None):
        self.ai = ai_engine
        self.voice = UKVIVoice() if TTS_AVAILABLE else None

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC: Generate questions (7-phase real UKVI pattern)
    # ═══════════════════════════════════════════════════════════════
    def generate_questions(
        self,
        difficulty: str = "medium",
        personalized: bool = True,
        profile: Dict = None,
        visa_type: str = "student",
    ) -> List[Dict]:
        result = self.generate_interview(
            difficulty=difficulty, visa_type=visa_type, profile=profile
        )
        if result.get('success') and 'questions' in result:
            return result['questions']
        return []

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC: Generate full interview (7-phase)
    # ═══════════════════════════════════════════════════════════════
    def generate_interview(
        self,
        difficulty: str = "medium",
        visa_type: str = "student",
        profile: Dict = None,
    ) -> Dict:
        if not profile:
            return {
                'error': 'Profile required for UKVI interview generation.',
                'success': False,
            }

        questions = self._build_real_ukvi_questions(profile, difficulty)

        if not questions:
            return {
                'error': 'Failed to build question set.',
                'success': False,
            }

        total_time = sum(q['time_seconds'] for q in questions)
        phases_summary = self._summarize_phases(questions)

        return {
            'visa_type': visa_type,
            'difficulty': difficulty,
            'total_questions': len(questions),
            'total_phases': len(phases_summary),
            'estimated_duration_minutes': total_time // 60,
            'estimated_duration_seconds': total_time,
            'questions': questions,
            'phases': phases_summary,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'has_audio': self.has_audio(),
            'ai_generated': bool(self.ai),
            'real_ukvi_pattern': True,
            'success': True,
        }

    # ═══════════════════════════════════════════════════════════════
    # BUILD: Real UKVI questions from 7-phase structure
    # ═══════════════════════════════════════════════════════════════
    def _build_real_ukvi_questions(self, profile: Dict, difficulty: str) -> List[Dict]:
        """Build questions using 7-phase real UKVI pattern."""
        tokens = self._build_tokens(profile)
        all_questions = []
        q_index = 0

        for phase_data in REAL_UKVI_PHASES:
            for q in phase_data['questions']:
                # Skip trick questions on easy difficulty
                if difficulty == 'easy' and q.get('trick'):
                    continue
                # Skip credibility phase questions on easy
                if difficulty == 'easy' and phase_data['phase'] == 7:
                    if not q.get('type') == 'open_ended':
                        continue

                text = q['q']
                # Fill tokens
                for key, val in tokens.items():
                    text = text.replace('{' + key + '}', str(val))

                q_index += 1
                all_questions.append({
                    'index': q_index,
                    'phase': phase_data['phase'],
                    'phase_name': phase_data['name'],
                    'phase_question_num': len([x for x in all_questions if x['phase'] == phase_data['phase']]) + 1,
                    'question': text,
                    'time_seconds': q['time_seconds'],
                    'mandatory': q.get('mandatory', False),
                    'drill_down': q.get('drill_down', False),
                    'trick': q.get('trick', False),
                    'sensitive': q.get('sensitive', False),
                    'critical': q.get('critical', False),
                    'requires_number': q.get('requires_number', False),
                    'requires_evidence': q.get('requires_evidence', False),
                    'type': q.get('type', 'general'),
                    'difficulty': difficulty,
                })

        return all_questions

    def _build_tokens(self, profile: Dict) -> Dict[str, str]:
        return {
            'university': profile.get('university') or 'your chosen university',
            'city': profile.get('university_city') or profile.get('city') or 'the city',
            'course': profile.get('course') or 'your course',
            'country': profile.get('home_country') or 'your home country',
            'sponsor': profile.get('sponsor_name') or 'your sponsor',
            'father': profile.get('father_name') or 'your father',
            'mother': profile.get('mother_name') or 'your mother',
        }

    def _summarize_phases(self, questions: List[Dict]) -> List[Dict]:
        """Summarize phases for progress display."""
        phase_map = {}
        for q in questions:
            p = q['phase']
            if p not in phase_map:
                phase_map[p] = {
                    'phase': p,
                    'name': q['phase_name'],
                    'question_count': 0,
                    'total_time_seconds': 0,
                }
            phase_map[p]['question_count'] += 1
            phase_map[p]['total_time_seconds'] += q['time_seconds']

        return sorted(phase_map.values(), key=lambda x: x['phase'])

    # ═══════════════════════════════════════════════════════════════
    # TTS helpers
    # ═══════════════════════════════════════════════════════════════
    def has_audio(self) -> bool:
        return self.voice is not None and TTS_AVAILABLE

    def speak_question(self, question: str, filename: str = None) -> Optional[str]:
        if not self.voice:
            return None
        if filename is None:
            filename = f"ukvi_q_{abs(hash(question)) % 10000}.mp3"
        try:
            return self.voice.speak(question, filename)
        except Exception as e:
            logger.warning(f"TTS failed: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════
    # FOLLOW-UP GENERATION
    # ═══════════════════════════════════════════════════════════════
    def generate_follow_up(
        self,
        question: str,
        answer: str,
        category: str,
        profile: Dict,
        difficulty: str,
    ) -> Optional[str]:
        if not self.ai:
            return random.choice(FOLLOW_UP_PROBES)

        answer_lower = (answer or '').lower()
        word_count = len((answer or '').split())
        trigger = False
        reason = ""

        if word_count < 10:
            trigger = True
            reason = "The answer was too short. Need more detail."
        elif category == 'financial' and not any(c.isdigit() for c in (answer or '')):
            trigger = True
            reason = "Financial answer lacks specific figures."
        elif any(phrase in answer_lower for phrase in ['i guess', 'maybe', 'not sure', 'sort of', 'probably']):
            trigger = True
            reason = "Answer appears evasive."
        elif category == 'ties_to_home' and 'return' not in answer_lower and 'back' not in answer_lower:
            trigger = True
            reason = "No mention of return to home country."

        if not trigger:
            return None

        prompt = f"""You are a UKVI credibility interview officer.

Question asked: "{question}"
Applicant's answer: "{answer}"
Reason for follow-up: {reason}

Generate ONE single follow-up question that probes deeper and demands specific details or evidence. Sound firm but professional.

Return ONLY the follow-up question as plain text. No quotes, no commentary.

Example: "Can you give me the exact annual income of your sponsor in Nepali rupees?"
"""
        try:
            resp = self.ai.generate(prompt, max_tokens=100, temperature=0.7)
            follow_up = resp.strip().strip('"').strip("'")
            if follow_up and len(follow_up) > 10:
                return follow_up
        except Exception as e:
            logger.warning(f"Follow-up generation failed: {e}")

        return random.choice(FOLLOW_UP_PROBES)

    # ═══════════════════════════════════════════════════════════════
    # Rule-based evaluation
    # ═══════════════════════════════════════════════════════════════
    def evaluate_response(self, question: str, answer: str, category: str) -> Dict:
        word_count = len((answer or '').split())
        clarity = self._score_clarity(answer)
        credibility = self._score_credibility(answer, category)
        english = self._score_english(answer)
        red_flags = self._detect_red_flags(answer, category)
        overall = round((clarity + credibility + english) / 3, 1)
        return {
            'overall_score': overall,
            'clarity': round(clarity, 1),
            'credibility': round(credibility, 1),
            'english_level': round(english, 1),
            'word_count': word_count,
            'red_flags': red_flags,
            'status': 'Strong' if overall >= 7 else 'Adequate' if overall >= 5 else 'Needs improvement',
            'feedback': self._feedback(clarity, credibility, english, red_flags),
        }

    def _score_clarity(self, answer: str) -> float:
        answer = answer or ''
        words = answer.split()
        if len(words) < 5:
            return 3.0
        if len(words) < 10:
            return 5.0
        score = 5.0
        if 'because' in answer.lower():
            score += 1.0
        if any(w in answer.lower() for w in ['for example', 'such as']):
            score += 1.0
        if any(w.isdigit() for w in words):
            score += 0.5
        return min(9.0, score)

    def _score_credibility(self, answer: str, category: str) -> float:
        answer = answer or ''
        al = answer.lower()
        weak = sum(1 for p in ['maybe', 'i guess', 'sort of', 'not sure', 'probably'] if p in al)
        strong = sum(1 for p in ['definitely', 'certainly', 'i can confirm'] if p in al)
        score = 5.0 - weak * 1.0 + strong * 0.5
        if category == 'financial' and not any(w.isdigit() for w in answer.split()):
            score -= 1.0
        if category == 'ties_to_home' and 'return' not in al:
            score -= 1.5
        return max(3.0, min(9.0, score))

    def _score_english(self, answer: str) -> float:
        answer = answer or ''
        words = answer.split()
        if len(words) < 5:
            return 3.0
        score = 6.0
        for p in [r'\b(he|she|it)\s+don\'t\b', r'\bmore\s+(better|worse)\b']:
            if re.search(p, answer, re.IGNORECASE):
                score -= 0.5
        if len(words) > 15:
            score += 0.5
        if any(w in answer.lower() for w in ['however', 'although', 'therefore']):
            score += 1.0
        return max(3.0, min(9.0, score))

    def _detect_red_flags(self, answer: str, category: str) -> List[str]:
        flags = []
        answer = answer or ''
        al = answer.lower()
        if any(m in al for m in ['actually', 'wait', 'i mean', 'what i meant']):
            flags.append("Possible inconsistency")
        if any(m in al for m in ["i don't remember", 'i forgot', 'not sure exactly']):
            flags.append("Evasive response")
        if category == 'financial' and 'enough' in al and not any(w.isdigit() for w in answer.split()):
            flags.append("Financial answer lacks specific figures")
        if category == 'ties_to_home' and ('nothing' in al or 'not really' in al):
            flags.append("Weak ties to home country - MAJOR red flag")
        return flags

    def _feedback(self, clarity, credibility, english, red_flags) -> str:
        parts = []
        if clarity < 5:
            parts.append("Be more direct and specific")
        if credibility < 5:
            parts.append("Provide specific details and evidence")
        if english < 5:
            parts.append("Work on English grammar and vocabulary")
        if red_flags:
            parts.append(f"Red flag: {red_flags[0]}")
        return ". ".join(parts) if parts else "Good response"



# ═══════════════════════════════════════════════════════════════════════
# CONSISTENCY CHECKER — Detect contradictions between answers
# ═══════════════════════════════════════════════════════════════════════

class ConsistencyChecker:
    """Detect inconsistencies in student answers across questions"""

    KEY_PATTERNS = {
        'tuition_fee': r'(?:tuition|fee|course fee)[^\d]{0,30}([£$]?\s*\d[\d,\.]*)',
        'living_cost': r'(?:living|accommodation|rent)[^\d]{0,30}([£$]?\s*\d[\d,\.]*)',
        'sponsor_income': r'(?:income|salary|earn)[^\d]{0,30}([£$]?\s*\d[\d,\.]*)',
        'savings': r'(?:savings|save|saved)[^\d]{0,30}([£$]?\s*\d[\d,\.]*)',
        'ielts_score': r'(?:ielts|band)[^\d]{0,20}(\d\.?\d?)',
        'university': r'(?:study at|chosen|attend|university of)\s+([A-Z][a-zA-Z\s]+(?:University|College))',
        'course': r'(?:study|doing|pursue|studying)\s+(?:a|an|the)?\s*(?:MSc|MA|BA|BSc|PhD|MBA)?\s*([A-Z][a-zA-Z\s]+)',
    }

    def __init__(self):
        self.answers = []

    def add_answer(self, q_index, question, answer, category='general'):
        self.answers.append({
            'index': q_index,
            'question': question,
            'answer': answer,
            'category': category,
            'entities': self._extract_entities(answer),
        })

    def _extract_entities(self, text):
        entities = {}
        text_lower = (text or '').lower()
        for key, pattern in self.KEY_PATTERNS.items():
            matches = re.findall(pattern, text_lower, re.IGNORECASE)
            if matches:
                entities[key] = str(matches[-1]).strip()
        return entities

    def check_consistency(self, new_answer, new_category='general'):
        new_entities = self._extract_entities(new_answer)
        issues = []
        for prev in self.answers:
            for key in new_entities:
                if key in prev['entities']:
                    old_val = prev['entities'][key]
                    new_val = new_entities[key]
                    if self._values_differ(old_val, new_val):
                        issues.append({
                            'key': key,
                            'old_value': old_val,
                            'new_value': new_val,
                            'old_question_index': prev['index'],
                            'old_question': prev['question'][:100],
                        })

        challenge = None
        if issues:
            issue = issues[0]
            key_human = issue['key'].replace('_', ' ')
            challenge = (
                "Earlier when I asked about your " + key_human + ", "
                "you said " + str(issue['old_value']) + ". "
                "Now you're saying " + str(issue['new_value']) + ". "
                "Can you explain that discrepancy?"
            )

        return {
            'consistent': len(issues) == 0,
            'issues': issues,
            'challenge': challenge,
        }

    def _values_differ(self, old_val, new_val):
        def normalize(v):
            v = str(v).lower().strip()
            v = v.replace(',', '').replace('£', '').replace('$', '').replace('npr', '').replace('rs', '').strip()
            if v.endswith('k'):
                try:
                    return float(v[:-1]) * 1000
                except ValueError:
                    pass
            try:
                return float(v)
            except ValueError:
                return v

        old_norm = normalize(old_val)
        new_norm = normalize(new_val)

        if isinstance(old_norm, float) and isinstance(new_norm, float):
            if old_norm == 0 and new_norm == 0:
                return False
            diff_pct = abs(old_norm - new_norm) / max(old_norm, new_norm) * 100
            return diff_pct > 5

        return str(old_norm).lower() != str(new_norm).lower()
