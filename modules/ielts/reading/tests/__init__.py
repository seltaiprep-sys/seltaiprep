"""Tests package for IELTS Reading module"""

# Import test functions for easy access
from .test_generator import run_all_tests as run_generator_tests
from .test_reliable import run_all_tests as run_reliable_tests
from .test_evaluator import run_all_tests as run_evaluator_tests
from .test_scoring import run_all_tests as run_scoring_tests

__all__ = [
    "run_generator_tests",
    "run_reliable_tests",
    "run_evaluator_tests",
    "run_scoring_tests",
]