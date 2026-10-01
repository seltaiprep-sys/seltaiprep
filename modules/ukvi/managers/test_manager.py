# modules/ukvi/managers/test_manager.py
"""
UKVI Test session management – real UKVI style with adaptive follow-up.

v2.0 — REAL UKVI REALISM:
  • Profile validation before interview start
  • Better error handling (graceful failures)
  • Combined evaluation + follow-up (single AI call)
  • Conversation history (last 5 turns)
  • Safe JSON parsing
"""

import json
import os
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..models import UKVITestSession, UKVITestResult

logger = logging.getLogger(__name__)


class UKVITestManager:
    def __init__(self, db, service):
        self.db = db
        self.service = service

    # ═══════════════════════════════════════════════════════════════
    # COMBINED EVALUATE + FOLLOW-UP (single AI call)
    # ═══════════════════════════════════════════════════════════════
    def _combined_evaluate_and_followup(
        self,
        question: str,
        answer: str,
        category: str,
        difficulty: str,
        profile: Optional[Dict] = None,
        conversation_history: Optional[List[Dict]] = None,
    ) -> tuple:
        """
        One AI call: returns (evaluation_dict, next_question_or_None)
        Uses full conversation history for context.
        """
        # Build conversation history text (last 5 turns)
        history_text = ""
        if conversation_history:
            for i, turn in enumerate(conversation_history[-5:], 1):
                q = turn.get('question', '')
                a = turn.get('answer', '')
                history_text += f"Previous Q{i}: {q}\nPrevious A{i}: {a}\n"

        profile_text = ""
        if profile:
            name = profile.get('full_name', 'the student')
            uni = profile.get('university', '')
            course = profile.get('course', '')
            profile_text = f"The student is {name}"
            if uni:
                profile_text += f", applying to {uni}"
            if course:
                profile_text += f" for {course}"
            profile_text += "."

        prompt = f"""You are a UKVI examiner for a student visa credibility interview.
Question category: {category}
Difficulty: {difficulty}
{profile_text}

Conversation so far:
{history_text}

Current question: "{question}"
Student's answer: "{answer}"

Based on the entire conversation, provide:
1. An IELTS-style band score (0-9) for overall performance.
2. A short, constructive feedback message (2-3 sentences) that references the conversation.
3. The next question to ask — IF the answer was vague or incomplete, probe deeper.
   If the answer was complete, output "None".
   The next question should sound like a real UKVI officer — firm but fair.

Return ONLY valid JSON with keys: "score", "feedback", "next_question".
Do not include any other text.
"""
        try:
            raw = self.service.ai_engine.generate(
                prompt, max_tokens=300, temperature=0.3
            )
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                data = {
                    "score": 6.0,
                    "feedback": "Good attempt.",
                    "next_question": None,
                }
        except Exception as e:
            logger.warning(f"Combined AI call failed: {e}, using fallback")
            data = {
                "score": 6.0,
                "feedback": "Good attempt.",
                "next_question": None,
            }

        # Build evaluation dict
        score = float(data.get('score', 6.0))
        evaluation = {
            'overall_score': score,
            'feedback': data.get('feedback', ''),
            'fluency': score,
            'grammar': score,
            'vocabulary': score,
            'coherence': score,
        }
        next_question = data.get('next_question')
        if next_question in ("None", "none", "", None):
            next_question = None

        return evaluation, next_question

    # ═══════════════════════════════════════════════════════════════
    # START INTERVIEW
    # ═══════════════════════════════════════════════════════════════
    def start_interview(
        self,
        user_id: int,
        difficulty: str = "medium",
        personalized: bool = True,
    ) -> Dict:
        """Start a new UKVI interview session."""
        # ─── Validate profile first ──────────────────────────────
        profile = self.service.get_profile(user_id)
        if not profile:
            return {
                'error': 'Profile required. Please fill in your details first.',
                'needs_profile': True,
            }

        missing = [
            k for k in ['university', 'course'] if not profile.get(k)
        ]
        if missing:
            return {
                'error': f'Profile incomplete. Missing: {", ".join(missing)}.',
                'needs_profile': True,
                'missing_fields': missing,
            }

        # ─── Generate questions ──────────────────────────────────
        interview = self.service.generate_interview(
            user_id, difficulty, personalized
        )
        if interview.get('error'):
            return interview

        questions = interview.get('questions', [])
        if not questions:
            return {'error': 'No questions generated'}

        # ─── Create session ──────────────────────────────────────
        session = UKVITestSession(
            user_id=user_id,
            test_type='interview',
            difficulty=difficulty,
            status='in_progress',
            start_time=datetime.now(timezone.utc),
            test_data=json.dumps({
                'questions': questions,
                'personalized': personalized,
                'difficulty': difficulty,
                'visa_type': 'student',
                'ai_generated': interview.get('ai_generated', False),
                'fallback_used': interview.get('fallback_used', False),
                'cached': interview.get('cached', False),
            }),
            current_question_index=0,
            answers_so_far='{}',
        )
        self.db.session.add(session)
        self.db.session.commit()

        logger.info(
            f"UKVI session {session.id} started for user {user_id} "
            f"with {len(questions)} questions"
        )

        return {
            'session_id': session.id,
            'questions': questions,
            'total_questions': len(questions),
            'difficulty': difficulty,
            'personalized': personalized,
            'ai_generated': interview.get('ai_generated', False),
            'cached': interview.get('cached', False),
        }

    # ═══════════════════════════════════════════════════════════════
    # SUBMIT ANSWER (adaptive)
    # ═══════════════════════════════════════════════════════════════
    def submit_answer_adaptive(
        self,
        user_id: int,
        session_id: int,
        question_index: int,
        question_text: str,
        answer: str,
    ) -> Dict:
        """Submit an answer – uses a single AI call with conversation history."""
        session = self._get_session(user_id, session_id)
        if session.get('error'):
            return session

        session_obj = session['session']
        test_data = session_obj.get_test_data()
        difficulty = test_data.get('difficulty', 'medium')

        # Find the category for this question
        category = 'general'
        for q in test_data.get('questions', []):
            if q.get('question') == question_text:
                category = q.get('category', 'general')
                break

        # Get existing answers to build conversation history
        answers = session_obj.get_answers()
        conversation_history = []
        for idx, ans in answers.items():
            if int(idx) != question_index:
                conversation_history.append({
                    'question': ans.get('question', ''),
                    'answer': ans.get('answer', ''),
                })

        # Get profile for personalisation
        profile = self.service.get_profile(user_id)

        # ─── Combined call with history ──────────────────────────
        evaluation, next_question = self._combined_evaluate_and_followup(
            question_text, answer, category, difficulty, profile,
            conversation_history,
        )
        evaluation['category'] = category

        # Store the answer
        answers[str(question_index)] = {
            'question': question_text,
            'category': category,
            'answer': answer,
            'evaluation': evaluation,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        session_obj.set_answers(answers)
        session_obj.last_updated = datetime.now(timezone.utc)
        self.db.session.commit()

        follow_up_added = bool(next_question)
        if follow_up_added:
            logger.info(f"Generated context-aware follow-up: {next_question}")

        return {
            'question_index': question_index,
            'evaluation': evaluation,
            'follow_up_added': follow_up_added,
            'next_question': next_question,
            'remaining': 0,
            'total_answered': len(answers),
        }

    # ═══════════════════════════════════════════════════════════════
    # AUDIO SUBMISSION
    # ═══════════════════════════════════════════════════════════════
    def submit_audio_answer(
        self,
        user_id: int,
        session_id: int,
        question_index: int,
        question_text: str,
        audio_data: bytes,
        filename: str = None,
    ) -> Dict:
        # Save audio file
        audio_dir = os.path.join('data', 'ukvi', 'audio_answers')
        os.makedirs(audio_dir, exist_ok=True)
        if not filename:
            filename = f"{session_id}_q{question_index}_answer.wav"
        audio_path = os.path.join(audio_dir, filename)
        with open(audio_path, 'wb') as f:
            f.write(audio_data)

        # Transcribe
        transcript = self.service.transcribe_audio(audio_path)

        # Submit the transcribed answer
        result = self.submit_answer_adaptive(
            user_id, session_id, question_index, question_text, transcript
        )
        if result.get('error'):
            return result

        # Add audio path to stored answer
        session_obj = self._get_session(user_id, session_id)['session']
        answers = session_obj.get_answers()
        if str(question_index) in answers:
            answers[str(question_index)]['audio_path'] = audio_path
            session_obj.set_answers(answers)
            self.db.session.commit()

        result['transcript'] = transcript
        result['audio_path'] = audio_path
        return result

    # ═══════════════════════════════════════════════════════════════
    # COMPLETE INTERVIEW
    # ═══════════════════════════════════════════════════════════════
    def complete_interview(self, user_id: int, session_id: int) -> Dict:
        session = self._get_session(user_id, session_id)
        if session.get('error'):
            return session

        session_obj = session['session']
        answers = session_obj.get_answers()

        if not answers:
            return {'error': 'No answers found to evaluate'}

        # Build list of evaluations
        evaluations = []
        for idx, ans in answers.items():
            ev = ans.get('evaluation', {})
            if ev:
                evaluations.append(ev)

        if not evaluations:
            return {'error': 'No valid evaluations found'}

        # Final evaluation
        profile = self.service.get_profile(user_id)
        final = self.service.evaluate_interview(evaluations, profile)

        # Store result
        result = UKVITestResult(
            user_id=user_id,
            test_type='interview',
            score=final.get('overall_score', 0),
            band_score=final.get('overall_score', 0),
            answers=json.dumps(answers),
            feedback=json.dumps(final.get('recommendations', [])),
            detailed_evaluation=json.dumps(final),
        )
        self.db.session.add(result)

        session_obj.status = 'completed'
        session_obj.completed_at = datetime.now(timezone.utc)
        session_obj.last_updated = datetime.now(timezone.utc)
        self.db.session.commit()

        return {
            'session_id': session_id,
            'total_questions': len(
                session_obj.get_test_data().get('questions', [])
            ),
            'answered': len(answers),
            'evaluation': final,
            'result_id': result.id,
        }

    # ═══════════════════════════════════════════════════════════════
    # HISTORY / PROGRESS
    # ═══════════════════════════════════════════════════════════════
    def get_history(self, user_id: int) -> Dict:
        """List all interview sessions for a user."""
        try:
            sessions = (
                UKVITestSession.query
                .filter_by(user_id=user_id)
                .order_by(UKVITestSession.start_time.desc())
                .limit(20)
                .all()
            )
            results = (
                UKVITestResult.query
                .filter_by(user_id=user_id)
                .order_by(UKVITestResult.created_at.desc())
                .limit(20)
                .all()
            )
            return {
                'success': True,
                'sessions': [
                    {
                        'id': s.id,
                        'status': s.status,
                        'difficulty': s.difficulty,
                        'start_time': s.start_time.isoformat() if s.start_time else None,
                        'completed_at': s.completed_at.isoformat() if s.completed_at else None,
                    }
                    for s in sessions
                ],
                'results': [
                    {
                        'id': r.id,
                        'score': r.score,
                        'band_score': r.band_score,
                        'created_at': r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in results
                ],
            }
        except Exception as e:
            logger.exception(f"get_history failed: {e}")
            return {'success': False, 'error': str(e)}

    def get_progress(self, user_id: int) -> Dict:
        """Summary progress for a user."""
        try:
            total_sessions = (
                UKVITestSession.query.filter_by(user_id=user_id).count()
            )
            completed = (
                UKVITestSession.query
                .filter_by(user_id=user_id, status='completed')
                .count()
            )
            results = (
                UKVITestResult.query.filter_by(user_id=user_id).all()
            )
            scores = [r.band_score for r in results if r.band_score]
            avg = round(sum(scores) / len(scores), 1) if scores else 0.0
            best = round(max(scores), 1) if scores else 0.0
            return {
                'success': True,
                'total_sessions': total_sessions,
                'completed_sessions': completed,
                'average_score': avg,
                'best_score': best,
                'total_interviews': len(results),
            }
        except Exception as e:
            logger.exception(f"get_progress failed: {e}")
            return {'success': False, 'error': str(e)}

    # ═══════════════════════════════════════════════════════════════
    # HELPER
    # ═══════════════════════════════════════════════════════════════
    def _get_session(
        self,
        user_id: int,
        session_id: int,
        check_status: bool = True,
    ) -> Dict:
        query = UKVITestSession.query.filter_by(
            id=session_id, user_id=user_id
        )
        if check_status:
            query = query.filter_by(status='in_progress')
        session = query.first()
        if not session:
            return {'error': 'Session not found or already completed'}
        return {'session': session}

    def get_question_audio(
        self,
        user_id: int,
        session_id: int,
        question_index: int,
    ) -> Dict:
        """Generate TTS audio for a specific question."""
        session = self._get_session(user_id, session_id)
        if session.get('error'):
            return session
        session_obj = session['session']
        test_data = session_obj.get_test_data()
        questions = test_data.get('questions', [])
        if question_index < 0 or question_index >= len(questions):
            return {'error': 'Invalid question index'}
        question_text = questions[question_index].get('question', '')
        audio_url = self.service.interview_gen.speak_question(question_text)
        if audio_url:
            return {'success': True, 'audio_url': audio_url}
        return {'error': 'TTS generation failed'}