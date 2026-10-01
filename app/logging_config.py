import logging
import sys

import structlog

from app.config import settings


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


SECRET_KEYS = {
    "groq_api_key",
    "authorization",
    "token",
    "jwt",
    "jwt_secret",
    "device_registration_secret",
    "password",
}


def redact(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if k.lower() in SECRET_KEYS:
            out[k] = "***REDACTED***"
        elif isinstance(v, dict):
            out[k] = redact(v)
        elif isinstance(v, list):
            out[k] = [redact(item) if isinstance(item, dict) else item for item in v]
        else:
            out[k] = v
    return out
