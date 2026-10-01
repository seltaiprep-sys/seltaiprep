# ai_engine.py
"""
AI Engine — DeepSeek-only edition (v9.0)

CHANGELOG:
  v9.0 — Mistral completely removed.
         Reason: free tier rate-limited (5 RPM) → unreliable fallback,
         wasted 50+ seconds per DeepSeek failure with useless retries.
         DeepSeek paid tier is 99% reliable, no fallback needed.

  v8.x — Multi-provider (DeepSeek + Mistral) with retry rounds.
"""
import requests
import json
import os
import random
import time
import re
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class AIEngine:
    """
    DeepSeek-only AI Engine.

    Handles:
      • Text generation with automatic retry on network errors
      • Multi-round retry with exponential backoff (5s → 15s → 30s)
      • Short-response acceptance (min 5 chars)
      • Structured methods for IELTS modules
    """

    def __init__(self):
        self.deepseek_api_key = os.getenv('DEEPSEEK_API_KEY')
        self.deepseek_url = "https://api.deepseek.com/v1/chat/completions"
        self.current_provider = "deepseek"

        if not self.deepseek_api_key:
            print("[WARN] DEEPSEEK_API_KEY not found - DeepSeek will be skipped")
            logger.warning("DEEPSEEK_API_KEY missing — AI generation will fail")
        else:
            print("[OK] AI Engine initialized (Provider: DeepSeek)")

    # ═══════════════════════════════════════════════════════════
    # ERROR CLASSIFIERS
    # ═══════════════════════════════════════════════════════════
    @staticmethod
    def _is_network_error(err: Exception) -> bool:
        """Return True for DNS / connection / timeout / SSL errors."""
        msg = str(err).lower()
        keywords = [
            'connection', 'timeout', 'timed out', 'resolve', 'getaddrinfo',
            'name resolution', 'max retries', 'ssl', 'unreachable',
            'connectionerror', 'readtimeout', 'connecttimeout',
            'nameresolutionerror', 'network', 'temporarily unavailable',
            'host', 'dns',
        ]
        return any(kw in msg for kw in keywords)

    @staticmethod
    def _is_rate_limit_error(err: Exception) -> bool:
        """Return True for 429 / rate-limit errors."""
        msg = str(err).lower()
        return ('429' in msg or 'rate limit' in msg or 'too many' in msg
                or 'quota' in msg)

    @staticmethod
    def _is_valid_response(result: Optional[str], min_length: int = 5) -> bool:
        """Return True if the AI response is usable."""
        if not result:
            return False
        if not isinstance(result, str):
            return False
        stripped = result.strip()
        if not stripped:
            return False
        if len(stripped) < min_length:
            return False
        return True

    # ═══════════════════════════════════════════════════════════
    # MAIN — generate() with multi-round retry + backoff
    # ═══════════════════════════════════════════════════════════
    def generate(
        self,
        prompt: str,
        max_tokens: int = 8000,
        temperature: float = 0.8,
        fast: bool = False,
        timeout: Optional[int] = None,
        max_rounds: int = 3,
        min_response_length: int = 5,
    ) -> str:
        """
        Generate text with DeepSeek — with automatic retry on failures.

        Retry strategy:
          • Up to `max_rounds` attempts
          • Exponential backoff: 5s → 15s → 30s
          • Accepts short responses (min 5 chars)
          • Only raises after all rounds fail
        """
        if max_tokens > 8192:
            max_tokens = 8192
        if max_tokens < 500:
            max_tokens = 500

        if fast:
            temperature = min(temperature, 0.7)
            timeout = timeout or 180
        else:
            timeout = timeout or 300

        backoff_schedule = [5, 15, 30]
        last_error: Optional[Exception] = None

        for round_idx in range(max_rounds):
            if round_idx > 0:
                wait = backoff_schedule[min(round_idx - 1, len(backoff_schedule) - 1)]
                print(
                    f"[INFO] DeepSeek failed. "
                    f"Retry round {round_idx + 1}/{max_rounds} in {wait}s..."
                )
                time.sleep(wait)

            # ─── Try DeepSeek ─────────────────────────────────
            if not self.deepseek_api_key:
                last_error = Exception("DEEPSEEK_API_KEY not set")
                continue

            try:
                result = self._call_deepseek_api(
                    prompt, max_tokens, temperature, timeout
                )
                if self._is_valid_response(result, min_response_length):
                    if round_idx > 0:
                        print(f"[OK] DeepSeek succeeded on retry round {round_idx + 1}")
                    return result
                else:
                    print(
                        f"[WARN] DeepSeek returned short/empty response "
                        f"({len(result.strip()) if result else 0} chars)"
                    )
                    last_error = Exception(
                        f"DeepSeek short response: "
                        f"{len(result.strip()) if result else 0} chars"
                    )
            except Exception as e:
                last_error = e
                if self._is_network_error(e):
                    print(
                        f"[WARN] DeepSeek network error "
                        f"(round {round_idx + 1}): {str(e)[:100]}"
                    )
                elif self._is_rate_limit_error(e):
                    print(
                        f"[WARN] DeepSeek rate limited "
                        f"(round {round_idx + 1}): {str(e)[:100]}"
                    )
                else:
                    print(
                        f"[WARN] DeepSeek failed "
                        f"(round {round_idx + 1}): {str(e)[:100]}"
                    )

        # ═══════════════════════════════════════════════════════
        # All rounds exhausted
        # ═══════════════════════════════════════════════════════
        error_msg = str(last_error)[:200] if last_error else "unknown error"
        print(
            f"[ERROR] DeepSeek failed after {max_rounds} retry rounds. "
            f"Last error: {error_msg}"
        )
        raise Exception(
            f"AI generation failed after {max_rounds} retry rounds: {error_msg}"
        )

    # ═══════════════════════════════════════════════════════════
    # DEEPSEEK API
    # ═══════════════════════════════════════════════════════════
    def _call_deepseek_api(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            response = requests.post(
                self.deepseek_url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except requests.exceptions.Timeout:
            raise Exception(f"DeepSeek timeout after {timeout}s")
        except requests.exceptions.ConnectionError as e:
            raise Exception(f"DeepSeek connection error: {str(e)[:120]}")
        except requests.exceptions.RequestException as e:
            raise Exception(f"DeepSeek request error: {str(e)[:120]}")

        if response.status_code == 200:
            try:
                result = response.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError) as e:
                raise Exception(f"DeepSeek malformed response: {str(e)[:120]}")
            print(
                f" [OK] DeepSeek: {len(result)} chars "
                f"(tokens requested: {max_tokens})"
            )
            return result
        elif response.status_code == 429:
            raise Exception("DeepSeek rate limit reached")
        elif response.status_code == 402:
            raise Exception("DeepSeek insufficient balance — add credits")
        elif response.status_code == 401:
            raise Exception("DeepSeek unauthorized — check API key")
        else:
            raise Exception(
                f"DeepSeek API Error {response.status_code}: "
                f"{response.text[:100]}"
            )

    # ═══════════════════════════════════════════════════════════
    # JSON CLEANER
    # ═══════════════════════════════════════════════════════════
    def _clean_json_response(self, text: str) -> str:
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        text = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', text)
        text = re.sub(r',\s*}', '}', text)
        text = re.sub(r',\s*\]', ']', text)
        return text.strip()

    # ═══════════════════════════════════════════════════════════
    # SPEAKING EVALUATOR (local, no API call)
    # ═══════════════════════════════════════════════════════════
    def evaluate_speaking_response(
        self, question: str, response: str, part: int
    ) -> Dict:
        word_count = len(response.split())
        if word_count > 100:
            score = 7.5
        elif word_count > 60:
            score = 6.5
        elif word_count > 30:
            score = 5.5
        else:
            score = 5.0
        return {
            "score": score,
            "band": round(score),
            "fluency": score - 0.5,
            "vocabulary": score,
            "grammar": score - 0.5,
            "coherence": score,
            "feedback": [
                "Good effort! Keep practicing.",
                "Try to extend your answers with examples.",
                "Use more varied vocabulary.",
            ],
            "strengths": ["Clear communication", "Good attempt"],
            "word_count": word_count,
        }


# ═══════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════
ai_engine = AIEngine()