"""
AI Concurrency Limiter with automatic retry.
=============================================

Wraps any AI engine (DeepSeek / Mistral / etc.) so that
`ai_engine.generate()` is:
  • concurrency-limited (Semaphore)
  • thread-safe (safe for Flask threaded mode)
  • transparent — forwards ALL arguments to the wrapped engine

For PAID providers (DeepSeek Standard, Mistral Paid):
  → max_concurrent=20 is safe for 10-20 parallel users.

For FREE providers:
  → use max_concurrent=3 (DeepSeek free ~60 RPM)

v9.1 FIX — SIGNATURE PARITY:
  The wrapper's generate() now mirrors the underlying engine's full
  signature (prompt, max_tokens, temperature, fast, timeout,
  max_rounds, min_response_length, **kwargs). Previously only
  (prompt, max_tokens) were positional, so calls like
  `ai_engine.generate(prompt, 600, 0.7, 20)` raised
  "takes from 2 to 3 positional arguments but 4 were given".

  The wrapper's own retry loop is GONE — the underlying engine
  already implements multi-round retries with exponential backoff.
  The wrapper's only job is the semaphore (concurrency gate).

Usage in app.py
---------------
    from ai_engine import ai_engine
    from ai_queue import wrap_ai_engine

    ai_engine = wrap_ai_engine(ai_engine, max_concurrent=20)

Everything else (callers, modules) stays the same —
`ai_engine.generate(...)` transparently goes through the queue.
"""

import threading
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# RateLimitedAI — wrapper class
# ═══════════════════════════════════════════════════════════════════
class RateLimitedAI:
    """
    Thread-safe concurrency limiter around an AI engine.

    Behaviour
    ---------
      • Max N concurrent `generate()` calls (default 20).
      • Extra calls block on a Semaphore (FIFO-ish).
      • Every argument is forwarded verbatim to the underlying engine.
      • Retry logic lives in the engine — the wrapper does NOT
        duplicate it (prevents 2× backoff from double retry layers).
      • Every other attribute/method is proxied to the wrapped engine.
    """

    def __init__(self, engine, max_concurrent: int = 20, max_retries: int = 0):
        if engine is None:
            raise ValueError("RateLimitedAI: engine cannot be None")

        self._engine = engine
        self._sem = threading.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        # `max_retries` kept for API compatibility — ignored (engine retries).
        self._max_retries = max_retries

        logger.info(
            "[AI Queue] Initialized — max_concurrent=%s",
            max_concurrent,
        )

    # ───────────────────────────────────────────────────────────────
    # Public API — mirrors ai_engine.generate() exactly
    # ───────────────────────────────────────────────────────────────
    def generate(
        self,
        prompt: str,
        max_tokens: int = 8000,
        temperature: float = 0.8,
        fast: bool = False,
        timeout: Optional[int] = None,
        max_rounds: int = 3,
        min_response_length: int = 5,
        **kwargs,
    ) -> str:
        """
        Concurrency-limited pass-through to `engine.generate()`.

        Blocks up to 180s waiting for a free slot. If the slot cannot
        be acquired (server fully saturated), raises TimeoutError.
        """
        acquired = self._sem.acquire(timeout=180)
        if not acquired:
            raise TimeoutError(
                "AI queue saturated for 180s — all workers busy. Try again."
            )
        try:
            # Forward EVERYTHING — engine owns its own retry strategy
            return self._engine.generate(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                fast=fast,
                timeout=timeout,
                max_rounds=max_rounds,
                min_response_length=min_response_length,
                **kwargs,
            )
        finally:
            self._sem.release()

    # ───────────────────────────────────────────────────────────────
    # Proxy everything else to the underlying engine
    # (e.g. .providers, .model, .other_method())
    # ───────────────────────────────────────────────────────────────
    def __getattr__(self, name):
        # Avoid infinite recursion during __init__ if engine is missing
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._engine, name)

    def __repr__(self):
        return (
            f"<RateLimitedAI engine={self._engine!r} "
            f"max_concurrent={self._max_concurrent}>"
        )


# ═══════════════════════════════════════════════════════════════════
# Convenience factory
# ═══════════════════════════════════════════════════════════════════
def wrap_ai_engine(engine, max_concurrent: int = 20):
    """
    Wrap an AI engine with the concurrency limiter.

    Idempotent — calling twice returns the same wrapper.
    Returns None if `engine` is None.
    """
    if engine is None:
        return None
    if isinstance(engine, RateLimitedAI):
        return engine # already wrapped
    return RateLimitedAI(engine, max_concurrent=max_concurrent)


# ═══════════════════════════════════════════════════════════════════
# Self-test — run directly: python ai_queue.py
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import time
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    class _DummyEngine:
        def generate(self, prompt, max_tokens=1000, temperature=0.8,
                     fast=False, timeout=None, max_rounds=3,
                     min_response_length=5, **kwargs):
            time.sleep(0.1)
            return f"OK:{prompt[:20]}"

    wrapped = wrap_ai_engine(_DummyEngine(), max_concurrent=3)

    # Test 1 — simple call
    print("Test 1 — simple call")
    print(" Result:", wrapped.generate("hello world"))

    # Test 2 — positional args (the exact failure case)
    print("Test 2 — positional args (prompt, 600, 0.7, 20)")
    r = wrapped.generate("test positional", 600, 0.7, 20)
    print(" Result:", r)

    # Test 3 — keyword args
    print("Test 3 — keyword args")
    r = wrapped.generate("test keywords", max_tokens=500, temperature=0.5, timeout=10)
    print(" Result:", r)

    print(" ai_queue.py self-test passed")