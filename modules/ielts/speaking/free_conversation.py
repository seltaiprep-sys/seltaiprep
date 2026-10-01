"""Free Conversation Engine — Real IELTS Speaking simulation.

This module implements a dynamic, natural, two-way IELTS Speaking examiner
that adapts its questions based on the candidate's responses.

Key features:
- In-memory session management (no DB writes per turn)
- Random examiner persona per session
- Dynamic greeting generation
- Response analysis with warnings (too short, off-topic, hesitation, etc.)
- Phase transitions (Part 1 → Part 2 → Part 3)
- Context memory across the whole test
- Dynamic follow-up question generation
- Part 2 cue card generation from recent context
- Time-based automatic phase advancement
- Natural farewell generation

Public API:
    engine = FreeConversationEngine(ai_engine)
    result = engine.start_session(user_id, difficulty='medium')
    result = engine.process_response(session_id, transcript)
    result = engine.get_session_state(session_id)
    result = engine.end_session(session_id)
"""

import json
import logging
import random
import re
import time
import uuid
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# EXAMINER PERSONAS
# ═══════════════════════════════════════════════════════════════
EXAMINER_PERSONAS = [
    {"name": "Emma Wilson", "voice": "female", "style": "warm but professional"},
    {"name": "James Chen", "voice": "male", "style": "calm and encouraging"},
    {"name": "Sarah Mitchell", "voice": "female", "style": "friendly and attentive"},
    {"name": "David Thompson", "voice": "male", "style": "formal and precise"},
]


# ═══════════════════════════════════════════════════════════════
# PHASE CONFIGURATION
# ═══════════════════════════════════════════════════════════════
PHASE_CONFIG = {
    1: {
        'name': 'Introduction & Interview',
        'duration_sec': 270, # 4.5 minutes
    },
    2: {
        'name': 'Long Turn',
        'prep_sec': 60,
        'speak_sec': 120,
        'followup_sec': 60,
    },
    3: {
        'name': 'Discussion',
        'duration_sec': 270,
    },
}


# ═══════════════════════════════════════════════════════════════
# WARNING MESSAGES
# ═══════════════════════════════════════════════════════════════
WARNINGS = {
    'no_response': ' No speech detected. Please respond.',
    'too_short': ' Too short. Try 2-3 sentences with reasons.',
    'off_topic': ' Stay focused on the question.',
    'no_detail': ' Add a specific example or detail.',
    'repetition': ' Try to vary your vocabulary.',
    'hesitation': ' Try to speak more fluently.',
}


