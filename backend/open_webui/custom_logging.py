"""Compatibility setup for disabling uvicorn's duplicate access records."""

import logging

ACCESS_LOG_NAME = "uvicorn.access"


def reconfigure_access_log() -> None:
    """Disable uvicorn access records; RequestContextMiddleware owns access logs."""
    logger = logging.getLogger(ACCESS_LOG_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.filters.clear()
    logger.disabled = True
    logger.propagate = False
