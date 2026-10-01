"""Enhanced answer evaluator with multiple correct forms, synonyms, TFNG, numeric."""

import re
import logging
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Union, Set, Tuple

logger = logging.getLogger(__name__)

# ============================================================
# SEMANTIC ANALYZER
# ============================================================
class SemanticAnalyzer:
    """Semantic similarity using synonym matching, token overlap, and sequence matching."""

    def __init__(self):
        self.synonyms = self._load_synonyms()
        self.stop_words = {
            'a', 'an', 'and', 'the', 'of', 'to', 'in', 'for', 'on', 'with',
            'at', 'by', 'from', 'up', 'down', 'off', 'over', 'under'
        }
        self.negation_words = {'no', 'not', 'never', 'none', 'neither', 'nor'}

    def _load_synonyms(self) -> Dict[str, Set[str]]:
        return {
            'important': {'significant', 'crucial', 'vital', 'essential', 'key', 'major', 'paramount', 'critical'},
            'large': {'big', 'great', 'substantial', 'considerable', 'massive', 'huge', 'enormous', 'vast'},
            'small': {'little', 'minor', 'slight', 'minimal', 'tiny', 'negligible', 'insignificant'},
            'good': {'great', 'excellent', 'superior', 'fine', 'positive', 'beneficial', 'favorable'},
            'bad': {'poor', 'negative', 'detrimental', 'harmful', 'adverse', 'unfavorable'},
            'high': {'tall', 'elevated', 'lofty', 'top', 'upward', 'great'},
            'low': {'small', 'minor', 'short', 'diminished', 'reduced', 'inferior'},
            'new': {'recent', 'modern', 'novel', 'fresh', 'contemporary', 'latest', 'current'},
            'old': {'ancient', 'historic', 'antique', 'outdated', 'former', 'previous'},
            'fast': {'rapid', 'swift', 'quick', 'speedy', 'hasty', 'brisk'},
            'slow': {'gradual', 'leisurely', 'steady', 'moderate', 'gentle'},
            'increase': {'rise', 'grow', 'expand', 'climb', 'surge', 'escalate', 'magnify', 'amplify'},
            'decrease': {'decline', 'fall', 'drop', 'reduce', 'diminish', 'shrink', 'lessen'},
            'show': {'demonstrate', 'indicate', 'reveal', 'display', 'exhibit', 'illustrate'},
            'cause': {'lead to', 'result in', 'bring about', 'generate', 'produce', 'induce', 'trigger'},
            'effect': {'impact', 'influence', 'consequence', 'outcome', 'result'},
            'change': {'alter', 'modify', 'transform', 'adjust', 'vary', 'evolve', 'convert'},
            'develop': {'evolve', 'advance', 'progress', 'grow', 'mature', 'proceed'},
            'achieve': {'accomplish', 'attain', 'gain', 'reach', 'score', 'secure', 'fulfill'},
            'analyze': {'examine', 'investigate', 'scrutinize', 'study', 'explore', 'inspect'},
            'compare': {'contrast', 'differentiate', 'distinguish', 'relate', 'parallel'},
            'emphasize': {'highlight', 'stress', 'accentuate', 'underscore', 'focus'},
            'reason': {'cause', 'justification', 'explanation', 'basis', 'rationale', 'motive'},
            'result': {'outcome', 'consequence', 'effect', 'product', 'corollary'},
            'problem': {'issue', 'obstacle', 'challenge', 'difficulty', 'dilemma'},
            'solution': {'answer', 'resolution', 'remedy', 'cure', 'panacea'},
            'idea': {'notion', 'concept', 'thought', 'belief', 'conception', 'impression'},
            'method': {'approach', 'technique', 'procedure', 'process', 'system', 'strategy'},
            'advantage': {'benefit', 'asset', 'strength', 'profit', 'gain', 'edge'},
            'disadvantage': {'drawback', 'weakness', 'deficit', 'handicap', 'liability'},
            'growth': {'expansion', 'development', 'progress', 'advancement', 'escalation'},
            'value': {'worth', 'merit', 'importance', 'significance', 'usefulness'},
        }

    def calculate_similarity(self, answer: Optional[str], expected: Optional[str]) -> float:
        if not answer or not expected:
            return 0.0
        answer_norm = self._normalize(answer)
        expected_norm = self._normalize(expected)
        if not answer_norm or not expected_norm:
            return 0.0
        if answer_norm == expected_norm:
            return 1.0

        answer_tokens = set(answer_norm.split())
        expected_tokens = set(expected_norm.split())
        intersection = len(answer_tokens & expected_tokens)
        union = len(answer_tokens | expected_tokens)
        jaccard = intersection / union if union > 0 else 0.0

        synonym_matches = 0
        for a_token in answer_tokens:
            for e_token in expected_tokens:
                if self._are_synonyms(a_token, e_token):
                    synonym_matches += 1
                    break
        expected_len = len(expected_tokens) or 1
        synonym_score = min(1.0, synonym_matches / expected_len)

        seq_score = SequenceMatcher(None, answer_norm, expected_norm).ratio()
        final = (jaccard * 0.35) + (seq_score * 0.35) + (synonym_score * 0.30)
        return min(1.0, final)

    def _normalize(self, text: str) -> str:
        if not text:
            return ""
        text = text.lower()
        text = re.sub(r'[^\w\s\']', ' ', text)
        text = ' '.join(text.split())
        words = []
        for w in text.split():
            if w in self.negation_words:
                words.append(w)
            elif w not in self.stop_words and len(w) > 2:
                words.append(w)
        return ' '.join(words)

    def _are_synonyms(self, word1: str, word2: str) -> bool:
        if not word1 or not word2:
            return False
        w1 = word1.lower().strip()
        w2 = word2.lower().strip()
        if w1 == w2:
            return True
        for key, syns in self.synonyms.items():
            key_low = key.lower()
            if (w1 == key_low or w1 in syns) and (w2 == key_low or w2 in syns):
                return True
        if ' ' in w1 or ' ' in w2:
            w1_parts = set(w1.split())
            w2_parts = set(w2.split())
            common = w1_parts & w2_parts
            if len(common) >= min(len(w1_parts), len(w2_parts)) * 0.5:
                return True
        return False


