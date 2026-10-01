# modules/pte/utils/ai_generator.py
"""PTE 2026 AI Generator - Official PTE Academic 2026 Pattern (Pure AI, No Fallback)"""

import json
import re
import random
import os
import time
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

logger = logging.getLogger(__name__)

# FIX #1: Use existing singleton — NO new instance
try:
    from ai_engine import ai_engine # Already initialized in app.py
except ImportError:
    ai_engine = None
    logger.warning(" AI Engine not available.")

# Matplotlib for chart generation
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import Rectangle, FancyBboxPatch
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    logger.warning(" matplotlib not installed. Chart images disabled.")


class PTEAIGenerator:
    """Generate PTE test content using AI only - No fallback templates"""

    # ============ FIX #14: AI TIMEOUT ============
    AI_TIMEOUT_SECONDS = 30
    AI_RETRY_COUNT = 2

    TOPICS = [
        'artificial intelligence', 'machine learning', 'quantum computing', 'blockchain',
        'renewable energy', 'solar power', 'wind energy', 'electric vehicles',
        'space exploration', 'aerospace engineering', 'satellite technology',
        'internet of things', 'cybersecurity', 'digital transformation',
        'biotechnology', 'genetic engineering', 'nanotechnology',
        'climate change', 'global warming', 'biodiversity', 'deforestation',
        'ocean conservation', 'sustainable development', 'water scarcity',
        'air pollution', 'plastic waste', 'recycling', 'circular economy',
        'carbon emissions', 'green energy', 'eco-friendly solutions',
        'public health', 'mental health', 'nutrition', 'healthcare systems',
        'medical research', 'vaccination', 'telemedicine', 'pandemic response',
        'gene therapy', 'precision medicine', 'healthcare innovation',
        'aging population', 'health insurance', 'epidemiology',
        'online learning', 'educational reform', 'early childhood education',
        'critical thinking', 'emotional intelligence', 'cognitive development',
        'learning disabilities', 'educational psychology', 'career education',
        'distance learning', 'curriculum design', 'teacher training',
        'globalization', 'free trade', 'entrepreneurship', 'startup ecosystem',
        'economic development', 'inflation', 'employment trends', 'remuneration',
        'small business', 'international trade', 'digital economy',
        'sustainable business', 'corporate responsibility',
        'cultural diversity', 'gender equality', 'social media impact',
        'urbanization', 'housing crisis', 'migration patterns',
        'community development', 'social justice', 'demographic changes',
        'cultural heritage', 'language preservation', 'tourism impact',
        'scientific research', 'space science', 'marine biology',
        'astronomy', 'physics breakthroughs', 'chemistry innovation',
        'earth science', 'geology', 'meteorology', 'oceanography',
        'forensic science', 'archaeology', 'paleontology',
        'good governance', 'public policy', 'international relations',
        'environmental law', 'human rights', 'election systems',
        'diplomacy', 'peace studies', 'dispute resolution',
        'digital art', 'cultural influence', 'media evolution',
        'music technology', 'film industry', 'literary trends',
        'visual arts', 'performing arts', 'cultural festivals',
        'architecture design', 'fashion innovation'
    ]

    READING_TYPES = {
        'fill_blanks_rw': {'count': 5, 'weight': 35},
        'multiple_choice_multiple': {'count': 2, 'weight': 15},
        'reorder_paragraphs': {'count': 2, 'weight': 30},
        'multiple_choice_single': {'count': 3, 'weight': 20}
    }

    LISTENING_TYPES = {
        'summarize_spoken_text': {'count': lambda: random.choice([1, 2]), 'weight': 35},
        'multiple_choice_single': {'count': lambda: random.choice([1, 2]), 'weight': 10},
        'fill_blanks': {'count': lambda: random.choice([2, 3]), 'weight': 15},
        'highlight_correct_summary': {'count': lambda: random.choice([1, 2]), 'weight': 10},
        'select_missing_word': {'count': lambda: random.choice([1, 2]), 'weight': 10},
        'highlight_incorrect_words': {'count': lambda: random.choice([1, 2]), 'weight': 10},
        'write_from_dictation': {'count': lambda: random.choice([4, 5]), 'weight': 20}
    }

    # FIX #14: AI generation with timeout
    @classmethod
    def _ai_generate(cls, prompt: str, max_tokens: int = 600,
                     temperature: float = 0.7,
                     timeout: Optional[int] = None) -> Optional[str]:
        """AI generation with timeout — returns None if AI fails or times out."""
        if ai_engine is None:
            logger.warning(" AI engine not available")
            return None
        
        timeout = timeout or cls.AI_TIMEOUT_SECONDS
        
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    ai_engine.generate,
                    prompt,
                    max_tokens=max_tokens,
                    temperature=temperature
                )
                try:
                    result = future.result(timeout=timeout)
                    return result
                except FutureTimeoutError:
                    logger.warning(f" AI generation timed out after {timeout}s")
                    future.cancel()
                    return None
        except Exception as e:
            logger.warning(f" AI generation failed: {e}")
            return None

    @classmethod
    def get_random_topic(cls) -> str:
        return random.choice(cls.TOPICS)

    @classmethod
    def get_multiple_topics(cls, count: int) -> List[str]:
        if count <= len(cls.TOPICS):
            return random.sample(cls.TOPICS, count)
        return random.choices(cls.TOPICS, k=count)

    @classmethod
    def get_topic_categories(cls) -> Dict[str, List[str]]:
        categories = {}
        for topic in cls.TOPICS:
            if any(w in topic for w in ['technology', 'computer', 'data', 'digital']):
                categories.setdefault('Technology', []).append(topic)
            elif any(w in topic for w in ['climate', 'energy', 'environment', 'solar']):
                categories.setdefault('Environment', []).append(topic)
            elif any(w in topic for w in ['health', 'medical', 'mental', 'disease']):
                categories.setdefault('Health', []).append(topic)
            elif any(w in topic for w in ['education', 'learning', 'school', 'teaching']):
                categories.setdefault('Education', []).append(topic)
            elif any(w in topic for w in ['economic', 'business', 'trade', 'startup']):
                categories.setdefault('Business', []).append(topic)
            else:
                categories.setdefault('General', []).append(topic)
        return categories

    # ============ LISTENING MCQ ============
    @classmethod
    def generate_listening_mcq(cls, difficulty: str = 'medium', topic: Optional[str] = None) -> Optional[Dict]:
        if not topic:
            topic = cls.get_random_topic()
        
        prompt = f"""Generate a PTE Listening Multiple Choice question about "{topic}" at {difficulty} difficulty.

Return ONLY valid JSON. Do not include any extra text before or after.

Format:
{{
    "passage": "A 40-50 word passage about the topic",
    "question": "A clear question about the passage",
    "options": ["Meaningful option 1", "Meaningful option 2", "Meaningful option 3", "Meaningful option 4"],
    "correct": [0]
}}

IMPORTANT RULES:
1. The options must be REAL, meaningful options related to the passage.
2. Do NOT use "Option A", "Option B", "Option C", "Option D" - use actual content.
3. The passage should be 40-50 words.
4. The correct answer should be clearly supported by the passage.
5. The options should be distinct and not overlapping in meaning."""

        result = cls._ai_generate(prompt, max_tokens=500, temperature=0.7)
        if not result:
            return None

        try:
            start = result.find('{')
            end = result.rfind('}') + 1
            if start == -1 or end <= start:
                return None
            data = json.loads(result[start:end])

            # FIX #39: Stricter generic check
            generic_exact = {
                'option a', 'option b', 'option c', 'option d',
                'option 1', 'option 2', 'option 3', 'option 4',
                'a', 'b', 'c', 'd'
            }
            is_generic = all(
                str(opt).strip().lower() in generic_exact
                for opt in data.get('options', [])
            )
            if is_generic:
                logger.warning("Rejected generic options")
                return None

            # FIX #17: Validate passage length
            passage = data.get('passage', '')
            word_count = len(passage.split())
            if word_count < 30 or word_count > 70:
                logger.warning(f"Passage length out of range: {word_count} words")
                return None

            if not data.get('options') or len(data['options']) < 4:
                return None

            # FIX #13: Add topic to response
            data['topic'] = topic
            return data
        except Exception as e:
            logger.warning(f"MCQ parse failed: {e}")
            return None

    # ============ READING PASSAGE ============
    @classmethod
    def generate_reading_passage(cls, difficulty: str = 'medium',
                                  count: int = 1,
                                  topic: Optional[str] = None) -> Optional[List[Dict]]:
        if not topic:
            topics = cls.get_multiple_topics(count)
        else:
            topics = [topic] * count

        results = []
        for topic in topics:
            passage_len = {
                'easy': '150-180',
                'medium': '180-220',
                'hard': '220-280'
            }.get(difficulty, '180-220')

            prompt = f"""Write a passage for PTE Reading at {difficulty} level. ({passage_len} words)
Topic: {topic}
Requirements:
- Academic style with 4-5 paragraphs
- Include specific facts, statistics, or research findings
- Use academic vocabulary suitable for IELTS/PTE Band 6-9
- Include expert opinions or citations
- Return only the passage text with proper paragraph breaks
- Use clear topic sentences and logical flow
- Include at least one example or case study

The passage should be suitable for PTE Academic 2026 Reading test."""

            result = cls._ai_generate(prompt, max_tokens=700, temperature=0.7)
            if not result:
                return None

            results.append({
                "title": f"Understanding {topic.title()}",
                "text": result.strip(),
                "difficulty": difficulty,
                "topic": topic,
                "word_count": len(result.split()),
                "year": 2026
            })

        return results if count > 1 else results[0]

    # ============ LISTENING SCRIPT ============
    @classmethod
    def generate_listening_script(cls, question_type: str, difficulty: str = 'medium',
                                   count: int = 1) -> Optional[List[Dict]]:
        topics = cls.get_multiple_topics(count)
        results = []

        for topic in topics:
            prompt = cls._get_listening_prompt(question_type, difficulty, topic)
            result = cls._ai_generate(prompt, max_tokens=500, temperature=0.7)
            if not result:
                return None

            script = {
                'script': result.strip(),
                'topic': topic,
                'difficulty': difficulty,
                'year': 2026,
                'word_count': len(result.split())
            }
            results.append(script)

        return results if count > 1 else results[0]

    @classmethod
    def _get_listening_prompt(cls, question_type: str, difficulty: str, topic: str) -> str:
        prompts = {
            'summarize_spoken_text': f"""Generate a PTE Listening script for Summarize Spoken Text at {difficulty} difficulty.
Topic: {topic}
Requirements:
- Academic lecture style (60-120 seconds)
- Natural spoken language with clear main points
- Include introduction, body, and conclusion
- 4-5 key ideas that can be summarized
- Return the script text only""",
            'write_from_dictation': f"""Generate a PTE Listening Write from Dictation sentence at {difficulty} difficulty.
Topic: {topic}
Requirements:
- 10-15 word sentence
- Academic style with complex vocabulary
- Return the sentence only""",
            'multiple_choice_single': f"""Generate a PTE Listening Multiple Choice passage at {difficulty} difficulty.
Topic: {topic}
Requirements:
- 30-45 second passage
- Clear main idea for a single question
- Return the passage text only""",
            'fill_blanks': f"""Generate a PTE Listening Fill in the Blanks passage at {difficulty} difficulty.
Topic: {topic}
Requirements:
- 45-60 second passage
- 4-6 key words that could be missing
- Natural conversational style
- Return the passage text only""",
            'highlight_correct_summary': f"""Generate a PTE Listening Highlight Correct Summary passage at {difficulty} difficulty.
Topic: {topic}
Requirements:
- 45-60 second passage
- Clear main points and supporting details
- Academic or lecture style
- Return the passage text only""",
            'select_missing_word': f"""Generate a PTE Listening Select Missing Word passage at {difficulty} difficulty.
Topic: {topic}
Requirements:
- 30-45 second passage
- The last word should be missing
- Natural conversational style
- Return the passage text only""",
            'highlight_incorrect_words': f"""Generate a PTE Listening Highlight Incorrect Words passage at {difficulty} difficulty.
Topic: {topic}
Requirements:
- 45-60 second passage
- Include 3-5 words that are incorrect
- Academic or news style
- Return the passage text only"""
        }
        return prompts.get(question_type, f"Generate a PTE Listening script about {topic}.")

    # ============ COMPLETE READING TEST ============
    @classmethod
    def generate_complete_reading_test(cls, difficulty: str = 'medium') -> Optional[Dict]:
        passage_data = cls.generate_reading_passage(difficulty, 1)
        if not passage_data:
            return None

        if isinstance(passage_data, list):
            passage_data = passage_data[0]
        passage_text = passage_data['text']
        passage_title = passage_data['title']

        questions = []

        fill_blanks = cls._generate_fill_blanks_rw(difficulty, 5, passage_text)
        for i, fb in enumerate(fill_blanks):
            questions.append({
                'id': f"fill_blanks_rw_{i+1}",
                'type': 'fill_blanks_rw',
                'type_name': 'Reading & Writing: Fill in the Blanks',
                'text': fb.get('passage', ''),
                'blanks': fb.get('blanks', []),
                'options': fb.get('options', []),
                'instruction': 'Select the correct word for each blank from the dropdown menu.',
                'time_allowed': 120,
                'difficulty': difficulty,
                'year': 2026
            })

        reorder = cls._generate_reorder_paragraphs(difficulty, 2, passage_text)
        for i, ro in enumerate(reorder):
            questions.append({
                'id': f"reorder_paragraphs_{i+1}",
                'type': 'reorder_paragraphs',
                'type_name': 'Re-order Paragraphs',
                'items': ro.get('sentences', []),
                'correct_order': ro.get('correct_order', []),
                'instruction': 'Arrange the paragraphs in the correct logical order.',
                'time_allowed': 120,
                'difficulty': difficulty,
                'year': 2026
            })

        mc_multiple = cls._generate_multiple_choice_multiple(difficulty, 2, passage_text)
        for i, mc in enumerate(mc_multiple):
            questions.append({
                'id': f"multiple_choice_multiple_{i+1}",
                'type': 'multiple_choice_multiple',
                'type_name': 'Multiple Choice, Multiple Answers',
                'text': mc.get('question', ''),
                'options': mc.get('options', []),
                'correct_indices': mc.get('answer_indices', []),
                'instruction': 'Select all correct answers. There may be 2-3 correct options.',
                'time_allowed': 90,
                'difficulty': difficulty,
                'year': 2026
            })

        mc_single = cls._generate_multiple_choice_single(difficulty, 3, passage_text)
        for i, mc in enumerate(mc_single):
            questions.append({
                'id': f"multiple_choice_single_{i+1}",
                'type': 'multiple_choice_single',
                'type_name': 'Multiple Choice, Single Answer',
                'text': mc.get('question', ''),
                'options': mc.get('options', []),
                'correct_index': mc.get('answer_index', 0),
                'instruction': 'Select the one correct answer.',
                'time_allowed': 60,
                'difficulty': difficulty,
                'year': 2026
            })

        random.shuffle(questions)

        for idx, q in enumerate(questions):
            q['question_number'] = idx + 1

        return {
            'id': f"pte_reading_{int(time.time())}",
            'title': 'PTE Reading Practice Test',
            'year': 2026,
            'difficulty': difficulty,
            'duration': '29-35 minutes',
            'passage': passage_text,
            'passage_title': passage_title,
            'questions': questions,
            'total_questions': len(questions),
            'official_pattern': True,
            'topic': passage_data.get('topic', 'general'),
            'generated_at': datetime.now().isoformat()
        }

    @classmethod
    def generate_complete_listening_test(cls, difficulty: str = 'medium') -> Optional[Dict]:
        questions = []

        question_types = [
            ('summarize_spoken_text', 2),
            ('multiple_choice_single', 2),
            ('fill_blanks', 2),
            ('highlight_correct_summary', 2),
            ('select_missing_word', 1),
            ('write_from_dictation', 4)
        ]

        for q_type, count in question_types:
            for i in range(count):
                if q_type == 'multiple_choice_single':
                    mcq_data = cls.generate_listening_mcq(difficulty)
                    if mcq_data:
                        questions.append({
                            'id': f"{q_type}_{i+1}",
                            'type': q_type,
                            'type_name': 'Multiple Choice (Single)',
                            'prompt': {
                                'instruction': 'Listen to the passage and choose the correct answer.',
                                'passage': mcq_data.get('passage', ''),
                                'question': mcq_data.get('question', 'What is the main idea?'),
                                'options': mcq_data.get('options', ['Option 1', 'Option 2', 'Option 3', 'Option 4']),
                                'correct': mcq_data.get('correct', [0])
                            },
                            'time_allowed': 60,
                            'difficulty': difficulty,
                            'year': 2026,
                            'topic': mcq_data.get('topic', 'general')
                        })
                        continue

                script_data = cls.generate_listening_script(q_type, difficulty, 1)
                if not script_data:
                    return None
                if isinstance(script_data, list):
                    script_data = script_data[0]

                script = script_data.get('script', '')
                if not script:
                    return None

                question = {
                    'id': f"{q_type}_{i+1}",
                    'type': q_type,
                    'type_name': cls._get_question_type_name(q_type),
                    'prompt': {
                        'instruction': cls._get_instruction_for_type(q_type),
                        'passage': script,
                        'script': script
                    },
                    'time_allowed': cls._get_time_for_type(q_type),
                    'difficulty': difficulty,
                    'year': 2026,
                    'topic': script_data.get('topic', 'general')
                }

                questions.append(question)

        random.shuffle(questions)

        for idx, q in enumerate(questions):
            q['question_number'] = idx + 1

        return {
            'id': f"pte_listening_{int(time.time())}",
            'title': 'PTE Listening Practice Test',
            'year': 2026,
            'difficulty': difficulty,
            'duration': '30-35 minutes',
            'questions': questions,
            'total_questions': len(questions),
            'official_pattern': True,
            'generated_at': datetime.now().isoformat()
        }

    # ============ SPEAKING & WRITING ============
    @classmethod
    def generate_complete_speaking_writing_test(cls, difficulty: str = 'medium',
                                                 topic: Optional[str] = None) -> Optional[Dict]:
        if topic is None:
            topic = cls.get_random_topic()

        logger.info(f"Generating S&W test — Topic: {topic}, Difficulty: {difficulty}")

        questions = []

        # 1. Read Aloud
        for i in range(2):
            prompt = f"Write a short academic paragraph (30-40 words) about '{topic}' for PTE Read Aloud. Return only the text."
            text = cls._ai_generate(prompt, max_tokens=200, temperature=0.7)
            if text:
                questions.append({
                    "id": f"read_aloud_{i+1}",
                    "type": "read_aloud",
                    "type_name": "Read Aloud",
                    "prompt": {"instruction": "Read the text aloud as naturally as possible.", "text": text.strip()},
                    "difficulty": difficulty,
                    "year": 2026
                })

        # 2. Repeat Sentence
        for i in range(2):
            prompt = f"Generate a 10-15 word academic sentence about '{topic}' for PTE Repeat Sentence. Return only the sentence."
            sentence = cls._ai_generate(prompt, max_tokens=200, temperature=0.7)
            if sentence:
                questions.append({
                    "id": f"repeat_sentence_{i+1}",
                    "type": "repeat_sentence",
                    "type_name": "Repeat Sentence",
                    "prompt": {"instruction": "Listen to the sentence and repeat it exactly.", "sentence": sentence.strip()},
                    "correct_answer": sentence.strip(),
                    "difficulty": difficulty,
                    "year": 2026
                })

        # 3. Describe Image
        prompt = f"""Describe an image related to '{topic}' for PTE Describe Image.
        Provide a vivid description (3-4 sentences) of an imaginary image.
        Return ONLY valid JSON: {{"image_description": "...", "key_points": ["point1","point2","point3"]}}"""
        result = cls._ai_generate(prompt, max_tokens=300, temperature=0.7)
        if result:
            try:
                start = result.find('{')
                end = result.rfind('}') + 1
                if start != -1 and end > start:
                    data = json.loads(result[start:end])
                    questions.append({
                        "id": "describe_image_1",
                        "type": "describe_image",
                        "type_name": "Describe Image",
                        "prompt": {
                            "instruction": "Describe the image in as much detail as possible.",
                            "image_description": data.get("image_description", ""),
                            "key_points": data.get("key_points", [])
                        },
                        "difficulty": difficulty,
                        "year": 2026
                    })
            except Exception as e:
                logger.warning(f"Describe Image parse failed: {e}")

        # 4. Re-tell Lecture
        prompt = f"Write a short lecture script (60-80 words) about '{topic}' for PTE Re-tell Lecture. Return only the lecture text."
        lecture = cls._ai_generate(prompt, max_tokens=300, temperature=0.7)
        if lecture:
            questions.append({
                "id": "re_tell_lecture_1",
                "type": "re_tell_lecture",
                "type_name": "Re-tell Lecture",
                "prompt": {"instruction": "Listen to the lecture and retell it in your own words.", "lecture": lecture.strip()},
                "difficulty": difficulty,
                "year": 2026
            })

        # 5. Answer Short Question
        for i in range(2):
            prompt = f"""Create a short question and its one-word/short answer about '{topic}' for PTE Answer Short Question.
            Return ONLY valid JSON: {{"question": "...", "answer": "..."}}"""
            result = cls._ai_generate(prompt, max_tokens=200, temperature=0.7)
            if result:
                try:
                    start = result.find('{')
                    end = result.rfind('}') + 1
                    if start != -1 and end > start:
                        data = json.loads(result[start:end])
                        questions.append({
                            "id": f"answer_short_question_{i+1}",
                            "type": "answer_short_question",
                            "type_name": "Answer Short Question",
                            "prompt": {
                                "instruction": "Answer the question with one word or a short phrase.",
                                "question": data.get("question", ""),
                                "correct_answer": data.get("answer", "")
                            },
                            "correct_answer": data.get("answer", ""),
                            "difficulty": difficulty,
                            "year": 2026
                        })
                except Exception as e:
                    logger.warning(f"ASQ parse failed: {e}")

        # 6. Summarize Written Text
        prompt = f"Write a short passage (80-100 words) about '{topic}' for PTE Summarize Written Text. Return only the passage."
        passage = cls._ai_generate(prompt, max_tokens=400, temperature=0.7)
        if passage:
            questions.append({
                "id": "summarize_written_text_1",
                "type": "summarize_written_text",
                "type_name": "Summarize Written Text",
                "prompt": {"instruction": "Write a one-sentence summary of the passage (5-75 words).", "passage": passage.strip()},
                "difficulty": difficulty,
                "year": 2026
            })

        # 7. Write Essay
        prompt = f"Generate an IELTS/PTE style essay prompt about '{topic}' for PTE Write Essay. Return only the essay prompt."
        essay_prompt = cls._ai_generate(prompt, max_tokens=200, temperature=0.7)
        if essay_prompt:
            questions.append({
                "id": "write_essay_1",
                "type": "write_essay",
                "type_name": "Write Essay",
                "prompt": {"instruction": "Write a 200-300 word essay on the given topic.", "prompt": essay_prompt.strip()},
                "difficulty": difficulty,
                "year": 2026
            })

        if not questions:
            return None

        for idx, q in enumerate(questions):
            q['question_number'] = idx + 1

        return {
            "questions": questions,
            "total_questions": len(questions),
            "duration": "30-40 minutes",
            "difficulty": difficulty,
            "year": 2026,
            "official_pattern": True,
            "official_version": "PTE Academic 2026",
            "test_type": "pte_speaking_writing",
            "topic": topic,
            "generated_at": datetime.now().isoformat()
        }

    # ============ HELPER METHODS ============
    @classmethod
    def _get_question_type_name(cls, q_type: str) -> str:
        names = {
            'summarize_spoken_text': 'Summarize Spoken Text',
            'multiple_choice_single': 'Multiple Choice (Single)',
            'fill_blanks': 'Fill in the Blanks',
            'highlight_correct_summary': 'Highlight Correct Summary',
            'select_missing_word': 'Select Missing Word',
            'write_from_dictation': 'Write from Dictation'
        }
        return names.get(q_type, q_type.replace('_', ' ').title())

    @classmethod
    def _get_instruction_for_type(cls, q_type: str) -> str:
        instructions = {
            'summarize_spoken_text': 'Listen to the lecture and summarize the main points in 50-70 words.',
            'multiple_choice_single': 'Listen to the passage and choose the correct answer.',
            'fill_blanks': 'Listen to the passage and fill in the missing words.',
            'highlight_correct_summary': 'Listen to the passage and select the correct summary.',
            'select_missing_word': 'Listen to the passage and select the missing word.',
            'write_from_dictation': 'Type the sentence exactly as you hear it.'
        }
        return instructions.get(q_type, 'Listen carefully and answer the question.')

    @classmethod
    def _get_time_for_type(cls, q_type: str) -> int:
        times = {
            'summarize_spoken_text': 120,
            'multiple_choice_single': 60,
            'fill_blanks': 90,
            'highlight_correct_summary': 90,
            'select_missing_word': 60,
            'write_from_dictation': 45
        }
        return times.get(q_type, 60)

    # ============ READING QUESTION GENERATORS ============
    @classmethod
    def _generate_fill_blanks_rw(cls, difficulty: str, count: int, passage_text: str) -> List[Dict]:
        results = []
        words = passage_text.split()
        if len(words) < 20:
            logger.warning(f"Passage too short for fill-blanks: {len(words)} words")
            return []

        for _ in range(count):
            max_blanks = min(6, len(words) - 6)
            if max_blanks < 3:
                continue
            num_blanks = random.randint(3, max_blanks)
            blank_positions = random.sample(range(3, len(words) - 3), num_blanks)
            blank_positions.sort()

            blanks = []
            blanked_text = words.copy()
            for pos in blank_positions:
                if pos < len(words):
                    blanks.append(words[pos])
                    blanked_text[pos] = '[BLANK]'

            options = []
            for blank in blanks:
                opts = [blank]
                possible_words = [w for w in words if w.lower() != blank.lower() and len(w) > 2]
                if len(possible_words) >= 3:
                    opts.extend(random.sample(possible_words, 3))
                else:
                    opts.extend(['significant', 'important', 'essential'])
                random.shuffle(opts)
                options.append(opts)

            results.append({
                'passage': ' '.join(blanked_text),
                'blanks': blanks,
                'options': options
            })

        return results

    @classmethod
    def _generate_reorder_paragraphs(cls, difficulty: str, count: int, passage_text: str) -> List[Dict]:
        sentences = re.split(r'(?<=[.!?])\s+', passage_text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        if len(sentences) < 4:
            return []

        results = []
        for _ in range(count):
            num_para = random.choice([3, 4])
            selected = sentences[:num_para] if len(sentences) >= num_para else sentences

            # Server sends ORIGINAL order + correct_order
            results.append({
                'sentences': selected, # Original
                'correct_order': list(range(len(selected))) # [0, 1, 2, 3]
            })

        return results

    @classmethod
    def _generate_multiple_choice_single(cls, difficulty: str, count: int, passage_text: str) -> List[Dict]:
        results = []
        words = passage_text.split()
        topic = words[0] if words else 'the topic'

        for _ in range(count):
            question = f"What is the main argument presented in the passage about {topic}?"
            correct = f"The passage explains that {topic} plays a crucial role in shaping outcomes."

            distractors = [
                f"The author dismisses {topic} as a secondary consideration.",
                f"The passage primarily criticizes current approaches to {topic}.",
                f"The author recommends immediate action without proper evaluation."
            ]

            options = [correct] + random.sample(distractors, 3)
            random.shuffle(options)

            results.append({
                'question': question,
                'options': options,
                'answer_index': options.index(correct)
            })

        return results

    @classmethod
    def _generate_multiple_choice_multiple(cls, difficulty: str, count: int, passage_text: str) -> List[Dict]:
        results = []
        words = passage_text.split()
        topic = words[0] if words else 'the topic'

        for _ in range(count):
            question = f"Which statements about {topic} are supported by the passage?"

            corrects = [
                f"It has significant economic implications.",
                f"It requires interdisciplinary approaches."
            ]

            distractors = [
                f"It only affects developed nations.",
                f"It has no practical applications."
            ]

            options = corrects + random.sample(distractors, 2)
            random.shuffle(options)

            results.append({
                'question': question,
                'options': options,
                'answer_indices': [options.index(c) for c in corrects]
            })

        return results


# ============ SINGLETON ============
pte_ai_generator = PTEAIGenerator()