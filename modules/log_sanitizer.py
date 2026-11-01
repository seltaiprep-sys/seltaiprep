"""log_sanitizer.py — Redact sensitive data from all log messages"""
import logging
import re


class LogSanitizer(logging.Filter):
    """Filter that redacts sensitive data from log records."""

    # Entire message suppressed if any keyword present
    SUPPRESS_KEYWORDS = [
        'SCRIPT (',
        'Options:',
        'Correct:',
        'PASSAGE (',
        'TRANSCRIPT (',
        'Voice:',
        'voice=aura-',
        'voice=en-',
    ]

    # Patterns: (compiled_regex, replacement)
    PATTERNS = [
        # Passwords
        (re.compile(r"(password\s*[=:]\s*[\"']?)([^\"'\s,}]+)", re.I), r"\1***REDACTED***"),
        (re.compile(r"(new_password\s*[=:]\s*[\"']?)([^\"'\s,}]+)", re.I), r"\1***REDACTED***"),
        (re.compile(r"(passwd\s*[=:]\s*[\"']?)([^\"'\s,}]+)", re.I), r"\1***REDACTED***"),
        (re.compile(r"(pwd\s*[=:]\s*[\"']?)([^\"'\s,}]+)", re.I), r"\1***REDACTED***"),
        # API keys and secrets
        (re.compile(r"(api[_-]?key\s*[=:]\s*[\"']?)([A-Za-z0-9_\-]{10,})", re.I), r"\1***KEY***"),
        (re.compile(r"(secret\s*[=:]\s*[\"']?)([A-Za-z0-9_\-]{8,})", re.I), r"\1***SECRET***"),
        (re.compile(r"(Authorization\s*[:=]\s*[\"']?(?:Token|Bearer)\s+)([A-Za-z0-9_\-]+)", re.I), r"\1***TOKEN***"),
        # Tokens (JWT, hex, etc.) — generic
        (re.compile(r"(token\s*[=:]\s*[\"']?)([A-Za-z0-9_\-]{15,})", re.I), r"\1***TOKEN***"),
        (re.compile(r"(track_token\s*[=:]\s*[\"']?)([A-Za-z0-9_\-]{10,})", re.I), r"\1***TOKEN***"),
        (re.compile(r"(test_token\s*[=:]\s*[\"']?)([A-Za-z0-9_\-]{10,})", re.I), r"\1***TOKEN***"),
        # Track tokens in URLs
        (re.compile(r"(/ukvi/track/)([A-Za-z0-9_\-]{10,})"), r"\1***TOKEN***"),
        # Emails
        (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "***@***.***"),
        # Phone (Nepal + international)
        (re.compile(r"\+?977[\-\s]?\d{2,3}[\-\s]?\d{6,7}"), "+977-***-HIDDEN"),
        (re.compile(r"\+?\d{1,3}[\-\s]?\d{3,4}[\-\s]?\d{3,4}[\-\s]?\d{3,4}"), "***PHONE***"),
        # Audio paths (contain identifiers)
        (re.compile(r"/static/audio_cache/[^\s\"'<>]+"), "/static/audio_cache/***"),
        # Session cookies
        (re.compile(r"(session\s*[=:]\s*)[A-Za-z0-9_\-.%]{20,}", re.I), r"\1***SESSION***"),
        (re.compile(r"(Cookie\s*:\s*).+", re.I), r"\1***COOKIE***"),
        # Test content (extra safety)
        (re.compile(r"(SCRIPT\s*\([^)]*\)\s*:\s*).*", re.I), r"\1***HIDDEN***"),
        (re.compile(r"(Options\s*:\s*).*", re.I), r"\1***HIDDEN***"),
        (re.compile(r"(Correct\s*:\s*).*", re.I), r"\1***HIDDEN***"),
        (re.compile(r"(PASSAGE|TRANSCRIPT|TEXT)\s*(\([^)]*\))?\s*:\s*.+", re.I), r"\1 ***HIDDEN***"),
        # Transcripts
        (re.compile(r"(Final\s*:\s*).+", re.I), r"\1***"),
    ]

    def filter(self, record):
        try:
            msg = str(record.getMessage())

            # Full suppression
            for kw in self.SUPPRESS_KEYWORDS:
                if kw in msg:
                    return False

            # Apply redactions
            redacted = msg
            for pat, repl in self.PATTERNS:
                redacted = pat.sub(repl, redacted)

            # Truncate long messages
            if len(redacted) > 500:
                redacted = redacted[:500] + '...[TRUNCATED]'

            record.msg = redacted
            record.args = ()
            return True
        except Exception:
            return True


def install_log_sanitizer(app=None):
    """Install sanitizer on all loggers + handlers, reduce noisy log levels."""
    sanitizer = LogSanitizer()

    root = logging.getLogger()
    root.addFilter(sanitizer)
    for h in root.handlers:
        h.addFilter(sanitizer)

    if app is not None:
        try:
            app.logger.addFilter(sanitizer)
        except Exception:
            pass

    # Reduce verbose loggers
    quiet = [
        'modules.ielts.listening',
        'modules.pte.generators',
        'modules.ielts.speaking',
        'modules.ukvi.generators',
        'modules.ukvi.managers',
        'modules.audio',
        'modules.pte.utils',
    ]
    for name in quiet:
        try:
            logging.getLogger(name).setLevel(logging.WARNING)
        except Exception:
            pass

    return sanitizer


# Auto-install if imported
if __name__ != '__main__':
    install_log_sanitizer()