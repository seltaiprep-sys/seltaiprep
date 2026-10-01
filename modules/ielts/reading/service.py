"""Reading service with enhanced evaluation and scoring – module‑aware."""

import logging
from typing import Dict, List, Optional, Any

from .test_generator import IELTSReadingGenerator
from .evaluator import AnswerEvaluator
from .scoring import BandScoreCalculator
from .repository import ReadingTestRepository

logger = logging.getLogger(__name__)


class ReadingService:
    def __init__(
        self,
        ai_engine=None,
        repository=None,
        module: str = 'ielts'
    ):
        """
        Initialize ReadingService.

        Args:
            ai_engine: AI engine for generation.
            repository: Optional repository instance; if None, creates one with module.
            module: Module name (ielts, pte, ukvi) – used for repository.
        """
        self.ai_engine = ai_engine
        self.module = module
        self.generator = IELTSReadingGenerator(ai_engine=ai_engine)
        self.evaluator = AnswerEvaluator()
        self.scorer = BandScoreCalculator()
        self.repository = repository or ReadingTestRepository(module=module)
        logger.info(f"ReadingService initialized for module '{module}'")

    def create_test(
        self,
        topics: Optional[List[str]] = None,
        difficulty: str = "medium",
        user_id: Optional[str] = None
    ) -> Dict:
        """Generate a test with proper topic selection and save to repository (module‑aware)."""
        if not topics:
            from .test_generator import DEFAULT_TOPICS
            import random
            topics = random.sample(DEFAULT_TOPICS, 3)
        elif len(topics) < 3:
            import random
            from .test_generator import DEFAULT_TOPICS
            topics += random.sample(DEFAULT_TOPICS, 3 - len(topics))
        elif len(topics) > 3:
            topics = topics[:3]

        try:
            test = self.generator.generate_complete_test(
                difficulty=difficulty,
                topic_areas=topics
            )
            if test is None:
                return {'success': False, 'error': 'Generator returned None'}
            if hasattr(test, 'to_dict'):
                test_dict = test.to_dict()
            else:
                test_dict = test
            if user_id:
                # Repository will store with module info because it was initialized with module
                self.repository.save_test(test_dict, user_id)
            return {'success': True, 'test': test_dict}
        except Exception as e:
            logger.exception("Test generation failed")
            return {'success': False, 'error': str(e)}

    def evaluate_answers(self, test_dict: Dict, user_answers: Dict) -> Dict:
        """Evaluate answers with unanswered count."""
        results = self.evaluator.evaluate_test(test_dict, user_answers)
        total = results['total_questions']
        answered = sum(1 for k in user_answers if user_answers.get(k, '').strip())
        results['unanswered_count'] = total - answered
        return results