class FreeConversationEngine:
    """In-memory free conversation session manager.

    Not thread-safe across sessions, but each session is independent.
    Sessions expire after 1 hour of inactivity.
    """

    def __init__(self, ai_engine):
        """
        Args:
            ai_engine: An object with a .generate(prompt, ...) method
                       (your AIEngine instance from ai_engine.py).
        """
        self.ai = ai_engine
        self.sessions: Dict[str, Dict] = {}
        self._session_ttl = 3600 # 1 hour

    # ═══════════════════════════════════════════════════════════
    # SESSION LIFECYCLE
    # ═══════════════════════════════════════════════════════════
    def _cleanup_old_sessions(self):
        """Remove sessions older than _session_ttl seconds."""
        now = time.time()
        expired = [
            sid for sid, s in self.sessions.items()
            if now - s.get('created_at', 0) > self._session_ttl
        ]
        for sid in expired:
            del self.sessions[sid]

    def start_session(self, user_id, difficulty='medium'):
        """
        Start a new free-conversation session.

        Returns a dict with:
            - session_id
            - examiner_name, examiner_voice
            - phase, phase_name
            - utterance (the greeting to play)
            - expects_response (True)
            - is_complete (False)
        """
        self._cleanup_old_sessions()

        session_id = uuid.uuid4().hex[:12]
        persona = random.choice(EXAMINER_PERSONAS)

        greeting = self._generate_greeting(persona)

        session = {
            'session_id': session_id,
            'user_id': user_id,
            'difficulty': difficulty,
            'persona': persona,
            'created_at': time.time(),
            'phase': 1,
            'phase_started_at': time.time(),
            'context': [], # [{role, text, ts, phase}]
            'part2_topic': None,
            'part2_cue_card': None,
            'part2_state': 'pending', # pending → prep_done → speak_done → followup_done
            'exchanges': 0,
            'total_words': 0,
            'warnings_count': 0,
            'completed': False,
        }

        # Record greeting as first examiner turn
        session['context'].append({
            'role': 'examiner',
            'text': greeting,
            'ts': time.time(),
            'phase': 1,
        })
        self.sessions[session_id] = session

        logger.info(
            f" [free] Session {session_id} started — "
            f"user={user_id} examiner={persona['name']} "
            f"style={persona['style']}"
        )

        return {
            'success': True,
            'session_id': session_id,
            'examiner_name': persona['name'],
            'examiner_voice': persona['voice'],
            'examiner_style': persona['style'],
            'phase': 1,
            'phase_name': PHASE_CONFIG[1]['name'],
            'phase_duration_sec': PHASE_CONFIG[1]['duration_sec'],
            'utterance': greeting,
            'expects_response': True,
            'is_complete': False,
        }

    def end_session(self, session_id):
        """
        Terminate a session and return a summary of the conversation.
        """
        session = self.sessions.pop(session_id, None)
        if not session:
            return {'success': False, 'error': 'Session not found'}

        user_entries = [c for c in session['context'] if c['role'] == 'user']
        total_words = sum(len(c['text'].split()) for c in user_entries)

        return {
            'success': True,
            'session_id': session_id,
            'exchanges': len(user_entries),
            'total_words': total_words,
            'warnings_count': session['warnings_count'],
            'duration_sec': int(time.time() - session['created_at']),
            'context': session['context'],
        }

    # ═══════════════════════════════════════════════════════════
    # MAIN RESPONSE HANDLER
    # ═══════════════════════════════════════════════════════════
    def process_response(self, session_id, transcript):
        """
        Called when the candidate has finished speaking.

        Steps:
          1. Store the user's transcript in context
          2. Analyze the response (warnings, band estimate)
          3. Decide the next move (follow-up / next question / phase change)
          4. Generate the examiner's next utterance
          5. Advance phase / part2 sub-state if needed
          6. Return a structured response for the frontend

        Return shape (success):
        {
            'success': True,
            'session_id': str,
            'utterance': str, # what the examiner says next
            'phase': int,
            'phase_name': str,
            'phase_changed': bool, # True if a phase transition happened
            'warnings': [{'type': str, 'msg': str}, ...],
            'encouragement': str,
            'band_estimate': float | None,
            'expects_response': bool,
            'is_complete': bool,
            'cue_card': dict | None, # only for Part 2 cue card step
            'timer': dict | None, # {'type': 'prep'|'speak', 'seconds': int}
            'part2_state': str | None, # current sub-state during Part 2
        }
        """
        session = self.sessions.get(session_id)
        if not session:
            return {'success': False, 'error': 'Session not found'}

        transcript = (transcript or '').strip()
        word_count = len(transcript.split()) if transcript else 0
        session['total_words'] += word_count
        session['exchanges'] += 1

        # Add user response to context
        session['context'].append({
            'role': 'user',
            'text': transcript,
            'ts': time.time(),
            'phase': session['phase'],
        })

        # Analyze
        analysis = self._analyze_response(session, transcript, word_count)
        session['warnings_count'] += len(analysis.get('warnings', []))

        # Decide next move
        move = self._decide_next_move(session, analysis)

        # Generate utterance
        utterance = self._generate_utterance(session, move, analysis)

        # Record examiner utterance in context (skip for 'wait')
        if utterance:
            session['context'].append({
                'role': 'examiner',
                'text': utterance,
                'ts': time.time(),
                'phase': session['phase'],
            })

        # Apply phase change if any
        phase_changed = False
        if move.get('new_phase'):
            old_phase = session['phase']
            session['phase'] = move['new_phase']
            session['phase_started_at'] = time.time()

            if move['new_phase'] == 2:
                session['part2_state'] = 'pending'

            phase_changed = True
            logger.info(
                f" [free] Session {session_id} phase {old_phase} → {move['new_phase']}"
            )

        # Advance Part 2 sub-state
        if session['phase'] == 2:
            self._advance_part2_state(session, move)

        is_complete = bool(move.get('is_complete'))
        if is_complete:
            session['completed'] = True
            logger.info(f" [free] Session {session_id} completed")

        return {
            'success': True,
            'session_id': session_id,
            'utterance': utterance,
            'phase': session['phase'],
            'phase_name': PHASE_CONFIG.get(session['phase'], {}).get('name', ''),
            'phase_changed': phase_changed,
            'warnings': analysis.get('warnings', []),
            'encouragement': analysis.get('encouragement', ''),
            'band_estimate': analysis.get('band_estimate'),
            'expects_response': not is_complete,
            'is_complete': is_complete,
            'cue_card': move.get('cue_card'),
            'timer': move.get('timer'),
            'part2_state': session.get('part2_state') if session['phase'] == 2 else None,
        }

    # ═══════════════════════════════════════════════════════════
    # GREETING
    # ═══════════════════════════════════════════════════════════
    def _generate_greeting(self, persona):
        """Generate the opening greeting for the session."""
        prompt = (
            f"You are {persona['name']}, an IELTS Speaking examiner "
            f"with a {persona['style']} style.\n\n"
            "Generate the opening greeting for an IELTS Speaking test. It must:\n"
            "1. Greet warmly ('Good morning' or 'Good afternoon')\n"
            "2. Introduce yourself by name\n"
            "3. Ask for the candidate's full name\n\n"
            "Exactly 2-3 sentences. Return ONLY the greeting text, nothing else."
        )
        try:
            result = self.ai.generate(prompt, max_tokens=200, temperature=0.7, fast=True)
            text = (result or '').strip().strip('"').strip()
            # Strip markdown or JSON leakage
            text = re.sub(r'^```.*?\n', '', text, flags=re.DOTALL)
            text = re.sub(r'\n```$', '', text).strip()
            if text and len(text) > 20 and len(text) < 500:
                return text
        except Exception as e:
            logger.warning(f"Greeting generation failed: {e}")

        return (
            f"Good morning. My name is {persona['name']}. "
            f"Could you tell me your full name, please?"
        )

    # ═══════════════════════════════════════════════════════════
    # RESPONSE ANALYSIS
    # ═══════════════════════════════════════════════════════════
    def _analyze_response(self, session, transcript, word_count):
        """
        Analyze the candidate's response and return warnings + metadata.

        Combines fast heuristics with a single AI call for deep analysis.
        """
        warnings_list = []

        # ─── Fast heuristics ─────────────────────────────────
        if word_count == 0:
            warnings_list.append({
                'type': 'no_response',
                'msg': WARNINGS['no_response'],
            })
        elif word_count < 20:
            warnings_list.append({
                'type': 'too_short',
                'msg': WARNINGS['too_short'],
            })

        if word_count > 0:
            fillers = len(re.findall(
                r'\b(um|uh|er|ah|like|you know)\b',
                transcript.lower(),
            ))
            if fillers / max(1, word_count) > 0.08:
                warnings_list.append({
                    'type': 'hesitation',
                    'msg': WARNINGS['hesitation'],
                })

        # ─── AI deep analysis ─────────────────────────────────
        band_estimate = None
        encouragement = ''
        if word_count >= 15 and self.ai:
            try:
                ai_result = self._ai_analyze(session, transcript, word_count)
                if ai_result:
                    for w in ai_result.get('warnings', []):
                        wtype = w.get('type')
                        if wtype and not any(x['type'] == wtype for x in warnings_list):
                            warnings_list.append({
                                'type': wtype,
                                'msg': w.get('msg') or WARNINGS.get(wtype, ''),
                            })
                    band_estimate = ai_result.get('band_estimate')
                    encouragement = ai_result.get('encouragement', '')
            except Exception as e:
                logger.warning(f"AI analysis failed: {e}")

        if not warnings_list:
            encouragement = encouragement or ' Good response!'

        return {
            'warnings': warnings_list,
            'encouragement': encouragement,
            'band_estimate': band_estimate,
            'word_count': word_count,
        }

    def _ai_analyze(self, session, transcript, word_count):
        """Call AI for deep response analysis. Returns parsed dict or None."""
        last_q = self._last_examiner_question(session) or '(opening)'

        prompt = (
            "You are an IELTS Speaking examiner analyzing a candidate's response.\n\n"
            f"Question: {last_q}\n"
            f"Response: \"{transcript}\"\n"
            f"Part: {session['phase']}\n"
            f"Word count: {word_count}\n\n"
            "Evaluate and return ONLY this JSON:\n"
            "{\n"
            ' "relevance": <0.0-1.0>,\n'
            ' "band_estimate": <4.5-9.0>,\n'
            ' "warnings": [{"type": "off_topic|no_detail|repetition", "msg": "brief specific advice"}],\n'
            ' "encouragement": "one short positive note"\n'
            "}\n\n"
            "RULES:\n"
            "- If on-topic and 30+ words, warnings can be an empty array.\n"
            "- Warning messages must be < 100 chars and specific.\n"
            "- band_estimate must be realistic (most responses are 5.5-7.0).\n"
            "- Return valid JSON only, no markdown fences.\n"
        )

        try:
            result = self.ai.generate(
                prompt, max_tokens=400, temperature=0.3, fast=True
            )
            match = re.search(r'\{.*\}', result or '', re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:
            logger.warning(f"AI analyze parse failed: {e}")

        return None

    def _last_examiner_question(self, session):
        """Return the last examiner utterance that ended with a '?'."""
        for entry in reversed(session['context']):
            if entry['role'] == 'examiner' and entry['text'].strip().endswith('?'):
                return entry['text']
        return None

    # ═══════════════════════════════════════════════════════════
    # DECISION LOGIC
    # ═══════════════════════════════════════════════════════════
    def _decide_next_move(self, session, analysis):
        """Decide what the examiner should do next."""
        phase = session['phase']
        elapsed = time.time() - session['phase_started_at']
        exchanges = sum(
            1 for c in session['context']
            if c.get('phase') == phase and c['role'] == 'user'
        )

        if phase == 1:
            return self._decide_part1(session, elapsed, exchanges, analysis)
        if phase == 2:
            return self._decide_part2(session, elapsed)
        if phase == 3:
            return self._decide_part3(session, elapsed, exchanges)

        return {'action': 'end', 'is_complete': True}

    def _decide_part1(self, session, elapsed, exchanges, analysis):
        """Part 1: simple interview. Transition after duration or ~4-5 exchanges."""
        if elapsed >= PHASE_CONFIG[1]['duration_sec']:
            return {'action': 'transition', 'new_phase': 2}

        # If the last answer was very short, ask a follow-up
        if analysis['word_count'] < 25 and exchanges >= 2:
            return {'action': 'follow_up'}

        # If Part 1 has run for a while (4+ user turns), move on
        if exchanges >= 5 and elapsed >= 180:
            return {'action': 'transition', 'new_phase': 2}

        return {'action': 'next_question'}

    def _decide_part2(self, session, elapsed):
        """
        Part 2 sub-states:
          pending → we generate the cue card + start prep timer
          prep_done → prep done, now speak for 2 min
          speak_done → long turn done, ask one follow-up
          followup_done → move on to Part 3
        """
        state = session.get('part2_state', 'pending')

        if state == 'pending':
            return {
                'action': 'cue_card',
                'cue_card': session.get('part2_cue_card'),
                'timer': {'type': 'prep', 'seconds': PHASE_CONFIG[2]['prep_sec']},
            }

        if state == 'prep_done':
            return {
                'action': 'speak',
                'timer': {'type': 'speak', 'seconds': PHASE_CONFIG[2]['speak_sec']},
            }

        if state == 'speak_done':
            return {'action': 'follow_up'}

        if state == 'followup_done' or elapsed >= 240:
            return {'action': 'transition', 'new_phase': 3}

        # Waiting — shouldn't happen but safe
        return {'action': 'wait'}

    def _decide_part3(self, session, elapsed, exchanges):
        """Part 3: abstract discussion. End after duration or ~6 exchanges."""
        config = PHASE_CONFIG[3]
        if elapsed >= config['duration_sec'] or exchanges >= 6:
            return {'action': 'end', 'is_complete': True}

        return {'action': 'next_question'}

    def _advance_part2_state(self, session, move):
        """
        Advance the Part 2 sub-state machine after generating the utterance.
        Called once per process_response while in Part 2.
        """
        action = move.get('action')
        if action == 'cue_card':
            session['part2_state'] = 'prep_done'
        elif action == 'speak':
            session['part2_state'] = 'speak_done'
        elif action == 'follow_up':
            session['part2_state'] = 'followup_done'

    # ═══════════════════════════════════════════════════════════
    # UTTERANCE GENERATION
    # ═══════════════════════════════════════════════════════════
    def _generate_utterance(self, session, move, analysis):
        """Dispatch to the right utterance generator based on action."""
        action = move.get('action', 'next_question')

        if action == 'end':
            return self._farewell(session)
        if action == 'wait':
            return ''
        if action == 'transition':
            return self._transition(session, move.get('new_phase'))
        if action == 'cue_card':
            return self._cue_card_intro(session)
        if action == 'speak':
            return "Alright, you may begin speaking now."
        if action == 'follow_up':
            return self._follow_up(session, analysis)

        return self._next_question(session)

    def _farewell(self, session):
        """Warm closing of the test."""
        prompt = (
            f"You are {session['persona']['name']}, an IELTS examiner.\n"
            "The test is complete. Generate a warm, professional closing "
            "(exactly 2 sentences). Thank the candidate for their time.\n"
            "Return ONLY the text."
        )
        try:
            r = self.ai.generate(prompt, max_tokens=150, temperature=0.7, fast=True)
            text = (r or '').strip().strip('"').strip()
            if text and 15 < len(text) < 400:
                return text
        except Exception:
            pass
        return (
            "Thank you very much. That is the end of the speaking test. "
            "Goodbye."
        )

    def _transition(self, session, new_phase):
        """Natural transition between parts."""
        if new_phase == 2:
            return (
                "Thank you. That is the end of Part 1. "
                "Now I'd like to move on to Part 2."
            )
        if new_phase == 3:
            return (
                "Thank you. Let's move on to Part 3, "
                "where we'll discuss some broader issues."
            )

        # Generic AI transition
        prompt = (
            f"You are {session['persona']['name']}, an IELTS examiner.\n"
            f"The candidate just finished Part {session['phase']}. "
            f"Now transition to Part {new_phase}.\n"
            "Generate a brief 1-2 sentence transition. Return ONLY the text."
        )
        try:
            r = self.ai.generate(prompt, max_tokens=150, temperature=0.7, fast=True)
            text = (r or '').strip().strip('"').strip()
            if text:
                return text
        except Exception:
            pass
        return "Let's move on."

    def _cue_card_intro(self, session):
        """Generate + announce Part 2 cue card."""
        if not session.get('part2_cue_card'):
            card = self._generate_cue_card(session)
            session['part2_topic'] = card['title']
            session['part2_cue_card'] = card

        card = session['part2_cue_card']
        return (
            f"Now, I'm going to give you a topic and you'll have one minute "
            f"to prepare. Here is your topic: {card['title']}. "
            f"You have one minute to prepare. You can make notes if you wish."
        )

    def _generate_cue_card(self, session):
        """Ask AI to design a cue card based on recent Part 1 conversation."""
        recent = ' '.join(
            c['text'] for c in session['context'][-6:] if c['role'] == 'user'
        )[:400]

        prompt = (
            "You are an IELTS examiner designing a Part 2 cue card.\n\n"
            f"Recent Part 1 topics discussed by the candidate: \"{recent}\"\n\n"
            "Generate a Part 2 cue card. Return ONLY this JSON:\n"
            "{\n"
            ' "title": "Describe a ...",\n'
            ' "prompts": ["what/where it is", "when/who with", '
            '"why you...", "and explain..."],\n'
            ' "follow_up": "One follow-up question to ask after their long turn"\n'
            "}\n\n"
            "The title must start with 'Describe'. The 'prompts' array must "
            "have exactly 4 short bullet points. No markdown, valid JSON only."
        )

        try:
            r = self.ai.generate(prompt, max_tokens=400, temperature=0.85, fast=True)
            match = re.search(r'\{.*\}', r or '', re.DOTALL)
            if match:
                card = json.loads(match.group())
                if card.get('title') and card.get('prompts'):
                    return card
        except Exception as e:
            logger.warning(f"Cue card generation failed: {e}")

        # Fallback cue card
        return {
            'title': 'Describe a memorable journey you have taken',
            'prompts': [
                'where you went',
                'who you went with',
                'what you did there',
                'and explain why it was memorable',
            ],
            'follow_up': 'Would you like to take a similar journey again?',
        }

    def _follow_up(self, session, analysis):
        """Generate a follow-up question that invites more detail."""
        last_q = self._last_examiner_question(session) or '(previous)'
        prompt = (
            f"You are {session['persona']['name']}, an IELTS examiner.\n"
            f"Last question: {last_q}\n"
            f"Candidate answered in ~{analysis.get('word_count', 0)} words.\n\n"
            "Generate ONE follow-up question that invites more detail.\n"
            "Start with a brief acknowledgment ('I see', 'Right', 'Interesting').\n"
            "Return ONLY the text (1-2 sentences, ending with a question mark)."
        )
        try:
            r = self.ai.generate(prompt, max_tokens=180, temperature=0.8, fast=True)
            text = (r or '').strip().strip('"').strip()
            if text and len(text) > 10:
                return text
        except Exception:
            pass
        return "I see. Could you tell me a bit more about that?"

    def _next_question(self, session):
        """Generate the next question based on the current phase and context."""
        phase = session['phase']

        # Build a compact context string
        recent = session['context'][-8:]
        ctx = '\n'.join(
            f"{'Examiner' if c['role'] == 'examiner' else 'Candidate'}: {c['text']}"
            for c in recent
        ) or '(opening)'

        if phase == 1:
            guide = (
                "Part 1: simple personal questions. Topics: hometown, work/study, "
                "hobbies, food, weather, travel. Ask about 3 questions per topic "
                "before moving on. Keep each question short (1-2 sentences)."
            )
        elif phase == 3:
            guide = (
                "Part 3: abstract discussion. Ask questions about comparisons "
                "(past vs present, culture vs culture), reasons, predictions, "
                "society-level opinions. Push for depth with 'Why?', "
                "'How has this changed?'. Relate questions to the Part 2 topic."
            )
        else:
            guide = "Continue the discussion naturally."

        prompt = (
            f"You are {session['persona']['name']}, an IELTS examiner.\n"
            f"Your style: {session['persona']['style']}.\n\n"
            f"{guide}\n\n"
            f"Conversation so far:\n{ctx}\n\n"
            "Generate your NEXT utterance:\n"
            "1. Brief acknowledgment (optional, one short clause)\n"
            "2. Next question (1-2 sentences, ends with '?')\n\n"
            "Return ONLY the text — no quotes, no JSON, no markdown."
        )

        try:
            r = self.ai.generate(prompt, max_tokens=300, temperature=0.85, fast=True)
            text = (r or '').strip().strip('"').strip()

            # Strip JSON leakage if the model ignored our instruction
            if text.startswith('{'):
                m = re.search(r'"text"\s*:\s*"([^"]+)"', text)
                if m:
                    text = m.group(1)
                else:
                    m2 = re.search(r'"question"\s*:\s*"([^"]+)"', text)
                    if m2:
                        text = m2.group(1)

            # Must end with a question mark
            if text and '?' not in text[-5:]:
                text = text.rstrip('.!') + '?'

            if text and len(text) > 10:
                return text
        except Exception as e:
            logger.warning(f"Next question generation failed: {e}")

        # Fallbacks by phase
        if phase == 1:
            return "That's interesting. Could you tell me more about that?"
        if phase == 3:
            return "Why do you think that is the case?"
        return "Could you tell me more about that?"

    # ═══════════════════════════════════════════════════════════
    # STATUS
    # ═══════════════════════════════════════════════════════════
    def get_session_state(self, session_id):
        """Return the current state of a session (for frontend polling)."""
        s = self.sessions.get(session_id)
        if not s:
            return {'success': False, 'error': 'Session not found'}

        elapsed = time.time() - s['phase_started_at']
        cfg = PHASE_CONFIG.get(s['phase'], {})

        return {
            'success': True,
            'session_id': session_id,
            'phase': s['phase'],
            'phase_name': cfg.get('name', ''),
            'phase_elapsed_sec': int(elapsed),
            'phase_duration_sec': cfg.get('duration_sec', 0),
            'exchanges': s['exchanges'],
            'total_words': s['total_words'],
            'warnings_count': s['warnings_count'],
            'completed': s['completed'],
            'part2_state': s.get('part2_state') if s['phase'] == 2 else None,
            'examiner_name': s['persona']['name'],
            'examiner_voice': s['persona']['voice'],
        }


__all__ = [
    'FreeConversationEngine',
    'EXAMINER_PERSONAS',
    'PHASE_CONFIG',
    'WARNINGS',
]