"""Business logic service layer for IELTS Listening – FULLY INTEGRATED AND MODULE‑AWARE"""

import logging
import random
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

from .test_generator import ListeningTestGenerator
from .scoring import ListeningScoring
from .repository import create_listening_repository

# Topic Registry (if available)
try:
    from .topics import TopicRegistry
    TOPICS_AVAILABLE = True
except ImportError:
    TopicRegistry = None
    TOPICS_AVAILABLE = False
    logging.warning(" topic_registry not available - using fallback topics")

logger = logging.getLogger(__name__)


class ListeningService:
    """
    Full service layer for Listening module – now module‑aware.
    Manages test generation, retrieval, scoring, and session management.
    """

    def __init__(self, ai_engine=None, module: str = 'ielts', db_session=None):
        """
        Initialize with AI engine, module name, and optional database session.

        Args:
            ai_engine: AI engine for generation
            module: 'ielts', 'pte', or 'ukvi' (default 'ielts')
            db_session: SQLAlchemy session (if not provided, uses models.db)
        """
        self.module = module
        self.ai_engine = ai_engine
        self.generator = ListeningTestGenerator(ai_engine)
        self.scoring = ListeningScoring()
        # Use factory to create repository with the correct module
        self.repo = create_listening_repository(module=module)
        self.db_session = db_session
        self._topic_cache = {}
        logger.info(f" ListeningService initialized for module '{module}' with Topic Registry integration")

    # ============================================================
    # TOPIC SELECTION HELPERS
    # ============================================================

    def _get_random_topic(self, section: int, difficulty: str, exclude: Optional[List[str]] = None) -> Optional[str]:
        """Get a random topic from the registry for given section and difficulty."""
        if not TOPICS_AVAILABLE or TopicRegistry is None:
            return None

        cache_key = f"{section}_{difficulty}_{'_'.join(exclude or [])}"
        if cache_key in self._topic_cache:
            topics = self._topic_cache[cache_key]
            if topics:
                return random.choice(topics)

        topics = TopicRegistry.get_random_topics(
            count=10,
            difficulty=difficulty,
            section=section,
            exclude=exclude,
            strict=False
        )

        if topics:
            self._topic_cache[cache_key] = topics
            return random.choice(topics)

        # Fallback without difficulty
        topics = TopicRegistry.get_random_topics(
            count=5,
            section=section,
            exclude=exclude,
            strict=False
        )
        if topics:
            self._topic_cache[cache_key] = topics
            return random.choice(topics)

        return None

    def _get_topics_for_sections(
        self,
        difficulty: str,
        user_topic: Optional[str] = None
    ) -> Dict[int, Optional[str]]:
        """Determine topics for sections 2, 3, 4."""
        result = {2: None, 3: None, 4: None}

        if not TOPICS_AVAILABLE or TopicRegistry is None:
            if user_topic:
                for sec in [2, 3, 4]:
                    result[sec] = user_topic
            else:
                defaults = {2: 'travel', 3: 'education', 4: 'environment'}
                for sec in [2, 3, 4]:
                    result[sec] = defaults.get(sec)
            return result

        if user_topic:
            info = TopicRegistry.get_topic_info(user_topic)
            if info:
                for sec in [2, 3, 4]:
                    result[sec] = user_topic
                logger.info(f" Using user-specified topic '{user_topic}' for all sections")
                return result
            else:
                logger.warning(f" User topic '{user_topic}' not found. Using random.")

        used_topics = []
        for sec in [2, 3, 4]:
            topic = self._get_random_topic(sec, difficulty, exclude=used_topics)
            if not topic:
                topic = self._get_random_topic(sec, None, exclude=used_topics)
            if topic:
                result[sec] = topic
                used_topics.append(topic)
            else:
                fallbacks = {2: 'travel', 3: 'education', 4: 'environment'}
                result[sec] = fallbacks.get(sec)
                logger.warning(f" No topic found for section {sec}, using fallback '{result[sec]}'")

        logger.info(f" Selected topics: S2={result[2]}, S3={result[3]}, S4={result[4]}")
        return result

    # ============================================================
    # TEST GENERATION
    # ============================================================

    def start_test(
        self,
        difficulty: str = "medium",
        topic: Optional[str] = None,
        exam: str = "ielts",
        accent: str = "british",
        fast: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate a new Listening test for the current module."""
        section_topics = self._get_topics_for_sections(difficulty, topic)
        kwargs['section_topics'] = section_topics

        main_topic = section_topics.get(2) or topic or 'travel'

        result = self.generator.generate(
            difficulty=difficulty,
            topic=main_topic,
            exam_type=exam,
            accent=accent,
            fast=fast,
            **kwargs,
        )

        if result and result.get('success'):
            test_data = result.get('test_data', {})
            if not test_data.get('topic'):
                test_data['topic'] = main_topic
            if 'section_topics' not in test_data:
                test_data['section_topics'] = section_topics
            # Add module info
            test_data['module'] = self.module

        return result

    # ============================================================
    # TEST RETRIEVAL (module‑aware)
    # ============================================================

    def get_saved_test(
        self,
        difficulty: str = "medium",
        exam: str = "ielts",
        fast: bool = True,
    ) -> Dict[str, Any]:
        """Load a random saved test, or generate new if none exist (for current module)."""
        saved = self.repo.random(difficulty=difficulty, exam_type=exam, module=self.module)
        if saved:
            return {
                'success': True,
                'test_data': saved,
                'source': 'saved',
                'message': 'Loaded saved test.'
            }
        logger.info(f" No saved test found for {difficulty}/{exam}, generating new...")
        new_test = self.generator.generate(
            difficulty=difficulty,
            exam_type=exam,
            fast=fast,
        )
        if new_test:
            try:
                self.repo.save(new_test, module=self.module)
            except Exception as e:
                logger.warning(f"Could not save generated test: {e}")
            return {
                'success': True,
                'test_data': new_test,
                'source': 'generated',
                'message': 'Generated new test (saved for future).'
            }
        else:
            return {
                'success': False,
                'error': 'Failed to generate test and no saved test available.'
            }

    def get_test_by_serial(self, serial_number: int) -> Optional[Dict[str, Any]]:
        if serial_number is None or serial_number < 1:
            return None
        return self.repo.get_by_serial(serial_number, module=self.module)

    def get_test_by_id(self, test_id: int) -> Optional[Dict[str, Any]]:
        if test_id is None or test_id < 1:
            return None
        return self.repo.get(test_id, module=self.module)

    def get_all_tests(
        self,
        exam: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        return self.repo.all(exam_type=exam, limit=limit, offset=offset, module=self.module)

    # ============================================================
    # TOPIC & STATISTICS
    # ============================================================

    def get_available_topics(self) -> Dict[str, List[str]]:
        """Get available topics by difficulty using TopicRegistry."""
        if TOPICS_AVAILABLE and TopicRegistry:
            topics_by_diff = {}
            for diff in ['easy', 'medium', 'hard']:
                topics = TopicRegistry.get_topic_names(difficulty=diff)
                topics_by_diff[diff] = topics
            return topics_by_diff
        else:
            # Fallback hardcoded topics
            return {
                'easy': ['supermarket', 'bank', 'pharmacy', 'bookstore', 'cafe'],
                'medium': ['museum', 'art_gallery', 'zoo', 'aquarium', 'university'],
                'hard': ['biotech', 'space', 'marine', 'nanotech', 'robotics']
            }

    def get_service_stats(self) -> Dict[str, Any]:
        """Get statistics about the service and repository (for current module)."""
        repo_stats = {
            'total_tests': self.repo.count(module=self.module),
            'by_difficulty': self.repo.count_by_difficulty(module=self.module),
        }
        gen_stats = {}
        if hasattr(self.generator, 'get_stats'):
            gen_stats = self.generator.get_stats()
        topic_stats = {}
        if TOPICS_AVAILABLE and TopicRegistry:
            topic_stats = TopicRegistry.get_stats()
        return {
            'repository': repo_stats,
            'generator': gen_stats,
            'topics': topic_stats,
        }

    def get_sections(self, test_id: int) -> List[Dict[str, Any]]:
        test_data = self.get_test_by_id(test_id)
        if not test_data:
            return []
        return test_data.get('sections', [])

    def get_questions_by_section(self, test_id: int, section_num: int) -> List[Dict[str, Any]]:
        sections = self.get_sections(test_id)
        for section in sections:
            if section.get('section_num') == section_num:
                return section.get('questions', [])
        return []

    # ============================================================
    # RESUME SUPPORT
    # ============================================================

    def resume_test(self, session_data: Dict[str, Any]) -> Dict[str, Any]:
        """Resume a test from saved session data."""
        test_id = session_data.get('test_id')
        if not test_id:
            raise ValueError("Missing test_id in session data")

        test_data = self.get_test_by_id(test_id)
        if not test_data:
            raise ValueError(f"Test {test_id} not found")

        answers_so_far = session_data.get('answers_so_far', {})
        for question in test_data.get('questions', []):
            qid = str(question.get('id', ''))
            if qid in answers_so_far:
                question['saved_answer'] = answers_so_far[qid]

        test_data['current_question_index'] = session_data.get('current_question_index', 0)
        test_data['_resumed'] = True
        return test_data

    # ============================================================
    # SUBMISSION & SCORING (module‑aware with dynamic models)
    # ============================================================

    def submit_test(
        self,
        test_id: int,
        answers: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Submit user answers and return detailed scoring."""
        if test_id is None or test_id < 1:
            return {'success': False, 'error': 'Valid test ID is required'}

        test = self.repo.get(test_id, module=self.module)
        if not test:
            logger.warning(f"Test not found: id={test_id}")
            return {'success': False, 'error': 'Test not found', 'test_id': test_id}

        correct_answers = test.get('correct_answers') or {}
        total = len(correct_answers)
        if total == 0:
            logger.error(f"Test {test_id} has no correct answers")
            return {'success': False, 'error': 'Test has no answer key'}

        validation = self.validate_answers(answers)
        if not validation['valid']:
            return {
                'success': False,
                'error': 'Invalid answer format',
                'details': validation,
            }

        cleaned_answers = validation['cleaned_answers']

        correct_count = 0
        detailed_results = []

        def key_sort(key):
            try:
                return int(key)
            except (ValueError, TypeError):
                return 0

        for q_num in sorted(correct_answers.keys(), key=key_sort):
            correct_answer = correct_answers[q_num]
            user_answer = cleaned_answers.get(str(q_num), '')
            is_correct = str(user_answer).strip().lower() == str(correct_answer).strip().lower()
            if is_correct:
                correct_count += 1
            detailed_results.append({
                'number': q_num,
                'user_answer': user_answer,
                'correct_answer': correct_answer,
                'is_correct': is_correct,
            })

        score_pct = round((correct_count / total) * 100, 1) if total > 0 else 0
        band = self.scoring.get_band(correct_count)
        descriptor = self.scoring.get_descriptor(band)
        cefr = self.scoring.get_cefr(band)

        try:
            self.repo.update_stats(test_id, score_pct, module=self.module)
        except Exception as e:
            logger.warning(f"Failed to update stats for test {test_id}: {e}")

        result = {
            'success': True,
            'test_id': test_id,
            'correct': correct_count,
            'total': total,
            'score_pct': score_pct,
            'band_score': band,
            'band_descriptor': descriptor,
            'cefr_level': cefr,
            'results': detailed_results,
            'topic': test.get('topic', ''),
            'difficulty': test.get('difficulty', ''),
            'accent': test.get('accent', ''),
            'module': self.module,
            'submitted_at': datetime.now(timezone.utc).isoformat(),
        }

        logger.info(f" Test submitted | id={test_id} | score={correct_count}/{total} ({score_pct}%) | band={band}")
        return result

    def submit_session(
        self,
        session_id: int,
        answers: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Submit answers for a test session using dynamic model."""
        try:
            from models import get_test_session_model, db
            TestSession = get_test_session_model(self.module)
            session_obj = TestSession.query.get(session_id)
            if not session_obj:
                return {'success': False, 'error': f'Session {session_id} not found'}
            test_data = session_obj.test_data
            test_id = test_data.get('id')
            if not test_id:
                return {'success': False, 'error': 'No test_id in session data'}
            # Update session
            session_obj.answers_so_far = answers
            session_obj.current_question_index = len(answers)
            session_obj.status = 'completed'
            db.session.commit()
            return self.submit_test(test_id, answers)
        except Exception as e:
            logger.error(f"Session submission failed: {e}")
            return {'success': False, 'error': str(e)}

    def save_progress(
        self,
        session_id: int,
        answers: Dict[str, Any],
        current_question_index: Optional[int] = None
    ) -> Dict[str, Any]:
        """Save partial answers for a test session (auto-save)."""
        try:
            from models import get_test_session_model, db
            TestSession = get_test_session_model(self.module)
            session_obj = TestSession.query.get(session_id)
            if not session_obj:
                return {'success': False, 'error': f'Session {session_id} not found'}
            existing = session_obj.answers_so_far or {}
            existing.update(answers)
            session_obj.answers_so_far = existing
            if current_question_index is not None:
                session_obj.current_question_index = current_question_index
            session_obj.last_updated = datetime.now(timezone.utc)
            db.session.commit()
            return {'success': True, 'message': 'Progress saved'}
        except Exception as e:
            logger.error(f"Save progress failed: {e}")
            return {'success': False, 'error': str(e)}

    # ============================================================
    # SCORING HELPERS
    # ============================================================

    def get_band_score(self, correct: int) -> float:
        return self.scoring.get_band(correct)

    def get_band_description(self, band: float) -> str:
        return self.scoring.get_descriptor(band)

    def get_cefr_level(self, band: float) -> str:
        return self.scoring.get_cefr(band)

    def get_score_breakdown(self, correct: int) -> Dict[str, Any]:
        band = self.scoring.get_band(correct)
        return {
            'correct': correct,
            'total': 40,
            'band_score': band,
            'band_descriptor': self.scoring.get_descriptor(band),
            'cefr_level': self.scoring.get_cefr(band),
        }

    # ============================================================
    # ANSWER VALIDATION
    # ============================================================

    @staticmethod
    def validate_answers(answers: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(answers, dict):
            return {
                'valid': False,
                'errors': ['Answers must be a dictionary'],
                'warnings': [],
                'cleaned_answers': {},
                'answer_count': 0,
            }

        errors: List[str] = []
        warnings: List[str] = []
        cleaned: Dict[str, str] = {}

        for key, value in answers.items():
            if not key:
                continue
            try:
                q_num = int(key)
            except (ValueError, TypeError):
                errors.append(f"Invalid question number: {key}")
                continue

            if q_num < 1 or q_num > 40:
                warnings.append(f"Question {q_num} is out of range (1-40)")

            cleaned[str(q_num)] = str(value).strip() if value is not None else ''

        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'cleaned_answers': cleaned,
            'answer_count': len(cleaned),
        }