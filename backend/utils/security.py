"""
utils/security.py — rate limiting and input sanitization.
Moved verbatim from app.py.
"""

import threading
import time
from functools import wraps

from flask import request, jsonify
import re

# ── Rate Limiter (fixed — properly wrapped) ──────────────────────────────────
_rate_data = {}
_rate_lock = threading.Lock()


def is_rate_limited(key, max_calls=5, window=60):
    """Returns True if key has exceeded max_calls in window seconds."""
    now = time.time()
    with _rate_lock:
        calls = [t for t in _rate_data.get(key, []) if now - t < window]
        if len(calls) >= max_calls:
            return True
        calls.append(now)
        _rate_data[key] = calls
    return False


def rate_limit(max_calls=5, window=60):
    """Properly working rate-limit decorator."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            key = f"{request.remote_addr}:{f.__name__}"
            if is_rate_limited(key, max_calls, window):
                return jsonify({"success": False,
                                "message": "Too many requests. Please wait."}), 429
            return f(*args, **kwargs)
        return wrapper
    return decorator


def sanitize(s, maxlen=128):
    if not s:
        return ""
    return re.sub(r'[<>\'\";&]', '', str(s).strip()[:maxlen])
