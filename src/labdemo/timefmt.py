"""Tiny pure helper for the dashboard: how long ago was an ISO timestamp."""

from datetime import datetime


def elapsed_since(since_iso: str, now: datetime) -> str:
    """Return the time between since_iso and now as '12 s', '2 min 5 s' or '3 h 30 min'.

    A future timestamp (clock skew) clamps to '0 s'.
    """
    seconds = max(0, int((now - datetime.fromisoformat(since_iso)).total_seconds()))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"
