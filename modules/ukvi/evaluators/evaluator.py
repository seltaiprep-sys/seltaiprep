# modules/ukvi/evaluators/evaluator.py
"""
UKVI Evaluation — Real UKVI standards (AI-based scoring)

Scoring model:
  - Language: 4 independent IELTS criteria (AI-scored)
  - Content:  Profile consistency + contradiction detection
  - Genuineness: Memorization/template detection
  - Credibility: Weighted 0-100 (language 40, content 30, consistency 20, genuineness 10)

Backward compatible with old signature:
  evaluate(question, answer, category='general')
  final_evaluation(evaluations, profile)
"""

import json
import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


IELTS_CRITERIA = ['fluency', 'grammar', 'vocabulary', 'coherence']

MEMORIZED_PATTERNS = [
    r"i (?:am|have) always (?:wanted|dreamed|dreamt) to",
    r"it has (?:always )?been my dream",
    r"from my childhood",
    r"since (?:my )?childhood",
    r"as (?:we all|everyone) knows",
    r"in today'?s (?:modern )?world",
    r"it is (?:a )?well[- ]known (?:fact|that)",
    r"first of all,? secondly",
    r"last but not (?:the )?least",
]

NUMBER_WORDS = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
    'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18,
    'nineteen': 19, 'twenty': 20, 'thirty': 30, 'forty': 40,
    'fifty': 50, 'sixty': 60, 'seventy': 70, 'eighty': 80,
    'ninety': 90,
}

MULTIPLIERS = {
    'k': 1000, 'thousand': 1000,
    'lakh': 100000, 'lac': 100000,
    'crore': 10000000,
    'million': 1000000, 'm': 1000000,
    'billion': 1000000000, 'b': 1000000000,
}

KNOWN_UNIVERSITIES = [
    'oxford', 'cambridge', 'harvard', 'yale', 'stanford', 'mit',
    'london', 'manchester', 'edinburgh', 'leeds', 'birmingham',
    'bristol', 'warwick', 'nottingham', 'sheffield', 'southampton',
    'glasgow', 'newcastle', 'cardiff', 'queen mary', 'lse',
    'imperial', 'ucl', 'kings college',
]