# ============================================================
# ANSWER EVALUATOR
# ============================================================
class AnswerEvaluator:
    """Evaluates answers with support for multiple correct forms, TFNG, numeric, synonyms."""

    TFNG_MAP = {
        'true': {'true', 't', 'yes', 'y'},
        'false': {'false', 'f', 'no', 'n'},
        'not given': {'not given', 'ng', 'notgiven'}
    }

    def __init__(self, fuzzy_threshold: float = 0.75, semantic_analyzer=None):
        self.fuzzy_threshold = fuzzy_threshold
        self.semantic = semantic_analyzer or SemanticAnalyzer()
        logger.info(f"AnswerEvaluator initialized with threshold={fuzzy_threshold}")

    def evaluate_test(self, test_dict: Dict, user_answers: Dict) -> Dict:
        correct_answers = self._extract_correct_answers(test_dict)
        if not correct_answers:
            return {
                'total_questions': 0,
                'correct_count': 0,
                'incorrect_count': 0,
                'unanswered_count': 0,
                'score_percentage': 0,
                'detailed_results': []
            }

        total = len(correct_answers)
        correct = 0
        detailed = []
        for q_id, expected in correct_answers.items():
            user_ans = user_answers.get(q_id, '').strip()
            is_correct, sim = self._check_answer(user_ans, expected)
            if is_correct:
                correct += 1
            detailed.append({
                'question_id': q_id,
                'user_answer': user_ans or '(not answered)',
                'correct_answer': expected if isinstance(expected, str) else ', '.join(expected),
                'is_correct': is_correct,
                'similarity': sim
            })

        answered = sum(1 for k in user_answers if user_answers.get(k, '').strip())
        unanswered = total - answered
        score_pct = (correct / total) * 100 if total else 0

        return {
            'total_questions': total,
            'correct_count': correct,
            'incorrect_count': total - correct,
            'unanswered_count': unanswered,
            'score_percentage': round(score_pct, 1),
            'detailed_results': detailed
        }

    def _extract_correct_answers(self, test_dict: Dict) -> Dict[str, Union[str, List[str]]]:
        ans = {}
        if 'correct_answers' in test_dict:
            raw = test_dict['correct_answers']
            if isinstance(raw, dict):
                for k, v in raw.items():
                    ans[str(k)] = v
            return ans

        for passage in test_dict.get('passages', []):
            for q in passage.get('questions', []):
                qid = str(q.get('id') or q.get('number'))
                if qid:
                    correct = q.get('correct_answer') or q.get('answer')
                    if correct:
                        ans[qid] = correct
        return ans

    def _check_answer(self, user: str, expected: Union[str, List[str]]) -> Tuple[bool, float]:
        if not user or not expected:
            return False, 0.0
        if isinstance(expected, list):
            best_sim = 0.0
            for exp in expected:
                is_correct, sim = self._check_single(user, exp)
                if is_correct:
                    return True, sim
                best_sim = max(best_sim, sim)
            return False, best_sim
        else:
            return self._check_single(user, expected)

    def _check_single(self, user: str, expected: str) -> Tuple[bool, float]:
        user_clean = self._normalize(user)
        expected_clean = self._normalize(expected)
        if not user_clean or not expected_clean:
            return False, 0.0

        if user_clean == expected_clean:
            return True, 1.0

        tfng_result = self._check_tfng(user_clean, expected_clean)
        if tfng_result is not None:
            return tfng_result

        if self._check_numeric(user, expected):
            return True, 0.95

        sem_sim = self.semantic.calculate_similarity(user, expected)
        if sem_sim >= self.fuzzy_threshold:
            return True, sem_sim

        seq_sim = SequenceMatcher(None, user_clean, expected_clean).ratio()
        if seq_sim >= self.fuzzy_threshold:
            return True, seq_sim

        user_words = set(user_clean.split())
        exp_words = set(expected_clean.split())
        if user_words and exp_words:
            overlap = len(user_words & exp_words)
            union = len(user_words | exp_words)
            jaccard = overlap / union if union else 0
            if jaccard >= 0.7:
                return True, jaccard

        return False, max(sem_sim, seq_sim, 0.0)

    def _normalize(self, text: str) -> str:
        if not text:
            return ""
        text = text.lower()
        text = re.sub(r'[^\w\s]', ' ', text)
        return ' '.join(text.split())

    def _check_tfng(self, user: str, expected: str) -> Optional[Tuple[bool, float]]:
        exp_cat = None
        for cat, terms in self.TFNG_MAP.items():
            if expected in terms:
                exp_cat = cat
                break
        if not exp_cat:
            return None
        user_cat = None
        for cat, terms in self.TFNG_MAP.items():
            if user in terms:
                user_cat = cat
                break
        if user_cat and user_cat == exp_cat:
            return True, 1.0
        return False, 0.0

    def _check_numeric(self, user: str, expected: str) -> bool:
        try:
            user_nums = re.findall(r'[-+]?\d*\.?\d+', user)
            exp_nums = re.findall(r'[-+]?\d*\.?\d+', expected)
            if not user_nums or not exp_nums:
                return False
            u_val = float(user_nums[0])
            e_val = float(exp_nums[0])
            if e_val == 0:
                return abs(u_val) <= 0.01
            return abs((u_val - e_val) / e_val) <= 0.1
        except:
            return False

    def get_single_result(self, user_answer: str, correct_answer: str) -> Dict:
        is_correct, similarity = self._check_answer(user_answer, correct_answer)
        return {
            'is_correct': is_correct,
            'similarity': round(similarity, 3),
            'user_answer': user_answer,
            'correct_answer': correct_answer,
            'feedback': 'Correct!' if is_correct else 'Incorrect.'
        }


# Backward compatibility alias
EnhancedAnswerEvaluator = AnswerEvaluator