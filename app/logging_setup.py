"""Strukturiertes JSON-Logging (Datensparsamkeit: niemals Dokumentinhalte)."""

from __future__ import annotations

import logging

from pythonjsonlogger.json import JsonFormatter


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers if h.formatter):
        return  # bereits konfiguriert (z.B. in Tests mehrfach aufgerufen)
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "time", "levelname": "level", "name": "logger"},
        )
    )
    root.handlers = [handler]
    root.setLevel(level)
