"""Coherence and cohesion analysis for IELTS Speaking - Graceful Degradation with ML fallback"""

import re
import logging
import numpy as np
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class CoherenceResult:
    """Coherence analysis result"""
    # FIX: default 0.0 (was 5.0)
    score: float = 0.0
    feedback: List[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


class CoherenceAnalyzer:
    """
    Analyze coherence and cohesion in spoken responses.

    Uses sentence-transformers for semantic flow (if available), with
    a pure-statistical fallback to ensure the tool never crashes.

     FIX: Empty/short responses -> score 0.0
     FIX: Minimum 3 sentences required for a real score
    """

    # FIX: MIN_SENTENCES 2 -> 3 (realistic minimum for IELTS)
    MIN_SENTENCES = 3
    MIN_WORDS = 30 # extra safety: fewer than 30 words -> band 0

    JACCARD_GOOD_LOW = 0.2
    JACCARD_GOOD_HIGH = 0.5
    JACCARD_OK_LOW = 0.1
    JACCARD_OK_HIGH = 0.6
    SEMANTIC_GOOD_LOW = 0.4
    SEMANTIC_GOOD_HIGH = 0.6
    SEMANTIC_OK_LOW = 0.3
    SEMANTIC_OK_HIGH = 0.7
    REPETITION_THRESHOLD = 0.10
    MAX_REPETITION_PENALTY = 1.5

    DISCOURSE_MARKERS = {
        'sequencing': ['first', 'firstly', 'second', 'secondly', 'third', 'finally', 'lastly'],
        'adding': ['in addition', 'furthermore', 'moreover', 'besides'],
        'contrast': ['however', 'nevertheless', 'on the other hand', 'although', 'though'],
        'conclusion': ['therefore', 'so', 'overall', 'in conclusion', 'to sum up', 'that is why']
    }

    OPINION_MARKERS = ['i think', 'i believe', 'in my opinion', 'personally', 'from my perspective']
    EXAMPLE_MARKERS = ['for example', 'for instance', 'such as', 'like', 'including']

    # Weights (sums to 1.0)
    WEIGHT_ARGUMENT = 0.30
    WEIGHT_ENTITY = 0.25
    WEIGHT_DISCOURSE = 0.20
    WEIGHT_SEMANTIC = 0.25

    def __init__(self, use_semantic: bool = True):
        """Initialize coherence analyzer."""
        self.use_semantic = use_semantic
        self._embedding_model = None
        self._has_nltk = False

        try:
            import nltk
            nltk.download('punkt_tab', quiet=True)
            self._sent_tokenize = nltk.sent_tokenize
            self._has_nltk = True
            logger.info("NLTK sentence tokenizer loaded")
        except ImportError:
            logger.warning("NLTK not installed. Using regex fallback for sentence splitting.")
            self._sent_tokenize = None

    def _load_embedding_model(self):
        """Lazy load sentence embedding model with graceful fallback."""
        if self._embedding_model is not None:
            return True

        if not self.use_semantic:
            return False

        try:
            from sentence_transformers import SentenceTransformer
            self._embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
            logger.info("Sentence embedding model loaded")
            return True
        except ImportError:
            logger.warning("sentence-transformers not installed. Semantic scoring disabled.")
            self.use_semantic = False
            return False
        except Exception as e:
            logger.warning(f"Failed to load embedding model: {e}. Semantic scoring disabled.")
            self.use_semantic = False
            return False

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences using NLTK or regex fallback."""
        text = text.strip()
        if not text:
            return []

        if self._has_nltk and self._sent_tokenize:
            try:
                return [s.strip() for s in self._sent_tokenize(text) if len(s.strip()) > 3]
            except Exception:
                pass

        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z"\'\(])', text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 3]

        if len(sentences) < 2:
            sentences = re.split(r'[\n\r]+', text)
            sentences = [s.strip() for s in sentences if len(s.strip()) > 3]

        return sentences

    # ============================================================
    # EMPTY / SHORT RESPONSE HANDLER
    # ============================================================

    def _empty_result(self, word_count: int, sentence_count: int, reason: str) -> CoherenceResult:
        """Return a Band 0 result for empty/short input."""
        return CoherenceResult(
            score=0.0,
            feedback=[reason],
            details={
                'sentence_count': sentence_count,
                'word_count': word_count,
                'message': reason,
                'empty': True,
            }
        )

    # ============================================================
    # MAIN ANALYZE
    # ============================================================

    def analyze(self, text: str, topic: str = None) -> CoherenceResult:
        """
        Analyze coherence of spoken text.

         FIX: Empty/short responses -> score 0.0 (was 4.0)
         FIX: Exception -> score 0.0 (was 5.0)
        """
        # FIX: Normalize input
        if text is None:
            text = ""
        text = str(text).strip()
        word_count = len(text.split())

        # FIX: Empty input -> Band 0
        if not text or word_count == 0:
            return self._empty_result(0, 0, "BAND 0 — No response provided.")

        # FIX: Very short input -> Band 0
        if word_count < self.MIN_WORDS:
            return self._empty_result(
                word_count, 0,
                f"BAND 0 — Response too short ({word_count} words). "
                f"At least {self.MIN_WORDS} words are needed for coherence analysis."
            )

        sentences = self._split_sentences(text)

        # FIX: Not enough sentences -> Band 0 (was 4.0)
        if len(sentences) < self.MIN_SENTENCES:
            return self._empty_result(
                word_count, len(sentences),
                f"BAND 0 — Response too short to evaluate coherence "
                f"({len(sentences)} sentences; minimum {self.MIN_SENTENCES})."
            )

        try:
            argument_score = self._score_argument_structure(sentences)
            entity_score = self._score_entity_cohesion(sentences)
            discourse_score = self._score_discourse_markers(sentences)
            semantic_score = self._score_semantic_flow(sentences)
            topical_score = self._score_topical_relevance(sentences, topic)

            repetition_penalty = self._compute_repetition_penalty(text)

            raw_score = (
                self.WEIGHT_ARGUMENT * argument_score +
                self.WEIGHT_ENTITY * entity_score +
                self.WEIGHT_DISCOURSE * discourse_score +
                self.WEIGHT_SEMANTIC * semantic_score
            )

            topical_multiplier = 0.7 + (topical_score - 2.0) * (0.3 / 7.0)
            raw_score *= topical_multiplier

            final_score = raw_score - repetition_penalty

            # FIX: Floor 0.0 (was 2.0)
            final_score = round(min(9.0, max(0.0, final_score)), 1)

            return CoherenceResult(
                score=final_score,
                feedback=self._generate_feedback(
                    argument_score, entity_score, discourse_score,
                    semantic_score, repetition_penalty, topical_score
                ),
                details={
                    'sentence_count': len(sentences),
                    'argument_structure': round(argument_score, 1),
                    'entity_cohesion': round(entity_score, 1),
                    'discourse_markers': round(discourse_score, 1),
                    'semantic_flow': round(semantic_score, 1),
                    'topical_relevance': round(topical_score, 1),
                    'repetition_penalty': round(repetition_penalty, 1),
                    'word_count': word_count
                }
            )

        except Exception as e:
            logger.error(f"Coherence analysis error: {e}")
            # FIX: Exception -> 0.0 (was 5.0)
            return self._empty_result(
                word_count, len(sentences),
                "BAND 0 — Analysis unavailable due to an internal error."
            )

    # ============================================================
    # SCORING FUNCTIONS
    # ============================================================

    def _score_argument_structure(self, sentences: List[str]) -> float:
        """Score the argument/opinion structure."""
        score = 5.0
        full_text = ' '.join(sentences).lower()
        opening = ' '.join(sentences[:2]).lower()

        if any(m in opening for m in self.OPINION_MARKERS):
            score += 1.0

        if any(m in full_text for m in self.EXAMPLE_MARKERS):
            score += 1.5

        closing = sentences[-1].lower()
        if any(m in closing for m in self.DISCOURSE_MARKERS['conclusion']):
            score += 1.0

        if any(m in full_text for m in ['i agree', 'i disagree', 'i support', 'i oppose']):
            score += 0.5

        return min(9.0, max(2.0, score))

    def _score_entity_cohesion(self, sentences: List[str]) -> float:
        """Score using Jaccard similarity between consecutive sentences."""
        overlaps = []

        for i in range(len(sentences) - 1):
            def get_content_words(s: str) -> set:
                words = re.findall(r'\b[a-z]{4,}\b', s.lower())
                stopwords = {'that', 'this', 'these', 'those', 'there', 'their', 'them', 'they',
                             'have', 'with', 'from', 'what', 'when', 'where', 'which', 'will'}
                return {w for w in words if w not in stopwords}

            set1 = get_content_words(sentences[i])
            set2 = get_content_words(sentences[i+1])

            if not set1 or not set2:
                overlaps.append(0.0)
                continue

            intersection = len(set1 & set2)
            union = len(set1 | set2)
            overlaps.append(intersection / union if union > 0 else 0.0)

        if not overlaps:
            return 5.0

        mean_jaccard = np.mean(overlaps)

        if self.JACCARD_GOOD_LOW <= mean_jaccard <= self.JACCARD_GOOD_HIGH:
            return 7.5
        elif self.JACCARD_OK_LOW <= mean_jaccard <= self.JACCARD_OK_HIGH:
            return 6.0
        elif mean_jaccard > self.JACCARD_GOOD_HIGH:
            return 5.0
        else:
            return 4.0

    def _score_discourse_markers(self, sentences: List[str]) -> float:
        """Score based on density of discourse markers."""
        full_text = ' '.join(sentences).lower()
        score = 5.0

        categories_used = set()
        for category, markers in self.DISCOURSE_MARKERS.items():
            if any(m in full_text for m in markers):
                categories_used.add(category)

        score += len(categories_used) * 0.8

        if len(sentences) >= 3:
            score += 0.5

        return min(9.0, max(2.0, score))

    def _score_semantic_flow(self, sentences: List[str]) -> float:
        """Score semantic coherence between consecutive sentences."""
        if not self._load_embedding_model() or self._embedding_model is None:
            return self._score_entity_cohesion(sentences)

        try:
            embeddings = self._embedding_model.encode(sentences, convert_to_numpy=True)
            similarities = []

            for i in range(len(embeddings) - 1):
                sim = float(
                    np.dot(embeddings[i], embeddings[i+1]) /
                    (np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[i+1]) + 1e-8)
                )
                similarities.append(sim)

            if not similarities:
                return 5.0

            mean_sim = np.mean(similarities)
            std_sim = np.std(similarities) if len(similarities) > 1 else 0.0

            if self.SEMANTIC_GOOD_LOW <= mean_sim <= self.SEMANTIC_GOOD_HIGH and std_sim < 0.25:
                return 8.0
            elif self.SEMANTIC_OK_LOW <= mean_sim <= self.SEMANTIC_OK_HIGH:
                return 6.5
            elif mean_sim > self.SEMANTIC_GOOD_HIGH:
                return 5.5
            else:
                return 4.5

        except Exception as e:
            logger.warning(f"Semantic scoring failed: {e}. Using fallback.")
            return self._score_entity_cohesion(sentences)

    def _score_topical_relevance(self, sentences: List[str], topic: str = None) -> float:
        """Score how well the response stays on topic."""
        if not topic or not topic.strip():
            return 7.0 # No topic -> neutral (unchanged)

        full_text = ' '.join(sentences)

        if self._load_embedding_model() and self._embedding_model is not None:
            try:
                text_embed = self._embedding_model.encode([full_text], convert_to_numpy=True)[0]
                topic_embed = self._embedding_model.encode([topic], convert_to_numpy=True)[0]

                sim = float(
                    np.dot(text_embed, topic_embed) /
                    (np.linalg.norm(text_embed) * np.linalg.norm(topic_embed) + 1e-8)
                )

                if sim >= 0.7:
                    return 9.0
                elif sim >= 0.5:
                    return 7.0 + (sim - 0.5) * 10
                elif sim >= 0.3:
                    return 5.0 + (sim - 0.3) * 10
                else:
                    return 2.0 + (sim / 0.3) * 3

            except Exception:
                pass

        topic_words = set(re.findall(r'\b[a-z]{3,}\b', topic.lower()))
        text_words = set(re.findall(r'\b[a-z]{3,}\b', full_text.lower()))

        if not topic_words:
            return 7.0

        overlap = len(topic_words & text_words) / len(topic_words)
        return 5.0 + overlap * 4.0

    def _compute_repetition_penalty(self, text: str) -> float:
        """Compute a penalty for excessive repetition."""
        words = [w.lower() for w in re.findall(r'\b[a-z]{4,}\b', text.lower())]

        if len(words) < 15:
            return 0.0

        word_counts = Counter(words)
        total = len(words)
        penalty = 0.0

        for word, count in word_counts.items():
            freq = count / total
            if freq > self.REPETITION_THRESHOLD:
                excess = freq - self.REPETITION_THRESHOLD
                penalty += excess * 0.5

        return min(self.MAX_REPETITION_PENALTY, penalty)

    def _generate_feedback(self, argument, entity, discourse, semantic, repetition_penalty, topical) -> List[str]:
        """Generate improvement tips based on weak spots."""
        feedback = []

        if argument < 6.0:
            feedback.append(" Structure your response: start with your opinion, give examples, then conclude.")
        if entity < 6.0:
            feedback.append(" Connect your sentences by repeating or paraphrasing your main topic words.")
        if discourse < 6.0:
            feedback.append(" Use discourse markers like 'firstly', 'however', and 'in conclusion' to guide the listener.")
        if semantic < 6.0:
            feedback.append(" Keep a smooth flow—avoid jumping between unrelated ideas.")
        if topical < 6.0:
            feedback.append(" Stay directly on the question topic—your response felt off-topic.")
        if repetition_penalty > 0.8:
            feedback.append(" You repeated the same words too often. Use synonyms (e.g., 'important' → 'crucial / vital').")

        if not feedback:
            feedback.append(" Excellent coherence! Your ideas flow logically and stay on topic.")

        return feedback


# ---------- FACTORY FUNCTION ----------
def create_coherence_analyzer(use_semantic: bool = True) -> CoherenceAnalyzer:
    """Factory function to create a CoherenceAnalyzer instance."""
    return CoherenceAnalyzer(use_semantic)