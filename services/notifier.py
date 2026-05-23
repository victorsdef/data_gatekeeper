"""
services/notifier.py
Notificaciones operativas simples por webhook HTTP.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict
from urllib.request import Request, urlopen

from config import settings
from utils.logging_utils import get_logger


def _setting(name: str, default: Any = None) -> Any:
    return getattr(settings, name, os.getenv(name, default))


ALERTS_ENABLED = str(_setting("ALERTS_ENABLED", "false")).lower() == "true"
ALERT_WEBHOOK_URL = str(_setting("ALERT_WEBHOOK_URL", "") or "").strip()
ALERT_ON_SUCCESS = str(_setting("ALERT_ON_SUCCESS", "false")).lower() == "true"
APP_ENV = str(_setting("APP_ENV", "production"))

logger = get_logger(__name__)


def _post_json(url: str, payload: Dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        status = getattr(response, "status", 500)
        if status >= 400:
            raise RuntimeError(f"Webhook respondió con estado {status}")


def notify_load_event(event_type: str, payload: Dict[str, Any]) -> None:
    """
    Envía una alerta operativa si está habilitada.
    event_type: load_failed | load_succeeded
    """
    if not ALERTS_ENABLED or not ALERT_WEBHOOK_URL:
        return
    if event_type == "load_succeeded" and not ALERT_ON_SUCCESS:
        return

    body = {
        "source": "data_gatekeeper",
        "environment": APP_ENV,
        "event_type": event_type,
        "payload": payload,
    }
    try:
        _post_json(ALERT_WEBHOOK_URL, body)
        logger.info("Notificación webhook enviada event_type=%s", event_type)
    except Exception:
        logger.exception("No se pudo enviar la notificación webhook event_type=%s", event_type)
