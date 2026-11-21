"""Raise the soft open-files limit (RLIMIT_NOFILE) as far as the OS allows.

macOS ships with a soft limit of 256 and systemd units default to 1024. Every ad
waiting for a broker slot holds one Unix-socket fd (plus one more on the broker
side), and every in-flight OpenAI call / Playwright page holds several more, so a
200+ ad report blows through those defaults with "[Errno 24] Too many open files".
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def raise_nofile_limit(target: int = 10240) -> int | None:
    """Best-effort: bump the soft limit to min(target, hard limit). Returns the new soft limit."""
    try:
        import resource
    except ImportError:          # Windows
        return None
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = target if hard == resource.RLIM_INFINITY else min(target, hard)
        if soft != resource.RLIM_INFINITY and soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
            logger.info("[fdlimit] RLIMIT_NOFILE soft limit raised %d -> %d (hard=%s)", soft, want, hard)
            return want
        return soft
    except (ValueError, OSError) as exc:
        logger.warning("[fdlimit] could not raise RLIMIT_NOFILE: %s", exc)
        return None
