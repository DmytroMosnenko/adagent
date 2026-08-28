import logging


_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S,%f"[:-3]  # matches Python default asctime


def setup_root_logging() -> None:
    """
    Apply our formatter to the root logger so that third-party loggers
    (uvicorn, httpx, playwright, etc.) inherit the same format.
    Call once at application startup — idempotent.
    """
    root = logging.getLogger()
    if any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        # Already configured (e.g. called twice); just update formatter.
        for h in root.handlers:
            h.setFormatter(logging.Formatter(_FORMAT))
        return

    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(h)
    root.setLevel(logging.DEBUG)

    # Suppress noisy uvicorn access logs' own handler so they use ours.
    # uvicorn installs its own handlers only after our setup_root_logging()
    # call, so we mark it propagate=True (default) and level=NOTSET so it
    # bubbles up to the root handler with our formatter.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    return logger
