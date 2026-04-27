"""_logger.py — logger JSONL leve pra observabilidade do agente.

Cada evento e uma linha JSON em logs/agent.jsonl. Pode ser parseado por jq, Loki,
ELK, etc.
"""
from __future__ import annotations
import json
import time
import uuid
from pathlib import Path
from threading import Lock


class JsonLogger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def log(self, event: str, **fields):
        rec = {"ts": time.time(), "event": event, **fields}
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]
