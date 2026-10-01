"""Data classes for IELTS Reading test - pure containers, no generation logic."""

import uuid
from typing import Dict, List, Optional, Any

class Question:
    def __init__(self, id=None, question_type="multiple_choice",
                 question_text="", options=None, correct_answer="",
                 explanation="", paragraph_reference="", question_number=0):
        self.id = id or f"q_{uuid.uuid4().hex[:8]}"
        self.question_type = question_type
        self.question_text = question_text
        self.options = options or []
        self.correct_answer = correct_answer
        self.explanation = explanation
        self.paragraph_reference = paragraph_reference
        self.question_number = question_number

    def to_dict(self):
        return {
            'id': self.id,
            'type': self.question_type,
            'text': self.question_text,
            'options': self.options,
            'correct_answer': self.correct_answer,
            'explanation': self.explanation,
            'paragraph_reference': self.paragraph_reference,
            'number': self.question_number
        }


class Passage:
    def __init__(self, id=None, title="", content="", difficulty="medium",
                 topic="general", word_count=0, questions=None,
                 passage_number=1, estimated_time=20):
        self.id = id or f"passage_{uuid.uuid4().hex[:8]}"
        self.title = title
        self.content = content
        self.difficulty = difficulty
        self.topic = topic
        self.word_count = word_count or len(content.split())
        self.questions = questions or []
        self.passage_number = passage_number
        self.estimated_time = estimated_time

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'content': self.content,
            'difficulty': self.difficulty,
            'topic': self.topic,
            'word_count': self.word_count,
            'questions': [q.to_dict() for q in self.questions],
            'passage_number': self.passage_number,
            'estimated_time': self.estimated_time
        }


class ReadingTest:
    def __init__(self, test_id=None, title="IELTS Reading Practice Test"):
        self.id = test_id or str(uuid.uuid4())
        self.title = title
        self.passages: List[Passage] = []
        self.total_questions = 0
        self.time_limit = 60

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'passages': [p.to_dict() for p in self.passages],
            'total_questions': self.total_questions,
            'time_limit': self.time_limit
        }

    def to_json(self, indent=2) -> str:
        import json
        return json.dumps(self.to_dict(), indent=indent)