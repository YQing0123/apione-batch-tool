from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Iterable

from .models import TaskConfig


class TaskStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.data_dir = root / "data"
        self.tasks_file = self.data_dir / "tasks.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[TaskConfig]:
        if not self.tasks_file.exists():
            return []
        with self.tasks_file.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return [TaskConfig.from_dict(item) for item in data.get("tasks", [])]

    def save(self, tasks: Iterable[TaskConfig]) -> None:
        payload = {"schema_version": 1, "tasks": [task.to_dict() for task in tasks]}
        self._atomic_write(payload)

    def save_secrets(self, tasks: Iterable[TaskConfig]) -> None:
        secrets_file = self.data_dir / "secrets.json"
        payload = {
            "secrets": {
                task.task_id: {"access_key": task.access_key, "secret_key": task.secret_key}
                for task in tasks
                if task.access_key or task.secret_key
            }
        }
        self._atomic_write_to(secrets_file, payload)
        try:
            secrets_file.chmod(0o600)
        except OSError:
            pass

    def load_secrets(self, tasks: Iterable[TaskConfig]) -> list[TaskConfig]:
        secrets_file = self.data_dir / "secrets.json"
        if not secrets_file.exists():
            return list(tasks)
        with secrets_file.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        secrets = payload.get("secrets", {})
        updated = []
        for task in tasks:
            item = secrets.get(task.task_id, {})
            task.access_key = item.get("access_key", "")
            task.secret_key = item.get("secret_key", "")
            updated.append(task)
        return updated

    def _atomic_write(self, payload: dict) -> None:
        self._atomic_write_to(self.tasks_file, payload)

    def _atomic_write_to(self, target: Path, payload: dict) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="tasks-", suffix=".tmp", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise
