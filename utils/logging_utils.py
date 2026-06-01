"""
utils/logging_utils.py
Configuracion centralizada de logging para la aplicacion.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from config import settings


def _setting(name: str, default: Any = None) -> Any:
    return getattr(settings, name, os.getenv(name, default))


def configure_logging() -> None:
    """Inicializa logging una sola vez usando LOG_LEVEL del entorno."""
    level_name = str(_setting("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger()
    if getattr(configure_logging, "_configured", False):
        root_logger.setLevel(level)
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    configure_logging._configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
