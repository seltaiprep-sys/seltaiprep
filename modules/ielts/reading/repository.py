"""Repository using SQLAlchemy models – module‑aware (IELTS Reading)

FIXES APPLIED:
- Constructor now accepts both `db_session` and `module` kwargs so callers
  like ReadingTestRepository(module='ielts') no longer crash.
- Uses `db.session` dynamically via a property instead of storing a stale
  session reference.
- Removed `module=_MODULE` from TestSession(...) — this attribute no longer
  exists on the model.
- Stops wrapping test_data in json.dumps (the column is a JSON type and
  accepts dicts directly).
- Uses db.session.query(...) instead of self.db.query(...).
"""

import json
import logging
from typing import Dict, Optional, List

from models import (
    get_test_session_model,
    get_test_result_model,
    db,
)

logger = logging.getLogger(__name__)

_MODULE = 'ielts'


class ReadingTestRepository:
    def __init__(self, db_session=None, module: str = 'ielts'):
        # Accept `db_session` for backward compatibility, but always use the
        # live `db.session` internally.
        self.module = module or 'ielts'
        self.TestSession = get_test_session_model(self.module)
        self.TestResult = get_test_result_model(self.module)

    # ============================================================
    # Dynamic session property — always the CURRENT request's session
    # ============================================================
    @property
    def session(self):
        return db.session
    # ============================================================

    def save_test(self, test_data: Dict, user_id: str) -> str:
        """Save a new test session."""
        try:
            # `module` is not an attribute of TestSession anymore.
            # `test_data` is a JSON column → pass the dict directly.
            session = self.TestSession(
                user_id=int(user_id),
                test_type='reading',
                test_data=test_data,
                status='in_progress'
            )
            self.session.add(session)
            self.session.commit()
            return str(session.id)
        except Exception as e:
            logger.error(f"Failed to save test: {e}")
            self.session.rollback()
            return ""

    def get_test(self, test_id: str) -> Optional[Dict]:
        """Get test data by session ID."""
        try:
            session = self.session.query(self.TestSession).filter_by(
                id=int(test_id)
            ).first()
            if session and session.test_data:
                if isinstance(session.test_data, dict):
                    return session.test_data
                # Fallback: if it was stored as a JSON string (legacy data)
                try:
                    return json.loads(session.test_data)
                except Exception:
                    return None
            return None
        except Exception as e:
            logger.error(f"Failed to get test: {e}")
            return None

    def save_results(self, test_id: str, user_answers: Dict, results: Dict, user_id: str) -> str:
        """Save test results (TestResult)."""
        try:
            # Update session status to completed
            session = self.session.query(self.TestSession).filter_by(
                id=int(test_id)
            ).first()
            if session:
                session.status = 'completed'
                session.answers_so_far = user_answers

            result = self.TestResult(
                user_id=int(user_id),
                test_type='reading',
                score=results.get('score_percentage', 0),
                band_score=results.get('band_score', 0),
                feedback=results.get('feedback', ''),
                answers=json.dumps(user_answers)
            )
            self.session.add(result)
            self.session.commit()
            return str(result.id)
        except Exception as e:
            logger.error(f"Failed to save results: {e}")
            self.session.rollback()
            return ""

    def get_user_history(self, user_id: str, limit=20) -> List[Dict]:
        """Get user's reading test history."""
        try:
            results = self.session.query(self.TestResult).filter_by(
                user_id=int(user_id),
                test_type='reading'
            ).order_by(self.TestResult.created_at.desc()).limit(limit).all()
            return [{
                'test_id': r.id,
                'score': r.score,
                'band': r.band_score,
                'date': r.created_at.isoformat() if r.created_at else None
            } for r in results]
        except Exception as e:
            logger.error(f"Failed to get history: {e}")
            return []