class UKVIEvaluator:
    """AI-based UKVI interview evaluator (real standards)."""

    def __init__(self, ai_engine):
        self.ai = ai_engine

    # ═══════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════

    def evaluate(self, question, answer, category='general',
                 profile=None, previous_answers=None, previous_qa=None):
        """
        Evaluate one answer.
        profile:          dict from UKVIUser.get_profile() or UKVIB2BStudent
        previous_answers: list of prior answer strings (for genuineness check)
        """
        if not answer or len(answer.strip()) < 3:
            return self._empty_response(category)

        answer = answer.strip()

        # 1. Language (AI 4 criteria)
        language = self._score_language(question, answer, category)

        # 2. Profile consistency
        contradictions = []
        if profile:
            contradictions.extend(
                self._check_profile_consistency(answer, category, profile)
            )
            if category == 'financial':
                contradictions.extend(self._check_financial(answer, profile))

        # 2b. Cross-answer consistency (NEW)
        if previous_qa:
            contradictions.extend(
                self._check_cross_answer(answer, category, previous_qa)
            )

        # 2c. Post-study plan check
        if category in ('post_study', 'post_study_plan', 'plans') or \
           any(k in (question or '').lower() for k in
               ['after your studies', 'after mba', 'future plan',
                'return to', 'come back to', 'post-study']):
            contradictions.extend(
                self._check_post_study_plan(answer, profile or {})
            )

        # 2d. Financial plausibility
        if category == 'financial' and profile:
            contradictions.extend(
                self._check_financial_plausibility(answer, profile)
            )

        # 2e. Immigration history
        if category in ('immigration', 'visa_history') or \
           any(k in (question or '').lower() for k in
               ['visa refused', 'visa refusal', 'immigration history',
                'refused before', 'rejected before']):
            contradictions.extend(
                self._check_immigration_history(answer, profile or {})
            )

        # 3. Genuineness — prefer AI from language call
        genuineness = self._check_genuineness(answer, previous_answers or [])
        if language.get('ai_genuineness') is not None:
            ai_g = language['ai_genuineness']
            kw_g = genuineness['score']
            genuineness['score'] = int(ai_g * 0.7 + kw_g * 0.3)
            if language.get('ai_genuineness_reason'):
                genuineness['flags'].append(
                    'AI: ' + language['ai_genuineness_reason']
                )
            genuineness['ai_used'] = True

        # 4. Content score
        content_score = self._score_content(
            answer, category, contradictions, genuineness
        )

        # 4b. CAP content by fluency — real UKVI: can't have high content
        #     when speech is incoherent/hesitant
        if language['fluency'] < 6.5:
            cap = language['fluency'] + 1.5  # e.g. fluency 5.5 → cap 7.0
            content_score = min(content_score, cap)
        if language['coherence'] < 6.0:
            cap = language['coherence'] + 1.5
            content_score = min(content_score, cap)

        # 5. Combine (content matters more in UKVI)
        overall = round(language['overall'] * 0.4 + content_score * 0.6, 1)

        # 6. Feedback
        feedback = self._generate_feedback(
            language, contradictions, genuineness, content_score
        )

        return {
            'overall_score': overall,
            'fluency': language['fluency'],
            'grammar': language['grammar'],
            'vocabulary': language['vocabulary'],
            'coherence': language['coherence'],
            'content_score': round(content_score, 1),
            'contradictions': contradictions,
            'genuineness_flags': genuineness['flags'],
            'genuineness_score': genuineness['score'],
            'feedback': feedback,
            'word_count': len(answer.split()),
            'category': category,
        }

    def final_evaluation(self, evaluations, profile=None):
        """Aggregate + real credibility score."""
        if not evaluations:
            return {
                'overall_score': 0.0,
                'recommendations': ['No answers provided'],
                'credibility_avg': 0,
                'breakdown': {},
                'total_contradictions': 0,
                'total_genuineness_flags': 0,
                'contradictions': [],
            }

        # Averages
        overall_scores = [e.get('overall_score', 0) for e in evaluations
                          if isinstance(e.get('overall_score'), (int, float))]
        avg = round(sum(overall_scores) / len(overall_scores), 1) if overall_scores else 0.0

        breakdown = {}
        for criterion in IELTS_CRITERIA:
            vals = [e.get(criterion, 0) for e in evaluations
                    if isinstance(e.get(criterion), (int, float))]
            breakdown[criterion] = round(sum(vals) / len(vals), 1) if vals else 0.0

        content_vals = [e.get('content_score', 0) for e in evaluations
                        if isinstance(e.get('content_score'), (int, float))]
        breakdown['content'] = round(sum(content_vals) / len(content_vals), 1) if content_vals else 0.0

        all_contradictions = []
        for e in evaluations:
            all_contradictions.extend(e.get('contradictions', []) or [])

        all_gen_flags = []
        for e in evaluations:
            all_gen_flags.extend(e.get('genuineness_flags', []) or [])

        credibility = self._calculate_credibility(
            breakdown, all_contradictions, all_gen_flags
        )

        # Recommendations
        weak_lang = [k for k in IELTS_CRITERIA if breakdown.get(k, 0) < 6.0]
        if all_contradictions:
            rec = (f"🔴 {len(all_contradictions)} contradiction(s) detected — "
                   f"HIGH RISK. Fix before real interview.")
        elif weak_lang:
            rec = (f"⚠️ Language weak in {', '.join(weak_lang)} — "
                   f"practice more. Credibility capped due to language.")
        elif credibility >= 80:
            rec = "✅ Strong performance — consistent with application, high credibility"
        elif credibility >= 60:
            rec = "⚠️ Acceptable — address minor gaps before real interview"
        else:
            rec = "🔴 High risk — review application and language ability"

        recommendations = [rec]
        if all_contradictions:
            recommendations.append(
                f"Fix {len(all_contradictions)} contradiction(s): "
                + "; ".join(all_contradictions[:3])
            )
        if all_gen_flags:
            recommendations.append(
                "Reduce rehearsed phrases — practice spontaneous answers"
            )
        weak = [k for k in IELTS_CRITERIA if breakdown.get(k, 0) < 6.0]
        if weak:
            recommendations.append(
                f"Practice {', '.join(weak)} — below IELTS 6.0 threshold"
            )

        return {
            'overall_score': avg,
            'recommendations': recommendations,
            'credibility_avg': credibility,
            'breakdown': breakdown,
            'total_contradictions': len(all_contradictions),
            'total_genuineness_flags': len(all_gen_flags),
            'contradictions': all_contradictions,
        }

    # ═══════════════════════════════════════════════════════
    # LANGUAGE SCORING
    # ═══════════════════════════════════════════════════════

    def _score_language(self, question, answer, category):
        # Try AI first
        if self.ai:
            try:
                prompt = (
                    "You are an IELTS Speaking examiner. Score this answer on 4 criteria, "
                    "each 0-9 with 0.5 increments.\n\n"
                    f"Question: {question}\n"
                    f"Answer: {answer}\n\n"
                    "Return ONLY valid JSON:\n"
                    '{"fluency": <n>, "grammar": <n>, "vocabulary": <n>, '
                    '"coherence": <n>, '
                    '"genuineness": <0-100>, '  # 0=memorized, 100=spontaneous
                    '"genuineness_reason": "<short>", '
                    '"feedback": "<one sentence>"}\n\n'
                    "Rubric:\n"
                    "- fluency: hesitation, pace, natural flow\n"
                    "- grammar: tense accuracy, sentence structure, complex forms\n"
                    "- vocabulary: range, precision, idiomatic use\n"
                    "- coherence: logical flow, connectors, complete thoughts\n\n"
                    "CALIBRATION:\n"
                    "- Short but grammatically correct answers = 6.0-6.5 (not 5.0)\n"
                    "- Errors that don't impede = 5.5-6.0\n"
                    "- Frequent errors with comprehension issues = 4.5-5.5\n"
                    "- Only score below 4.5 for severe breakdown\n\n"
                    "Do NOT base scores on word count. Score on actual language quality."
                )
                result = self._call_ai_json(prompt)
                if result and all(k in result for k in IELTS_CRITERIA):
                    scores = {k: self._clamp(result[k]) for k in IELTS_CRITERIA}
                    scores['overall'] = round(
                        sum(scores[k] for k in IELTS_CRITERIA) / 4, 1
                    )
                    scores['feedback'] = result.get('feedback', '')
                    # Capture genuineness from combined prompt
                    if 'genuineness' in result:
                        scores['ai_genuineness'] = self._clamp(result['genuineness'], 0, 100)
                        scores['ai_genuineness_reason'] = result.get('genuineness_reason', '')
                    return scores
            except Exception as e:
                logger.warning(f'AI language scoring failed: {e}')

        # Fallback heuristic (smarter than word count)
        return self._heuristic_language_score(answer)

    def _heuristic_language_score(self, answer):
        words = answer.lower().split()
        wc = len(words)
        if wc == 0:
            return self._empty_language()

        # Fluency: penalize hesitation words
        # Expanded hesitation detection
        HES_WORDS = ('um', 'uh', 'er', 'ah', 'hmm', 'erm', 'eh')
        HES_PHRASES = ('you know', 'i mean', 'sort of', 'kind of',
                       'or something', 'and stuff', 'basically')
        FILLER_WORDS = ('like', 'actually', 'literally', 'totally',
                        'anyway', 'whatever', 'right')
        hesitations = sum(1 for w in words if w in HES_WORDS)
        hesitations += sum(1 for p in HES_PHRASES
                          if p in answer.lower())
        # Filler words — count once, 'like' handled specially
        for fw in FILLER_WORDS:
            c = words.count(fw)
            if fw == 'like':
                # 'like' as filler only if >2 occurrences (first may be verb)
                if c > 2:
                    hesitations += c - 1
            elif c > 1:
                hesitations += c - 1
        hesitation_ratio = hesitations / max(1, wc)
        fluency = 8.0 - hesitation_ratio * 20
        fluency = max(4.0, min(9.0, fluency))

        # Vocabulary: unique word ratio
        unique_ratio = len(set(words)) / wc
        vocab = 4.5 + unique_ratio * 4.5
        vocab = max(4.0, min(9.0, vocab))

        # Grammar: sentence complexity (connectors, sentence length)
        connectors = [
            'because', 'although', 'however', 'therefore',
            'while', 'whereas', 'since', 'if', 'when',
            'moreover', 'furthermore', 'nevertheless',
        ]
        connector_count = sum(1 for c in connectors if c in answer.lower())
        sentences = [s.strip() for s in re.split(r'[.!?]+', answer) if s.strip()]
        num_sentences = max(1, len(sentences))
        avg_len = sum(len(s.split()) for s in sentences) / num_sentences

        # Base grammar — connectors show complex structures
        grammar = 5.5 + min(1.5, connector_count * 0.4)

        # BONUS: multiple sentences (real complex grammar)
        if num_sentences >= 2:
            grammar += 0.7
        if num_sentences >= 3:
            grammar += 0.5

        # BONUS: reasonable sentence length (not too short, not run-on)
        if 8 <= avg_len <= 20:
            grammar += 0.5

        # PENALTY: very long single sentence = run-on
        if avg_len > 25 and num_sentences == 1:
            grammar -= 1.5  # Run-on sentence
        elif avg_len > 30:
            grammar -= 0.8  # Ramble

        # PENALTY: hesitation words reduce grammaticality (real IELTS)
        if hesitation_ratio > 0.1:
            grammar -= hesitation_ratio * 10

        # PENALTY: very short answer (no evidence of grammar range)
        if wc < 8:
            grammar = min(grammar, 6.0)

        grammar = max(3.5, min(9.0, grammar))

        # Coherence: connectors + moderate length (NOT word count reward)
        # Length only helps up to ~20 words; beyond that no bonus
        length_bonus = min(2.0, (min(wc, 20) - 5) * 0.1)
        coherence = 5.0 + length_bonus + connector_count * 0.3
        # Penalize hesitations in coherence too
        coherence -= hesitation_ratio * 15
        coherence = max(3.0, min(9.0, coherence))

        return {
            'fluency': round(fluency, 1),
            'grammar': round(grammar, 1),
            'vocabulary': round(vocab, 1),
            'coherence': round(coherence, 1),
            'overall': round((fluency + grammar + vocab + coherence) / 4, 1),
            'feedback': '',
        }

    # ═══════════════════════════════════════════════════════
    # PROFILE CONSISTENCY
    # ═══════════════════════════════════════════════════════

    def _check_profile_consistency(self, answer, category, profile):
        issues = []
        ans = answer.lower()

        # University
        if profile.get('university') and category in ('education', 'general'):
            uni = profile['university'].lower()
            uni_kw = [w for w in re.findall(r'\b[a-z]{4,}\b', uni)
                      if w not in ('university', 'college', 'school', 'institute')]
            mentioned = [u for u in KNOWN_UNIVERSITIES
                         if u in ans and u not in uni_kw]
            if mentioned and not any(k in ans for k in uni_kw):
                issues.append(
                    f"University mismatch: profile='{profile['university']}' "
                    f"but answer mentions '{mentioned[0]}'"
                )

        # Course
        if profile.get('course') and category == 'education':
            course = profile['course'].lower()
            course_kw = [w for w in re.findall(r'\b[a-z]{4,}\b', course)
                         if w not in ('course', 'study', 'degree')]
            other = [c for c in ('engineering', 'medicine', 'nursing', 'law',
                                  'accounting', 'commerce', 'arts', 'economics')
                     if c in ans and c not in course]
            if other and not any(k in ans for k in course_kw):
                issues.append(
                    f"Course mismatch: profile='{profile['course']}' "
                    f"but answer mentions '{other[0]}'"
                )

        return issues

    def _check_financial(self, answer, profile):
        issues = []
        ans_nums = self._extract_numbers(answer)

        if not ans_nums:
            if any(k in answer.lower() for k in
                   ('tuition', 'fee', 'cost', 'pay', 'fund')):
                issues.append(
                    "Financial answer lacks specific figures — "
                    "state tuition and living costs"
                )
            return issues

        # Compare tuition
        if profile.get('tuition_fee'):
            prof_nums = self._extract_numbers(profile['tuition_fee'])
            if prof_nums:
                prof_tuition = prof_nums[0]
                # Check if ANY mentioned number is close to profile tuition
                matched = any(
                    abs(n - prof_tuition) <= prof_tuition * 0.20  # 20% tolerance
                    for n in ans_nums
                )
                if not matched:
                    # Find the closest number (likely tuition reference)
                    closest = min(ans_nums, key=lambda n: abs(n - prof_tuition))
                    # Only flag if closest is between 30% and 300% of profile
                    # (numbers outside this range are probably living cost, income, etc.)
                    if prof_tuition * 0.3 <= closest <= prof_tuition * 3.0:
                        issues.append(
                            f"Tuition mismatch: profile says "
                            f"'{profile['tuition_fee']}' but answer mentions "
                            f"~{self._format_num(closest)}"
                        )

        return issues

    # ═══════════════════════════════════════════════════════
    # GENUINENESS
    # ═══════════════════════════════════════════════════════

    def _check_genuineness(self, answer, previous_answers):
        """Keyword-based genuineness (fallback) + AI semantic when available."""
        flags = []
        ans = answer.lower()

        # Memorized patterns
        for pat in MEMORIZED_PATTERNS:
            if re.search(pat, ans):
                flags.append("Generic phrase: '" + pat + "'")

        # High overlap with previous answers
        for prev in previous_answers[-3:]:
            if not prev or len(prev.split()) < 15:
                continue
            prev_w = set(prev.lower().split())
            ans_w = set(ans.split())
            if not ans_w:
                continue
            overlap = len(prev_w & ans_w) / len(ans_w)
            if overlap > 0.7:
                flags.append(
                    "High overlap with previous answer ("
                    + str(int(overlap * 100)) + "%) — sounds rehearsed"
                )
                break

        # AI semantic check (NEW)
        if self.ai:
            try:
                prompt = (
                    "You are a UKVI credibility officer. Rate this answer's GENUINENESS.\n\n"
                    "Answer: " + answer + "\n\n"
                    "Genuine = specific, personal, spontaneous, inconsistent-topic-free.\n"
                    "Memorized = template-like, generic phrases, no specifics, essay-style.\n\n"
                    "Return ONLY JSON:\n"
                    '{"score": <0-100>, "reason": "<one short sentence>", '
                    '"is_memorized": <true|false>}'
                )
                result = self._call_ai_json(prompt)
                if result and 'score' in result:
                    ai_score = self._clamp(result['score'], 0, 100)
                    if result.get('is_memorized'):
                        flags.append("AI: appears memorized — " + result.get('reason', ''))
                    # Blend: AI 70% + keyword 30%
                    keyword_score = max(0, 100 - len(flags) * 20)
                    score = int(ai_score * 0.7 + keyword_score * 0.3)
                    return {'flags': flags, 'score': score, 'ai_used': True}
            except Exception as e:
                logger.warning('AI genuineness failed: %s', e)

        score = max(0, 100 - len(flags) * 20)
        return {'flags': flags, 'score': score, 'ai_used': False}

    def _check_cross_answer(self, answer, category, previous_qa):
        """Detect contradictions with previous answers."""
        issues = []
        if not previous_qa:
            return issues

        ans = answer.lower()
        ans_nums = self._extract_numbers(answer)

        for prev in previous_qa:
            if not prev or not prev.get('answer'):
                continue
            prev_cat = prev.get('category', '')
            prev_ans = prev.get('answer', '').lower()
            prev_q = prev.get('question', '')[:60]

            # Cross-category entity comparison (university, city)
            if category in ('education', 'general') and prev_cat in ('education', 'general'):
                cur_unis = [u for u in KNOWN_UNIVERSITIES if u in ans]
                prev_unis = [u for u in KNOWN_UNIVERSITIES if u in prev_ans]
                if cur_unis and prev_unis and set(cur_unis) != set(prev_unis):
                    issues.append(
                        "Cross-answer: university changed ('"
                        + prev_unis[0] + "' → '" + cur_unis[0] + "')"
                    )

            # Financial numbers changed significantly
            if category == 'financial' or prev_cat == 'financial':
                prev_nums = self._extract_numbers(prev['answer'])
                for an in ans_nums:
                    for pn in prev_nums:
                        if pn > 1000 and an > 1000:
                            diff = abs(an - pn) / max(pn, an)
                            if 0.25 < diff < 5.0:  # significant but same ballpark
                                if self._numbers_related(an, pn):
                                    issues.append(
                                        "Cross-answer: financial figure changed ("
                                        + self._format_num(pn) + " → "
                                        + self._format_num(an) + ")"
                                    )
                                    break

        # AI cross-answer check (NEW — semantic contradictions)
        if self.ai and len(previous_qa) >= 1:
            try:
                ctx = "\n".join(
                    "Q" + str(i+1) + " (" + (p.get('category','') or '') + "): "
                    + (p.get('question', '') or '')[:80] + "\n"
                    + "A: " + (p.get('answer', '') or '')[:200]
                    for i, p in enumerate(previous_qa[-3:])
                )
                prompt = (
                    "Detect contradictions between previous answers and this new answer.\n\n"
                    "PREVIOUS:\n" + ctx + "\n\n"
                    "NEW answer: " + answer + "\n\n"
                    "Look for: different universities, different courses, different cities, "
                    "different financial numbers, changed story about sponsor.\n"
                    "Do NOT flag normal topic changes or elaboration.\n\n"
                    "Return ONLY JSON:\n"
                    '{"contradictions": [{"issue": "<short description>"}]}\n'
                    'If none: {"contradictions": []}'
                )
                ai_result = self._call_ai_json(prompt)
                if ai_result and 'contradictions' in ai_result:
                    for c in ai_result['contradictions']:
                        issue = c.get('issue', '')
                        if issue:
                            # Avoid duplicate with rule-based
                            if not any(issue.lower() in i.lower() for i in issues):
                                issues.append("AI cross-answer: " + issue[:80])
            except Exception as e:
                logger.warning('AI cross-answer failed: %s', e)

        return issues

    def _numbers_related(self, a, b):
        """True if two numbers are in the same order of magnitude (tuition vs tuition)."""
        if a <= 0 or b <= 0:
            return False
        ratio = max(a, b) / min(a, b)
        return ratio < 3.0  # within 3x → likely same concept, different value


    # ═══════════════════════════════════════════════════════
    # CONTENT SCORE
    # ═══════════════════════════════════════════════════════

    def _score_content(self, answer, category, contradictions, genuineness):
        base = 7.5

        # Penalty: contradictions (each = -2)
        base -= len(contradictions) * 2.0

        # Penalty: genuineness flags (each = -1)
        base -= len(genuineness.get('flags', [])) * 1.0

        # Bonus: specific details (proper nouns, numbers, but NOT sentence-start)
        wc = len(answer.split())
        # Real proper noun: capitalized word not at sentence start, length >= 4
        # Split on sentence boundaries to identify sentence starters
        sentence_starts = set()
        for s in re.split(r'[.!?]\s+', answer):
            if s.strip():
                sentence_starts.add(s.strip().split()[0].lower().rstrip('.,!?'))
        proper_nouns = re.findall(r'\b[A-Z][a-z]{2,}\b', answer)
        real_proper = [p for p in proper_nouns if p.lower() not in sentence_starts]
        has_numbers = bool(re.search(r'\d{2,}', answer))  # 2+ digits
        if (real_proper or has_numbers) and wc >= 20:
            base += 0.5

        # Penalty: too short
        if wc < 10:
            base -= 1.5
        elif wc < 20:
            base -= 0.5

        return max(0.0, min(9.0, base))

    # ═══════════════════════════════════════════════════════
    # CREDIBILITY
    # ═══════════════════════════════════════════════════════


    # ═══════════════════════════════════════════════════════
    # POST-STUDY PLAN (Gap 3)
    # ═══════════════════════════════════════════════════════

    def _check_post_study_plan(self, answer, profile):
        """Detect 'return to home country' intention (real UKVI critical check)."""
        issues = []
        ans = (answer or '').lower()
        home = (profile.get('home_country') or 'Nepal').lower()

        # Positive: return intent
        return_keywords = [
            'return', 'back to', 'go home', 'home country', 'start my own',
            'start a business', 'contribute', 'develop', 'give back',
            'in ' + home, 'to ' + home, 'family business',
        ]
        has_return = any(k in ans for k in return_keywords)

        # Negative: intent to stay permanently (immigration risk)
        stay_keywords = [
            'settle in uk', 'settle permanently', 'stay forever',
            'never return', 'live in uk permanently', 'permanent residency',
            'stay in uk forever', 'never go back', 'make uk my home',
        ]
        has_stay = any(k in ans for k in stay_keywords)

        if has_stay:
            issues.append(
                "Immigration risk: student intends to stay in UK permanently — "
                "officers flag this as high risk"
            )
        elif not has_return and len(ans.split()) >= 15:
            issues.append(
                "Post-study plan unclear — officer will ask 'Will you return to "
                + home.title() + "?' Add specific plans"
            )

        return issues

    # ═══════════════════════════════════════════════════════
    # FINANCIAL PLAUSIBILITY (Gap 4)
    # ═══════════════════════════════════════════════════════

    def _check_financial_plausibility(self, answer, profile):
        """Check if sponsor income + savings realistically cover total cost."""
        issues = []
        try:
            tuition_text = (profile.get('tuition_fee') or '')
            living_text = (profile.get('living_cost') or '')
            income_text = (profile.get('sponsor_income') or '')
            savings_text = (profile.get('sponsor_savings') or '')

            tuition_nums = self._extract_numbers(tuition_text)
            living_nums = self._extract_numbers(living_text)
            income_nums = self._extract_numbers(income_text)
            savings_nums = self._extract_numbers(savings_text)

            if not tuition_nums or not income_nums:
                return issues

            GBP_TO_NPR = 160

            def _is_gbp(text, num):
                """Detect GBP by currency symbol or realistic UK amount range."""
                t = (text or '').lower()
                if '£' in t or 'gbp' in t or 'pound' in t:
                    return True
                if 'npr' in t or 'rs' in t or 'rupee' in t:
                    return False
                # Heuristic: UK tuition typical 5000-50000; UK living 5000-25000
                return 3000 <= num <= 60000

            def _to_npr(text, num):
                if _is_gbp(text, num):
                    return num * GBP_TO_NPR
                return num

            tuition_npr = _to_npr(tuition_text, tuition_nums[0])
            living_npr = _to_npr(living_text, living_nums[0]) if living_nums else 0
            income_npr = _to_npr(income_text, income_nums[0])
            savings_npr = _to_npr(savings_text, savings_nums[0]) if savings_nums else 0

            # Assume 2-year course
            total_cost = (tuition_npr + living_npr) * 2
            total_available = (income_npr * 2) + savings_npr

            if total_cost <= 0:
                return issues

            coverage = total_available / total_cost if total_cost > 0 else 1.0

            if coverage < 0.75:
                issues.append(
                    "Financial plausibility: 2-year cost ~NPR "
                    + str(int(total_cost / 100000)) + "L but income+savings "
                    + "~NPR " + str(int(total_available / 100000)) + "L "
                    + "(covers only " + str(int(coverage * 100)) + "%)"
                )
            elif coverage < 1.0:
                issues.append(
                    "Financial: income+savings cover "
                    + str(int(coverage * 100)) + "% of cost — may need "
                    + "additional sponsor or bank loan"
                )
        except Exception as e:
            logger.debug('Financial plausibility check failed: %s', e)
        return issues

    def _check_immigration_history(self, answer, profile):
        """Detect inconsistent or evasive answers about visa history."""
        issues = []
        ans = (answer or '').lower()

        # Keywords indicating refusal discussion
        refusal_keywords = [
            'refused', 'refusal', 'rejected', 'denied', 'visa denied',
            'turned down', 'not granted',
        ]
        has_refusal = any(k in ans for k in refusal_keywords)

        # Evasive patterns (red flag when asked about refusals)
        evasive = [
            'i don\'t remember', 'i don\'t know', 'not sure',
            'maybe', 'i think no', 'probably not',
        ]
        is_evasive = any(e in ans for e in evasive)

        if is_evasive and len(ans.split()) < 20:
            issues.append(
                "Evasive answer about immigration history — officer will "
                "cross-check visa records. Be direct"
            )

        # If profile has previous_visa_refusal flag (if added later)
        if profile.get('previous_visa_refusal') and not has_refusal:
            if any(k in ans for k in ['no refusal', 'never refused', 'first time']):
                issues.append(
                    "Contradiction: profile shows previous visa refusal "
                    "but answer denies it"
                )

        return issues

    def _calculate_credibility(self, breakdown, contradictions, gen_flags):
        # Language 40%
        lang = sum(breakdown.get(k, 0) for k in IELTS_CRITERIA) / 4
        lang_comp = (lang / 9) * 40

        # Content 30%
        content = breakdown.get('content', 0)
        content_comp = (content / 9) * 30

        # Consistency 20%
        consistency_comp = (20 if not contradictions
                            else max(0, 20 - len(contradictions) * 5))

        # Genuineness 10%
        gen_comp = (10 if not gen_flags
                    else max(0, 10 - len(gen_flags) * 3))

        raw = lang_comp + content_comp + consistency_comp + gen_comp

        # ── CEILING CHECK: weak language caps credibility ──
        # Real UKVI: grammar < 5.5 or coherence < 5.5 → cap at 65
        # Real UKVI: fluency < 5.0 → cap at 50
        grammar = breakdown.get('grammar', 9.0)
        coherence = breakdown.get('coherence', 9.0)
        fluency = breakdown.get('fluency', 9.0)

        if grammar < 5.0 or coherence < 5.0:
            raw = min(raw, 55)  # Serious language deficit
        elif grammar < 5.5 or coherence < 5.5:
            raw = min(raw, 65)  # Language below IELTS 6 threshold
        elif grammar < 6.0 or coherence < 6.0:
            raw = min(raw, 75)  # Borderline language ability

        if fluency < 5.0:
            raw = min(raw, 50)  # Cannot maintain speech flow

        # ── FLOOR CHECK: any contradiction hard-caps ──
        if contradictions:
            raw = min(raw, 60 - len(contradictions) * 5)
        if len(contradictions) >= 2:
            raw = min(raw, 40)  # Multiple contradictions = high risk

        return round(max(0, min(100, raw)))

    # ═══════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════

    def _generate_feedback(self, language, contradictions, genuineness, content_score):
        parts = []

        if contradictions:
            parts.append("⚠️ Consistency: " + "; ".join(contradictions))

        weak = [k for k in IELTS_CRITERIA if language[k] < 6.0]
        if weak:
            parts.append(f"Language: improve {', '.join(weak)}")
        elif language['overall'] >= 7.5:
            parts.append("Language: strong")

        if genuineness.get('flags'):
            parts.append("Avoid rehearsed phrases — be specific and personal")

        if content_score < 5.0:
            parts.append("Content: add specific details (numbers, names, examples)")
        elif content_score >= 7.5:
            parts.append("Content: well-detailed ✅")

        return " | ".join(parts) if parts else "Good response."

    def _extract_numbers(self, text):
        """Extract numeric values (handles 25,000 / 25k / lakh / words)."""
        if not text:
            return []
        text = text.lower()
        numbers = []

        # Digits with optional multiplier
        for m in re.finditer(r'([\d,]+\.?\d*)\s*(k|thousand|lakh|lac|crore|million|m|billion|b)?\b', text):
            try:
                num_str = m.group(1).replace(',', '')
                num = float(num_str)
                mult = m.group(2)
                if mult:
                    num *= MULTIPLIERS.get(mult, 1)
                numbers.append(num)
            except ValueError:
                pass

        # Word numbers (only large)
        for word, val in NUMBER_WORDS.items():
            if re.search(r'\b' + word + r'\b', text) and val >= 100:
                # Heuristic: 'twenty five thousand' → 20000 is wrong; just capture word
                # We catch 'thousand' separately so skip here unless big word
                if word in ('hundred', 'thousand', 'lakh', 'lac', 'crore',
                            'million', 'billion'):
                    continue
                # For 'twenty' in 'twenty thousand' — combined detection is complex.
                # Skip word numbers for simplicity; digit extraction suffices.

        return numbers

    def _format_num(self, n):
        if n >= 1000000:
            return f"{n/1000000:.1f}M"
        if n >= 1000:
            return f"{n/1000:.0f}k"
        return str(int(n))

    def _call_ai_json(self, prompt):
        """Try multiple AI interfaces."""
        if not self.ai:
            return None
        for method in ('generate_json', 'generate_structured', 'complete_json'):
            if hasattr(self.ai, method):
                try:
                    return getattr(self.ai, method)(prompt)
                except Exception:
                    pass
        for method in ('generate', 'complete', 'chat'):
            if hasattr(self.ai, method):
                try:
                    text = getattr(self.ai, method)(prompt)
                    if isinstance(text, str):
                        return self._parse_json(text)
                except Exception:
                    pass
        return None

    def _parse_json(self, text):
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            pass
        m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return None

    def _clamp(self, v, lo=0.0, hi=9.0):
        try:
            return round(max(lo, min(hi, float(v))), 1)
        except (TypeError, ValueError):
            return 5.0

    def _empty_response(self, category):
        return {
            'overall_score': 0.0,
            'fluency': 0.0, 'grammar': 0.0,
            'vocabulary': 0.0, 'coherence': 0.0,
            'content_score': 0.0,
            'contradictions': [],
            'genuineness_flags': ['Empty answer'],
            'genuineness_score': 0,
            'feedback': 'No answer provided — cannot evaluate.',
            'word_count': 0,
            'category': category,
        }

    def _empty_language(self):
        return {
            'fluency': 0.0, 'grammar': 0.0,
            'vocabulary': 0.0, 'coherence': 0.0,
            'overall': 0.0, 'feedback': '',
        }
