from __future__ import annotations

import copy
import json
import random
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .logging_service import TaskLogger
from .models import TaskConfig
from .sdk_runner import SdkRunner


class ApiResponseError(RuntimeError):
    """APIOne returned a JSON response whose application status is unsuccessful."""

    def __init__(self, response: object) -> None:
        self.response = response
        if isinstance(response, dict):
            message = response.get("message") or response.get("code") or "未知接口错误"
            request_id = response.get("requestId")
            suffix = f"，requestId={request_id}" if request_id else ""
            text = f"APIOne 接口返回失败：{message}{suffix}"
        else:
            text = "APIOne 接口返回失败"
        super().__init__(text)


class TaskScheduler:
    def __init__(self, root: Path, runner: SdkRunner, on_event: Callable[[str, str], None] | None = None) -> None:
        self.root = root
        self.runner = runner
        self.on_event = on_event or (lambda _task_id, _message: None)
        self.stop_flags: dict[str, threading.Event] = {}
        self.threads: dict[str, threading.Thread] = {}
        self.lock = threading.Lock()

    def execute_once(self, task: TaskConfig) -> None:
        self._start(task, once=True)

    def execute_batch(self, task: TaskConfig) -> None:
        self._start(task, once=False)

    def stop(self, task_id: str) -> None:
        with self.lock:
            flag = self.stop_flags.get(task_id)
        if flag:
            flag.set()
            self._emit(task_id, "已请求停止，当前请求结束后停止后续任务")

    def is_running(self, task_id: str) -> bool:
        with self.lock:
            thread = self.threads.get(task_id)
        return bool(thread and thread.is_alive())

    def _start(self, task: TaskConfig, once: bool) -> None:
        task.validate()
        task = copy.deepcopy(task)
        flag = threading.Event()
        with self.lock:
            thread = self.threads.get(task.task_id)
            if thread and thread.is_alive():
                raise RuntimeError("该任务已经在运行")
            self.stop_flags[task.task_id] = flag
            thread = threading.Thread(target=self._run, args=(task, flag, once), daemon=True)
            self.threads[task.task_id] = thread
        thread.start()

    def _run(self, task: TaskConfig, stop_flag: threading.Event, once: bool) -> None:
        logger = TaskLogger(self.root / "logs", task.task_id)
        result_path = self.root / "results" / f"{task.task_id}.jsonl"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        count = 1 if once else task.schedule.total_count
        success_count = 0
        failure_count = 0
        logger.event("task_started", task_name=task.task_name, once=once, total_count=count)
        self._emit(task.task_id, f"任务开始：{task.task_name}，计划调用 {count} 次")
        try:
            if not once:
                self._wait_for_window(task, stop_flag, logger)
            if stop_flag.is_set():
                logger.event("task_finished", stopped=True, success_count=success_count, failure_count=failure_count)
                self._emit(task.task_id, f"任务已停止：成功 {success_count} 次，失败 {failure_count} 次")
                return
            with result_path.open("a", encoding="utf-8") as result_file:
                for sequence in range(1, count + 1):
                    if stop_flag.is_set() or self._past_end(task):
                        break
                    body = task.request_body()
                    started = datetime.now().astimezone().isoformat()
                    try:
                        result = self._call_with_retry(task, body, sequence, logger, stop_flag)
                        success_count += 1
                        record = {"timestamp": started, "task_id": task.task_id, "sequence": sequence, "request": body, **result}
                        self._emit(task.task_id, f"第 {sequence}/{count} 次成功，参数={body}")
                    except Exception as exc:  # noqa: BLE001
                        failure_count += 1
                        logger.event("call_error", sequence=sequence, request=body, error=str(exc))
                        record = {"timestamp": started, "task_id": task.task_id, "sequence": sequence, "request": body, "error": str(exc)}
                        if isinstance(exc, ApiResponseError):
                            record["response"] = exc.response
                        self._emit(task.task_id, f"第 {sequence}/{count} 次失败：{exc}")
                    result_file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                    result_file.flush()
                    if sequence < count:
                        self._sleep(self._next_delay(task), stop_flag)
            stopped = stop_flag.is_set()
            logger.event("task_finished", stopped=stopped, success_count=success_count, failure_count=failure_count)
            if stopped:
                self._emit(task.task_id, f"任务已停止：成功 {success_count} 次，失败 {failure_count} 次")
            else:
                self._emit(task.task_id, f"任务已完成：成功 {success_count} 次，失败 {failure_count} 次")
        except Exception as exc:  # noqa: BLE001
            logger.event("task_error", error=str(exc))
            self._emit(task.task_id, f"任务异常：{exc}")
        finally:
            logger.close()
            with self.lock:
                self.stop_flags.pop(task.task_id, None)
                self.threads.pop(task.task_id, None)

    def _call_with_retry(self, task: TaskConfig, body: dict, sequence: int, logger: TaskLogger, stop_flag: threading.Event) -> dict:
        attempts = task.schedule.retry_count + 1
        for attempt in range(1, attempts + 1):
            if stop_flag.is_set():
                raise RuntimeError("任务已停止")
            try:
                result = self.runner.call(task, body)
                response = result["response"]
                logger.event("call_result", sequence=sequence, attempt=attempt, request=body, http_response=response, elapsed_ms=result["elapsed_ms"], success=response.get("success") if isinstance(response, dict) else None)
                if isinstance(response, dict) and response.get("success") is False:
                    raise ApiResponseError(response)
                return {"attempt": attempt, "elapsed_ms": result["elapsed_ms"], "response": result["response"]}
            except Exception as exc:  # noqa: BLE001
                logger.event("call_attempt_error", sequence=sequence, attempt=attempt, request=body, error=str(exc))
                if attempt == attempts:
                    raise
                self._sleep(1.0, stop_flag)
        raise RuntimeError("重试流程异常")

    def _wait_for_window(self, task: TaskConfig, stop_flag: threading.Event, logger: TaskLogger) -> None:
        if not task.schedule.start_at:
            return
        try:
            start = datetime.fromisoformat(task.schedule.start_at)
        except ValueError:
            logger.event("schedule_window_invalid", start_at=task.schedule.start_at)
            self._emit(task.task_id, f"开始时间格式无效，跳过等待：{task.schedule.start_at}")
            return
        now = datetime.now(start.tzinfo) if start.tzinfo else datetime.now()
        while now < start and not stop_flag.is_set():
            self._emit(task.task_id, f"等待开始时间：{start:%Y-%m-%d %H:%M:%S}")
            time.sleep(min(1.0, max(0.1, (start - now).total_seconds())))
            now = datetime.now(start.tzinfo) if start.tzinfo else datetime.now()
        logger.event("schedule_window_entered")

    @staticmethod
    def _past_end(task: TaskConfig) -> bool:
        if not task.schedule.end_at:
            return False
        try:
            end = datetime.fromisoformat(task.schedule.end_at)
        except ValueError:
            return False
        now = datetime.now(end.tzinfo) if end.tzinfo else datetime.now()
        return now > end

    @staticmethod
    def _next_delay(task: TaskConfig) -> float:
        if task.schedule.frequency_mode == "random":
            return random.uniform(task.schedule.random_min_seconds, task.schedule.random_max_seconds)
        return task.schedule.fixed_interval_seconds

    @staticmethod
    def _sleep(seconds: float, stop_flag: threading.Event) -> None:
        end = time.time() + seconds
        while time.time() < end and not stop_flag.is_set():
            time.sleep(min(0.2, end - time.time()))

    def _emit(self, task_id: str, message: str) -> None:
        self.on_event(task_id, message)
