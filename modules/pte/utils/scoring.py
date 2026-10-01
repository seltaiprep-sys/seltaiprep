# modules/pte/utils/scoring.py
"""PTE Scoring Module - Official 2026 Scoring System (All Modules)"""

from typing import Dict, List, Optional, Tuple, Any, Union
import logging
import re

logger = logging.getLogger(__name__)


class PTEScoring:
    """PTE Score calculation and conversion - Official 2026 Standards"""

    SCORE_SCALE: Tuple[int, int] = (10, 90)

    DEFAULT_ENABLING_SCORES: Dict[str, int] = {
        'content_relevance': 60, 'grammar': 60, 'oral_fluency': 60,
        'pronunciation': 60, 'spelling': 60, 'vocabulary': 60, 'written_discourse': 60
    }

    SCORE_DESCRIPTORS: Dict[Tuple[int, int], str] = {
        (85, 90): "Expert - Can understand and express complex ideas with ease. Near-native proficiency.",
        (76, 84): "Very Good - Can handle complex language and understand implicit meaning effectively.",
        (65, 75): "Good - Can understand main ideas and express opinions clearly with minor errors.",
        (50, 64): "Competent - Can communicate effectively in familiar academic and social contexts.",
        (36, 49): "Modest - Limited but effective communication in everyday contexts.",
        (30, 35): "Limited - Basic communication in very familiar situations only.",
        (10, 29): "Elementary - Very basic understanding of simple English."
    }

    IELTS_TO_PTE: Dict[float, int] = {
        9.0: 89, 8.5: 84, 8.0: 79, 7.5: 74, 7.0: 68,
        6.5: 62, 6.0: 56, 5.5: 50, 5.0: 44, 4.5: 38,
        4.0: 32, 3.5: 26, 3.0: 20, 2.5: 15, 2.0: 10
    }

    PTE_TO_IELTS: Dict[int, float] = {
        90: 9.0, 89: 9.0, 88: 9.0, 87: 9.0, 86: 9.0, 85: 9.0,
        84: 8.5, 83: 8.5, 82: 8.5, 81: 8.5, 80: 8.0, 79: 8.0,
        78: 8.0, 77: 8.0, 76: 8.0, 75: 7.5, 74: 7.5, 73: 7.5,
        72: 7.5, 71: 7.5, 70: 7.0, 69: 7.0, 68: 7.0, 67: 7.0,
        66: 7.0, 65: 6.5, 64: 6.5, 63: 6.5, 62: 6.5, 61: 6.5,
        60: 6.0, 59: 6.0, 58: 6.0, 57: 6.0, 56: 6.0, 55: 5.5,
        54: 5.5, 53: 5.5, 52: 5.5, 51: 5.5, 50: 5.5, 49: 5.0,
        48: 5.0, 47: 5.0, 46: 5.0, 45: 5.0, 44: 5.0, 43: 4.5,
        42: 4.5, 41: 4.5, 40: 4.5, 39: 4.5, 38: 4.5, 37: 4.0,
        36: 4.0, 35: 4.0, 34: 4.0, 33: 4.0, 32: 4.0, 31: 3.5,
        30: 3.5, 29: 3.5, 28: 3.5, 27: 3.5, 26: 3.5, 25: 3.0,
        24: 3.0, 23: 3.0, 22: 3.0, 21: 3.0, 20: 3.0, 19: 2.5,
        18: 2.5, 17: 2.5, 16: 2.5, 15: 2.5, 14: 2.0, 13: 2.0,
        12: 2.0, 11: 2.0, 10: 2.0
    }

    QUESTION_WEIGHTS_2026: Dict[str, Dict[str, float]] = {
        'speaking': {
            'describe_image': 0.25, 're_tell_lecture': 0.25,
            'repeat_sentence': 0.20, 'read_aloud': 0.15, 'answer_short_question': 0.15
        },
        'writing': {
            'write_essay': 0.70, 'summarize_written_text': 0.30
        },
        'reading': {
            'fill_blanks': 0.35, 'reading_fill_blanks': 0.20,
            'reorder_paragraphs': 0.25, 'multiple_choice_single': 0.10,
            'multiple_choice_multiple': 0.10
        },
        'listening': {
            'summarize_spoken_text': 0.25, 'write_from_dictation': 0.25,
            'fill_blanks': 0.15, 'highlight_incorrect_words': 0.10,
            'multiple_choice_multiple': 0.10, 'highlight_correct_summary': 0.05,
            'select_missing_word': 0.05, 'multiple_choice_single': 0.05
        }
    }

    ENABLING_SKILLS: List[str] = [
        'content_relevance', 'grammar', 'oral_fluency', 'pronunciation',
        'spelling', 'vocabulary', 'written_discourse'
    ]

    COMMUNICATIVE_SKILLS: List[str] = ['listening', 'reading', 'speaking', 'writing']

    COMMUNICATIVE_WEIGHTS: Dict[str, float] = {
        'listening': 0.25, 'reading': 0.25, 'speaking': 0.25, 'writing': 0.25
    }

    PERCENTILES: Dict[int, int] = {
        90: 99, 85: 97, 80: 94, 75: 88, 70: 80,
        65: 70, 60: 58, 55: 45, 50: 32, 45: 20,
        40: 12, 35: 6, 30: 3, 25: 1, 20: 1, 15: 1, 10: 1
    }

    COMMON_SPELLING_ERRORS: Dict[str, str] = {
        'accomodate': 'accommodate', 'recieve': 'receive',
        'seperate': 'separate', 'definately': 'definitely',
        'goverment': 'government', 'environement': 'environment',
        'developement': 'development', 'significent': 'significant',
        'importent': 'important', 'differant': 'different'
    }

    ACADEMIC_WORDS: set = {
        'analyze', 'approach', 'assess', 'concept', 'context', 'create',
        'define', 'derive', 'distribute', 'economy', 'environment', 'establish',
        'estimate', 'evidence', 'factor', 'function', 'identify', 'impact',
        'indicate', 'individual', 'interpret', 'involve', 'issue', 'legal',
        'legislate', 'major', 'method', 'occur', 'percent', 'period', 'policy',
        'principle', 'process', 'require', 'research', 'respond', 'role',
        'section', 'sector', 'significant', 'similar', 'source', 'specific',
        'structure', 'theory', 'variable'
    }

    DISCOURSE_MARKERS: List[str] = [
        'however', 'therefore', 'furthermore', 'moreover', 'consequently',
        'nevertheless', 'nonetheless', 'accordingly', 'additionally',
        'in addition', 'on the other hand', 'as a result', 'for example',
        'for instance', 'in conclusion', 'to summarize', 'firstly', 'secondly',
        'thirdly', 'finally', 'subsequently', 'ultimately'
    ]

    WORD_LIMITS = {
        'summarize_spoken_text': (50, 70),
        'summarize_written_text': (5, 75),
        'write_essay': (200, 300),
    }

    # ═══════════════════════════════════════════════════════════════════
    # BLANK-ANSWER DETECTION
    # ═══════════════════════════════════════════════════════════════════
    @staticmethod
    def _is_blank(ans: Any) -> bool:
        """Return True if an answer is effectively empty."""
        if ans is None:
            return True
        if isinstance(ans, str):
            return ans.strip() == ''
        if isinstance(ans, (list, tuple)):
            return all(PTEScoring._is_blank(x) for x in ans)
        if isinstance(ans, dict):
            return all(PTEScoring._is_blank(v) for v in ans.values())
        # Numbers, booleans, etc. — treat as non-blank
        return False

    # ═══════════════════════════════════════════════════════════════════
    # OVERALL + COMMUNICATIVE
    # ═══════════════════════════════════════════════════════════════════

    @classmethod
    def calculate_overall_score(cls, scores: Dict[str, Union[int, float]]) -> int:
        if not scores:
            return 0
        total_weighted = 0.0
        total_weight = 0.0
        for skill, weight in cls.COMMUNICATIVE_WEIGHTS.items():
            if skill in scores and scores[skill] is not None:
                total_weighted += float(scores[skill]) * weight
                total_weight += weight
        if total_weight == 0:
            return 0
        overall = int(round(total_weighted / total_weight))
        return max(cls.SCORE_SCALE[0], min(cls.SCORE_SCALE[1], overall))

    @classmethod
    def calculate_communicative_skills(
        cls, speaking_scores, writing_scores, reading_scores, listening_scores
    ) -> Dict[str, int]:
        def calc_weighted(scores, section):
            if not scores: return 0
            weighted = 0.0
            total_weight = 0.0
            section_weights = cls.QUESTION_WEIGHTS_2026.get(section, {})
            for q_type, score in scores.items():
                if q_type in section_weights:
                    weight = section_weights[q_type]
                    s = float(score)
                    if s <= 1.0:
                        s = s * 90
                    weighted += s * weight
                    total_weight += weight
            if total_weight == 0:
                return 0
            result = int(round(weighted / total_weight))
            return max(10, min(90, result))
        return {
            'speaking': calc_weighted(speaking_scores, 'speaking'),
            'writing': calc_weighted(writing_scores, 'writing'),
            'reading': calc_weighted(reading_scores, 'reading'),
            'listening': calc_weighted(listening_scores, 'listening')
        }

    # ═══════════════════════════════════════════════════════════════════
    # READING-LEVEL SCORING
    # ═══════════════════════════════════════════════════════════════════

    @classmethod
    def score_multiple_choice_single(cls, user_choice, correct_choice) -> Dict[str, Any]:
        def _to_int(x):
            if x is None: return None
            try: return int(x)
            except (TypeError, ValueError): return None
        u = _to_int(user_choice)
        c = _to_int(correct_choice)
        if c is None:
            return {'score': 0.0, 'correct': False, 'details': 'no_correct_defined'}
        is_correct = (u is not None and u == c)
        return {
            'score': 1.0 if is_correct else 0.0,
            'correct': is_correct,
            'user': user_choice, 'expected': correct_choice,
            'details': f'user={user_choice}, correct={correct_choice}'
        }

    @classmethod
    def score_multiple_choice_multiple(cls, user_choices, correct_choices) -> Dict[str, Any]:
        if not isinstance(user_choices, list): user_choices = []
        if not isinstance(correct_choices, list):
            return {'score': 0.0, 'correct': False, 'details': 'no_correct_defined'}
        def _to_int_set(lst):
            out = set()
            for v in lst:
                try: out.add(int(v))
                except (TypeError, ValueError): continue
            return out
        correct_set = _to_int_set(correct_choices)
        user_set = _to_int_set(user_choices)
        total_correct = len(correct_set)
        if total_correct == 0:
            return {'score': 0.0, 'correct': False, 'details': 'no_correct_defined'}
        correct_selected = len(user_set & correct_set)
        incorrect_selected = len(user_set - correct_set)
        raw = (correct_selected - incorrect_selected) / total_correct
        final_score = max(0.0, min(1.0, raw))
        return {
            'score': round(final_score, 4),
            'correct': (final_score >= 1.0),
            'correct_selected': correct_selected,
            'incorrect_selected': incorrect_selected,
            'total_correct': total_correct,
            'details': f'{correct_selected}/{total_correct} correct, {incorrect_selected} incorrect'
        }

    @classmethod
    def score_reorder_paragraphs(cls, user_order, correct_order) -> Dict[str, Any]:
        if not isinstance(user_order, list) or not isinstance(correct_order, list):
            return {'score': 0.0, 'correct': False, 'details': 'invalid_input'}
        n = len(correct_order)
        if n < 2: return {'score': 0.0, 'correct': False, 'details': 'too_few_sentences'}
        if len(user_order) != n: return {'score': 0.0, 'correct': False, 'details': 'length_mismatch'}
        def _to_int_list(lst):
            out = []
            for v in lst:
                try: out.append(int(v))
                except (TypeError, ValueError): out.append(None)
            return out
        u = _to_int_list(user_order)
        c = _to_int_list(correct_order)
        if any(v is None for v in u) or any(v is None for v in c):
            return {'score': 0.0, 'correct': False, 'details': 'invalid_values'}
        correct_pairs = set()
        for i in range(n - 1):
            correct_pairs.add((c[i], c[i + 1]))
        matched = 0
        for i in range(n - 1):
            if (u[i], u[i + 1]) in correct_pairs:
                matched += 1
        max_pairs = n - 1
        score = matched / max_pairs
        return {
            'score': round(score, 4),
            'correct': (matched == max_pairs),
            'correct_pairs': matched,
            'max_pairs': max_pairs,
            'details': f'{matched}/{max_pairs} adjacent pairs correct'
        }

    @classmethod
    def score_fill_blanks(cls, user_answers, correct_answers, case_sensitive: bool = False) -> Dict[str, Any]:
        if not isinstance(user_answers, list): user_answers = []
        if not isinstance(correct_answers, list):
            return {'score': 0.0, 'correct': False, 'details': 'no_correct_defined'}
        total = len(correct_answers)
        if total == 0:
            return {'score': 0.0, 'correct': False, 'details': 'no_correct_defined'}
        correct_count = 0
        per_blank = []
        for i in range(total):
            user_ans = user_answers[i] if i < len(user_answers) else ''
            correct_ans = correct_answers[i]
            if user_ans is None: user_ans = ''
            if correct_ans is None: correct_ans = ''
            u = str(user_ans).strip()
            c = str(correct_ans).strip()
            if not case_sensitive:
                u_cmp, c_cmp = u.lower(), c.lower()
            else:
                u_cmp, c_cmp = u, c
            ok = (u_cmp == c_cmp) and u != ''
            if ok: correct_count += 1
            per_blank.append({'blank_index': i, 'user': u, 'correct': c, 'is_correct': ok})
        return {
            'score': round(correct_count / total, 4),
            'correct': (correct_count == total),
            'correct_count': correct_count,
            'total': total,
            'per_blank': per_blank,
            'details': f'{correct_count}/{total} blanks correct'
        }

    score_reading_fill_blanks = score_fill_blanks

    # ═══════════════════════════════════════════════════════════════════
    # LISTENING-LEVEL SCORING
    # ═══════════════════════════════════════════════════════════════════

    @classmethod
    def score_write_from_dictation(cls, user_text, correct_text) -> Dict[str, Any]:
        if user_text is None: user_text = ''
        if correct_text is None: correct_text = ''
        # Empty user text → 0
        if not str(user_text).strip():
            return {'score': 0.0, 'correct': False, 'details': 'empty_response'}

        def _clean(s):
            s = re.sub(r'[^\w\s\']', ' ', str(s).lower())
            return [w for w in s.split() if w]

        user_words = _clean(user_text)
        correct_words = _clean(correct_text)
        total = len(correct_words)
        if total == 0:
            return {'score': 0.0, 'correct': False, 'details': 'no_correct_text'}

        matched = 0
        for i in range(min(len(user_words), total)):
            if user_words[i] == correct_words[i]:
                matched += 1

        score = matched / total
        return {
            'score': round(score, 4),
            'correct': (matched == total),
            'correct_words': matched,
            'total_words': total,
            'details': f'{matched}/{total} words correct'
        }

    @classmethod
    def score_highlight_incorrect_words(cls, user_selected, incorrect_words) -> Dict[str, Any]:
        if not isinstance(user_selected, list): user_selected = []
        if not isinstance(incorrect_words, list):
            return {'score': 0.0, 'correct': False, 'details': 'no_correct_defined'}
        total_incorrect = len(incorrect_words)
        if total_incorrect == 0:
            return {'score': 0.0, 'correct': False, 'details': 'no_correct_defined'}

        user_set = {str(w).strip().lower() for w in user_selected if str(w).strip()}
        correct_set = {str(w).strip().lower() for w in incorrect_words if str(w).strip()}

        hits = len(user_set & correct_set)
        false_positives = len(user_set - correct_set)
        raw = (hits - false_positives) / total_incorrect
        final_score = max(0.0, min(1.0, raw))
        return {
            'score': round(final_score, 4),
            'correct': (hits == total_incorrect and false_positives == 0),
            'hits': hits,
            'false_positives': false_positives,
            'total_incorrect': total_incorrect,
            'details': f'{hits} hits, {false_positives} false positives / {total_incorrect}'
        }

    @classmethod
    def score_select_missing_word(cls, user_choice, correct_choice) -> Dict[str, Any]:
        if isinstance(correct_choice, list):
            correct_choice = correct_choice[0] if correct_choice else None
        u = str(user_choice).strip() if user_choice is not None else ''
        c = str(correct_choice).strip() if correct_choice is not None else ''
        if not u:
            return {'score': 0.0, 'correct': False, 'details': 'empty_response'}
        try:
            u_int, c_int = int(u), int(c)
            ok = (u_int == c_int)
        except (TypeError, ValueError):
            ok = (u.lower() == c.lower() and u != '')
        return {
            'score': 1.0 if ok else 0.0,
            'correct': ok,
            'user': user_choice,
            'expected': correct_choice,
            'details': f'user={user_choice}, correct={correct_choice}'
        }

    @classmethod
    def score_highlight_correct_summary(cls, user_choice, correct_choice) -> Dict[str, Any]:
        return cls.score_select_missing_word(user_choice, correct_choice)

    @classmethod
    def score_summarize_spoken_text(cls, user_text, reference_text=None, ai_score=None) -> Dict[str, Any]:
        if user_text is None: user_text = ''
        user_text = str(user_text).strip()

        # Empty → 0
        if not user_text:
            return {
                'score': 0.0, 'correct': False, 'word_count': 0,
                'form_score': 0.0, 'content_score': 0.0,
                'source': 'empty', 'details': 'No response provided'
            }

        word_count = len(user_text.split())
        min_w, max_w = cls.WORD_LIMITS['summarize_spoken_text']

        if min_w <= word_count <= max_w:
            form_score = 1.0
        else:
            distance = min(abs(word_count - min_w), abs(word_count - max_w))
            form_score = max(0.0, 1.0 - (distance / max_w))

        if reference_text and user_text:
            ref_words = {w for w in re.findall(r'\b\w+\b', str(reference_text).lower()) if len(w) > 3}
            user_words = {w for w in re.findall(r'\b\w+\b', user_text.lower()) if len(w) > 3}
            if ref_words:
                overlap = len(user_words & ref_words) / len(ref_words)
                content_score = min(1.0, overlap * 1.5)
            else:
                content_score = 0.5
        else:
            content_score = 0.5 if word_count > 20 else 0.3

        if ai_score is not None:
            final = max(0.0, min(1.0, float(ai_score)))
            source = 'ai'
        else:
            final = 0.4 * content_score + 0.6 * form_score
            source = 'heuristic'

        return {
            'score': round(final, 4),
            'correct': (final >= 0.7),
            'word_count': word_count,
            'form_score': round(form_score, 4),
            'content_score': round(content_score, 4),
            'source': source,
            'details': f'{word_count} words, form={round(form_score,2)}, content={round(content_score,2)}'
        }

    @classmethod
    def score_listening_fill_blanks(cls, user_answers, correct_answers) -> Dict[str, Any]:
        return cls.score_fill_blanks(user_answers, correct_answers, case_sensitive=False)

    # ═══════════════════════════════════════════════════════════════════
    # SPEAKING SCORING
    # ═══════════════════════════════════════════════════════════════════

    @classmethod
    def score_speaking_response(cls, transcript, reference_text=None, ai_score=None, task_type='generic') -> Dict[str, Any]:
        if transcript is None: transcript = ''
        transcript = str(transcript).strip()

        # FIX: Empty transcript = zero score
        if not transcript:
            return {
                'score': 0.0, 'correct': False, 'word_count': 0,
                'content_score': 0.0, 'fluency_score': 0.0, 'pronunciation_score': 0.0,
                'source': 'empty',
                'details': 'No response provided',
            }

        words = transcript.split()
        wc = len(words)

        if ai_score and isinstance(ai_score, dict):
            content = float(ai_score.get('content', ai_score.get('content_relevance', 0.5)))
            fluency = float(ai_score.get('fluency', ai_score.get('oral_fluency', 0.5)))
            pronunciation = float(ai_score.get('pronunciation', 0.5))
            source = 'ai'
        else:
            if reference_text and wc > 0:
                ref_words = [w for w in re.findall(r'\b\w+\b', str(reference_text).lower()) if len(w) > 2]
                user_words = [w for w in re.findall(r'\b\w+\b', transcript.lower()) if len(w) > 2]
                if ref_words:
                    overlap = sum(1 for w in user_words if w in ref_words) / len(ref_words)
                    content = min(1.0, overlap * 1.2)
                else:
                    content = 0.5
            else:
                content = 0.5 if wc > 15 else 0.3

            if wc >= 40: fluency = 0.85
            elif wc >= 25: fluency = 0.75
            elif wc >= 15: fluency = 0.65
            elif wc >= 5: fluency = 0.5
            elif wc >= 1: fluency = 0.3
            else: fluency = 0.0

            pronunciation = 0.5
            source = 'heuristic'

        if task_type in ('read_aloud', 'repeat_sentence'):
            final = 0.55 * content + 0.25 * fluency + 0.20 * pronunciation
        elif task_type in ('describe_image', 're_tell_lecture'):
            final = 0.45 * content + 0.35 * fluency + 0.20 * pronunciation
        elif task_type == 'answer_short_question':
            final = 0.70 * content + 0.20 * fluency + 0.10 * pronunciation
        else:
            final = 0.40 * content + 0.30 * fluency + 0.30 * pronunciation

        final = max(0.0, min(1.0, final))
        return {
            'score': round(final, 4),
            'correct': (final >= 0.6),
            'word_count': wc,
            'content_score': round(content, 4),
            'fluency_score': round(fluency, 4),
            'pronunciation_score': round(pronunciation, 4),
            'source': source,
            'details': f'{wc} words, content={round(content,2)}, fluency={round(fluency,2)}'
        }

    @classmethod
    def score_read_aloud(cls, transcript, reference_text=None, ai_score=None):
        return cls.score_speaking_response(transcript, reference_text, ai_score, 'read_aloud')

    @classmethod
    def score_repeat_sentence(cls, transcript, reference_text=None, ai_score=None):
        return cls.score_speaking_response(transcript, reference_text, ai_score, 'repeat_sentence')

    @classmethod
    def score_describe_image(cls, transcript, reference_text=None, ai_score=None):
        return cls.score_speaking_response(transcript, reference_text, ai_score, 'describe_image')

    @classmethod
    def score_re_tell_lecture(cls, transcript, reference_text=None, ai_score=None):
        return cls.score_speaking_response(transcript, reference_text, ai_score, 're_tell_lecture')

    @classmethod
    def score_answer_short_question(cls, transcript, reference_text=None, ai_score=None):
        return cls.score_speaking_response(transcript, reference_text, ai_score, 'answer_short_question')

    # ═══════════════════════════════════════════════════════════════════
    # WRITING SCORING
    # ═══════════════════════════════════════════════════════════════════

    @classmethod
    def score_summarize_written_text(cls, user_text, reference_text=None, ai_score=None) -> Dict[str, Any]:
        if user_text is None: user_text = ''
        user_text = str(user_text).strip()

        # Empty → 0
        if not user_text:
            return {
                'score': 0.0, 'correct': False, 'word_count': 0, 'sentence_count': 0,
                'form_score': 0.0, 'content_score': 0.0,
                'grammar_score': 0.0, 'vocabulary_score': 0.0,
                'source': 'empty', 'details': 'No response provided'
            }

        words = user_text.split()
        wc = len(words)
        min_w, max_w = cls.WORD_LIMITS['summarize_written_text']
        sentence_count = max(1, user_text.count('.') + user_text.count('!') + user_text.count('?'))

        if min_w <= wc <= max_w and sentence_count == 1:
            form_score = 1.0
        else:
            length_ok = 1.0 if min_w <= wc <= max_w else max(0.0, 1.0 - abs(wc - min_w) / max_w)
            sentence_ok = 1.0 if sentence_count == 1 else max(0.0, 1.0 / sentence_count)
            form_score = length_ok * sentence_ok

        grammar_score = cls._evaluate_grammar(user_text) / 90.0
        vocab_score = cls._evaluate_vocabulary(user_text) / 90.0

        if reference_text and user_text:
            ref_words = {w for w in re.findall(r'\b\w+\b', str(reference_text).lower()) if len(w) > 3}
            user_words = {w for w in re.findall(r'\b\w+\b', user_text.lower()) if len(w) > 3}
            content_score = min(1.0, len(user_words & ref_words) / len(ref_words) * 1.5) if ref_words else 0.5
        else:
            content_score = 0.5 if wc > 20 else 0.3

        if ai_score is not None:
            final = max(0.0, min(1.0, float(ai_score)))
            source = 'ai'
        else:
            final = (0.40 * content_score + 0.20 * form_score
                     + 0.20 * grammar_score + 0.20 * vocab_score)
            source = 'heuristic'

        return {
            'score': round(final, 4),
            'correct': (final >= 0.7),
            'word_count': wc,
            'sentence_count': sentence_count,
            'form_score': round(form_score, 4),
            'content_score': round(content_score, 4),
            'grammar_score': round(grammar_score, 4),
            'vocabulary_score': round(vocab_score, 4),
            'source': source,
            'details': f'{wc} words, {sentence_count} sentence(s), form={round(form_score,2)}'
        }

    @classmethod
    def score_write_essay(cls, user_text, topic=None, ai_score=None) -> Dict[str, Any]:
        if user_text is None: user_text = ''
        user_text = str(user_text).strip()

        # Empty → 0
        if not user_text:
            return {
                'score': 0.0, 'correct': False, 'word_count': 0,
                'form_score': 0.0, 'content_score': 0.0,
                'grammar_score': 0.0, 'vocabulary_score': 0.0,
                'spelling_score': 0.0, 'discourse_score': 0.0,
                'source': 'empty', 'details': 'No response provided'
            }

        wc = len(user_text.split())
        min_w, max_w = cls.WORD_LIMITS['write_essay']

        if min_w <= wc <= max_w:
            form_score = 1.0
        else:
            distance = min(abs(wc - min_w), abs(wc - max_w))
            form_score = max(0.0, 1.0 - distance / max_w)

        grammar_score = cls._evaluate_grammar(user_text) / 90.0
        vocab_score = cls._evaluate_vocabulary(user_text) / 90.0
        spelling_score = cls._evaluate_spelling(user_text) / 90.0
        discourse_score = cls._evaluate_discourse(user_text) / 90.0

        if topic and user_text:
            topic_words = {w for w in re.findall(r'\b\w+\b', str(topic).lower()) if len(w) > 3}
            user_words = {w for w in re.findall(r'\b\w+\b', user_text.lower()) if len(w) > 3}
            topic_overlap = len(user_words & topic_words) / len(topic_words) if topic_words else 0.5
            content_score = min(1.0, topic_overlap * 2.0)
        else:
            content_score = 0.5 if wc > 100 else 0.3

        if ai_score is not None:
            final = max(0.0, min(1.0, float(ai_score)))
            source = 'ai'
        else:
            final = (0.30 * content_score + 0.20 * form_score
                     + 0.20 * grammar_score + 0.15 * vocab_score
                     + 0.10 * spelling_score + 0.05 * discourse_score)
            source = 'heuristic'

        return {
            'score': round(final, 4),
            'correct': (final >= 0.7),
            'word_count': wc,
            'form_score': round(form_score, 4),
            'content_score': round(content_score, 4),
            'grammar_score': round(grammar_score, 4),
            'vocabulary_score': round(vocab_score, 4),
            'spelling_score': round(spelling_score, 4),
            'discourse_score': round(discourse_score, 4),
            'source': source,
            'details': f'{wc} words, form={round(form_score,2)}'
        }

    # ═══════════════════════════════════════════════════════════════════
    # DISPATCHER
    # ═══════════════════════════════════════════════════════════════════

    @classmethod
    def score_question(cls, question, user_answer) -> Dict[str, Any]:
        if not isinstance(question, dict):
            return {'score': 0.0, 'correct': False, 'details': 'invalid_question'}

        # Blank guard — score 0 immediately
        if cls._is_blank(user_answer):
            return {'score': 0.0, 'correct': False, 'details': 'empty_response'}

        qtype = question.get('type')
        prompt = question.get('prompt') or {}

        def _correct(key):
            c = question.get('correct_answer')
            if c is not None: return c
            if isinstance(prompt, dict): return prompt.get(key)
            return None

        # Reading
        if qtype == 'multiple_choice_single':
            ca = _correct('correct')
            if isinstance(ca, list): ca = ca[0] if ca else None
            return cls.score_multiple_choice_single(user_answer, ca)

        if qtype == 'multiple_choice_multiple':
            return cls.score_multiple_choice_multiple(user_answer, _correct('correct') or [])

        if qtype == 'reorder_paragraphs':
            ca = _correct('correct_order') or _correct('correct_answer') or []
            return cls.score_reorder_paragraphs(user_answer or [], ca)

        if qtype == 'fill_blanks':
            return cls.score_fill_blanks(user_answer or [], _correct('blanks') or [])

        if qtype == 'reading_fill_blanks':
            return cls.score_reading_fill_blanks(user_answer or [], _correct('blanks') or [])

        # Listening
        if qtype == 'summarize_spoken_text':
            ref = prompt.get('reference_text') if isinstance(prompt, dict) else None
            ai = question.get('ai_score')
            return cls.score_summarize_spoken_text(user_answer, ref, ai)

        if qtype == 'write_from_dictation':
            ca = _correct('correct_answer') or (prompt.get('sentence') if isinstance(prompt, dict) else None)
            return cls.score_write_from_dictation(user_answer, ca)

        if qtype == 'highlight_incorrect_words':
            ca = _correct('incorrect_words') or []
            return cls.score_highlight_incorrect_words(user_answer or [], ca)

        if qtype == 'select_missing_word':
            ca = _correct('correct') or _correct('correct_answer')
            if isinstance(ca, list): ca = ca[0] if ca else None
            return cls.score_select_missing_word(user_answer, ca)

        if qtype == 'highlight_correct_summary':
            ca = _correct('correct') or _correct('correct_answer')
            if isinstance(ca, list): ca = ca[0] if ca else None
            return cls.score_highlight_correct_summary(user_answer, ca)

        if qtype == 'listening_fill_blanks':
            return cls.score_listening_fill_blanks(user_answer or [], _correct('blanks') or [])

        # Writing
        if qtype == 'summarize_written_text':
            ref = prompt.get('passage') if isinstance(prompt, dict) else None
            ai = question.get('ai_score')
            return cls.score_summarize_written_text(user_answer, ref, ai)

        if qtype == 'write_essay':
            topic = prompt.get('topic') if isinstance(prompt, dict) else None
            ai = question.get('ai_score')
            return cls.score_write_essay(user_answer, topic, ai)

        # Speaking
        if qtype in ('read_aloud', 'repeat_sentence', 'describe_image',
                     're_tell_lecture', 'answer_short_question'):
            ref = None
            if isinstance(prompt, dict):
                ref = (prompt.get('sentence') or prompt.get('passage') or
                       prompt.get('lecture') or prompt.get('text'))
            ai = question.get('ai_score')
            return cls.score_speaking_response(user_answer, ref, ai, qtype)

        return {'score': 0.0, 'correct': False, 'details': f'unknown_type:{qtype}'}

    # ═══════════════════════════════════════════════════════════════════
    # SECTION SCORERS
    # ═══════════════════════════════════════════════════════════════════

    @classmethod
    def score_reading_test(cls, questions, user_answers, section_weight: float = 1.0):
        return cls._score_section(questions, user_answers, 'reading', section_weight)

    @classmethod
    def score_listening_test(cls, questions, user_answers, section_weight: float = 1.0):
        return cls._score_section(questions, user_answers, 'listening', section_weight)

    @classmethod
    def score_speaking_writing_test(cls, questions, user_answers, section_weight: float = 1.0):
        return cls._score_section(questions, user_answers, 'speaking_writing', section_weight)

    @classmethod
    def _score_section(cls, questions, user_answers, section_name: str, section_weight: float) -> Dict[str, Any]:
        if not isinstance(questions, list) or not questions:
            return {
                'score_0_1': 0.0, 'score_0_90': 10,
                'raw_correct': 0, 'total': 0,
                'per_question': [], 'type_breakdown': {},
                'section': section_name, 'section_weight': section_weight,
            }

        per_question = []
        sum_score = 0.0
        fully_correct = 0
        type_scores: Dict[str, List[float]] = {}

        for q in questions:
            qid = q.get('id')
            user_ans = user_answers.get(qid) if isinstance(user_answers, dict) else None

            # FIX: Skip scoring entirely if blank
            if cls._is_blank(user_ans):
                entry = {
                    'question_id': qid,
                    'type': q.get('type'),
                    'user_answer': user_ans,
                    'correct_answer': q.get('correct_answer'),
                    'score': 0.0,
                    'is_correct': False,
                    'details': 'empty_response',
                }
                per_question.append(entry)
                qt = q.get('type') or 'unknown'
                type_scores.setdefault(qt, []).append(0.0)
                continue

            result = cls.score_question(q, user_ans)

            entry = {
                'question_id': qid,
                'type': q.get('type'),
                'user_answer': user_ans,
                'correct_answer': q.get('correct_answer'),
                'score': result.get('score', 0.0),
                'is_correct': result.get('correct', False),
                'details': result.get('details', ''),
            }
            per_question.append(entry)
            sum_score += entry['score']
            if entry['is_correct']:
                fully_correct += 1
            qt = q.get('type') or 'unknown'
            type_scores.setdefault(qt, []).append(entry['score'])

        total = len(questions)
        score_0_1 = sum_score / total if total else 0.0
        score_0_90 = int(round(10 + score_0_1 * 80))
        score_0_90 = max(10, min(90, score_0_90))

        type_breakdown = {
            qt: round(sum(s) / len(s), 4) for qt, s in type_scores.items()
        }

        return {
            'score_0_1': round(score_0_1, 4),
            'score_0_90': score_0_90,
            'raw_correct': fully_correct,
            'total': total,
            'per_question': per_question,
            'type_breakdown': type_breakdown,
            'section': section_name,
            'section_weight': section_weight,
        }

    # ═══════════════════════════════════════════════════════════════════
    # ENABLING SKILLS + SUB-SCORES
    # ═══════════════════════════════════════════════════════════════════

    @classmethod
    def calculate_enabling_skills(cls, responses, ai_confidence=None):
        if not responses:
            return cls.DEFAULT_ENABLING_SCORES.copy()
        enabling_scores = {k: 0.0 for k in cls.DEFAULT_ENABLING_SCORES}
        total = 0
        for _, rd in responses.items():
            if not isinstance(rd, dict): continue
            total += 1
            text = str(rd.get('text', ''))
            if text:
                enabling_scores['grammar'] += cls._evaluate_grammar(text)
                enabling_scores['vocabulary'] += cls._evaluate_vocabulary(text)
                enabling_scores['spelling'] += cls._evaluate_spelling(text)
                enabling_scores['written_discourse'] += cls._evaluate_discourse(text)
                wc = len(text.split())
                enabling_scores['oral_fluency'] += (70.0 if wc > 30 else 60.0 if wc > 10 else 50.0)
                enabling_scores['content_relevance'] += (
                    75.0 if wc > 100 else 65.0 if wc > 50 else 55.0 if wc > 20 else 45.0
                )
            enabling_scores['pronunciation'] += float(rd.get('pronunciation', 60.0))
        if total > 0:
            for k in enabling_scores:
                enabling_scores[k] = max(10, min(90, int(round(enabling_scores[k] / total))))
        return {k: int(v) for k, v in enabling_scores.items()}

    # FIX: return 0 for empty text
    @classmethod
    def _evaluate_grammar(cls, text: str) -> int:
        if not text or not text.strip():
            return 0
        sentences = text.count('.') + text.count('!') + text.count('?') or 1
        words = len(text.split())
        avg = words / sentences if sentences else 0
        if 15 <= avg <= 25: score = 75
        elif 10 <= avg <= 30: score = 65
        elif 5 <= avg <= 35: score = 55
        else: score = 45
        if text[0].isupper(): score += 5
        if text.rstrip().endswith(('.', '!', '?')): score += 5
        return max(0, min(90, score))

    @classmethod
    def _evaluate_vocabulary(cls, text: str) -> int:
        if not text or not text.strip():
            return 0
        words = text.split()
        if not words: return 0
        richness = len(set(words)) / len(words)
        academic_ratio = sum(1 for w in words if w.lower() in cls.ACADEMIC_WORDS) / len(words)
        if richness > 0.6 and academic_ratio > 0.15: return 85
        if richness > 0.5 and academic_ratio > 0.10: return 75
        if richness > 0.4 and academic_ratio > 0.05: return 65
        if richness > 0.3: return 55
        return 45

    @classmethod
    def _evaluate_spelling(cls, text: str) -> int:
        if not text or not text.strip():
            return 0
        words = text.lower().split()
        if not words: return 0
        errors = sum(1 for w in words if w in cls.COMMON_SPELLING_ERRORS)
        rate = errors / len(words)
        if rate == 0: return 85
        if rate < 0.02: return 75
        if rate < 0.05: return 65
        if rate < 0.10: return 55
        return 45

    @classmethod
    def _evaluate_discourse(cls, text: str) -> int:
        if not text or not text.strip():
            return 0
        markers = sum(1 for m in cls.DISCOURSE_MARKERS if m in text.lower())
        paragraphs = text.count('\n\n') + 1
        if markers >= 3 and paragraphs >= 3: return 85
        if markers >= 2 and paragraphs >= 2: return 75
        if markers >= 1 or paragraphs >= 2: return 65
        if markers >= 1: return 55
        return 45

    # ═══════════════════════════════════════════════════════════════════
    # CONVERSIONS
    # ═══════════════════════════════════════════════════════════════════

    @classmethod
    def pte_to_ielts(cls, pte_score: int) -> float:
        if pte_score >= 90: return 9.0
        if pte_score >= 10:
            closest = min(cls.PTE_TO_IELTS.keys(), key=lambda x: abs(x - pte_score))
            return cls.PTE_TO_IELTS[closest]
        return 0.0

    @classmethod
    def ielts_to_pte(cls, ielts_band: float) -> int:
        rounded = round(ielts_band * 2) / 2
        closest = min(cls.IELTS_TO_PTE.keys(), key=lambda x: abs(x - rounded))
        return cls.IELTS_TO_PTE[closest]

    @classmethod
    def get_score_descriptor(cls, score: int) -> str:
        for (lo, hi), desc in cls.SCORE_DESCRIPTORS.items():
            if lo <= score <= hi: return desc
        return "Score not rated"

    @classmethod
    def get_percentile(cls, score: int) -> int:
        closest = min(cls.PERCENTILES.keys(), key=lambda x: abs(x - score))
        return cls.PERCENTILES[closest]

    # ═══════════════════════════════════════════════════════════════════
    # BACKWARD-COMPAT HELPERS
    # ═══════════════════════════════════════════════════════════════════

    @classmethod
    def calculate_partial_credit(cls, correct, total, incorrect=0, negative_marking=False) -> float:
        if total == 0: return 0.0
        if negative_marking:
            return max(0, correct - incorrect) / total
        return correct / total

    @classmethod
    def calculate_ai_confidence_score(cls, response: str, expected_pattern=None) -> Dict[str, float]:
        conf = {'content_relevance': 0.0, 'grammar_accuracy': 0.0,
                'vocabulary_appropriateness': 0.0, 'overall_confidence': 0.0}
        if not response or not response.strip(): return conf
        wc = len(response.split())
        conf['content_relevance'] = min(1.0, wc / 50)
        sentences = response.count('.') + response.count('!') + response.count('?') or 1
        conf['grammar_accuracy'] = min(1.0, (wc / sentences) / 20)
        if wc > 0:
            conf['vocabulary_appropriateness'] = min(1.0, len(set(response.lower().split())) / wc * 2)
        conf['overall_confidence'] = (
            conf['content_relevance'] * 0.4 +
            conf['grammar_accuracy'] * 0.3 +
            conf['vocabulary_appropriateness'] * 0.3
        )
        return conf

    @classmethod
    def is_score_valid(cls, score: int, section: Optional[str] = None) -> bool:
        return cls.SCORE_SCALE[0] <= score <= cls.SCORE_SCALE[1]

    @classmethod
    def format_score_report(cls, scores_dict, include_enabling=True, include_percentile=True) -> str:
        lines = ["=" * 60, "PTE ACADEMIC SCORE REPORT - 2026", "=" * 60]
        overall = scores_dict.get('overall', 0)
        lines.append(f"\n Overall Score: {overall}/90")
        lines.append(f" Descriptor: {cls.get_score_descriptor(overall)}")
        if include_percentile:
            lines.append(f" Percentile: {cls.get_percentile(overall)}%")
        lines.append(f" IELTS Equivalent: {cls.pte_to_ielts(overall):.1f}")
        lines.append("\n" + "=" * 60)
        lines.append(" COMMUNICATIVE SKILLS")
        lines.append("=" * 60)
        for skill in cls.COMMUNICATIVE_SKILLS:
            lines.append(f" {skill.upper():10} : {scores_dict.get(skill, 0)}/90")
        if include_enabling and 'enabling' in scores_dict:
            lines.append("\n" + "=" * 60)
            lines.append(" ENABLING SKILLS")
            lines.append("=" * 60)
            for skill, score in scores_dict['enabling'].items():
                lines.append(f" {skill.replace('_', ' ').title():18} : {score}/90")
        lines.append("\n" + "=" * 60)
        lines.append(" AI Scoring Engine: PTE Academic 2026")
        lines.append("=" * 60)
        return "\n".join(lines)

    @classmethod
    def compare_scores(cls, score1: int, score2: int) -> Dict[str, Any]:
        diff = score2 - score1
        pct = (diff / score1) * 100 if score1 > 0 else 0
        if diff > 10: assessment = "Significant improvement"
        elif diff > 5: assessment = "Moderate improvement"
        elif diff > 0: assessment = "Slight improvement"
        elif diff == 0: assessment = "No change"
        elif diff > -5: assessment = "Slight decline"
        elif diff > -10: assessment = "Moderate decline"
        else: assessment = "Significant decline"
        return {
            'score1': score1, 'score2': score2, 'difference': diff,
            'percent_change': round(pct, 1), 'assessment': assessment,
            'descriptor1': cls.get_score_descriptor(score1),
            'descriptor2': cls.get_score_descriptor(score2),
            'ielts1': cls.pte_to_ielts(score1), 'ielts2': cls.pte_to_ielts(score2)
        }

    @classmethod
    def get_scoring_summary_2026(cls) -> Dict[str, Any]:
        return {
            'test_name': 'PTE Academic',
            'year': 2026,
            'score_range': '10-90',
            'scoring_algorithm': 'AI-Enhanced Weighted Scoring',
            'section_weights': cls.QUESTION_WEIGHTS_2026,
            'score_descriptors': cls.SCORE_DESCRIPTORS,
            'ielts_conversion': {
                'pte_90': 'ielts_9.0', 'pte_79': 'ielts_8.0',
                'pte_65': 'ielts_7.0', 'pte_50': 'ielts_6.0', 'pte_42': 'ielts_5.5'
            }
        }


pte_scoring = PTEScoring()

__all__ = ['PTEScoring', 'pte_scoring']