from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


class TaskLogger:
    def __init__(self, logs_dir: Path, task_id: str) -> None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.path = logs_dir / f"{task_id}.log"
        self.logger = logging.getLogger(f"apione.task.{task_id}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.logger.handlers.clear()
        handler = logging.FileHandler(self.path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        self.logger.addHandler(handler)

    def event(self, name: str, **fields: Any) -> None:
        self.logger.info(json.dumps({"event": name, **fields}, ensure_ascii=False, default=str))

    def close(self) -> None:
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)
