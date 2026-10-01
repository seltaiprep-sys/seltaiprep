"""Helper functions for text analysis."""

import re


def clean_text(text):
    """Clean and normalize text."""
    if not text:
        return ""
    text = ' '.join(text.split())
    text = re.sub(r'[^\w\s.,!?;:\'\"-]', '', text)
    return text.strip()


def count_words(text):
    """Count words in text."""
    if not text:
        return 0
    return len(text.split())