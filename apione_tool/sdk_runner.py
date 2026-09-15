from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import java_env


class SdkRunner:
    def __init__(self, bridge_dir: Path) -> None:
        self.bridge_dir = bridge_dir

    def call(self, task: Any, body: dict[str, Any]) -> dict[str, Any]:
        env = os.environ.copy()
        env["APIONE_AK"] = task.access_key
        env["APIONE_SK"] = task.secret_key
        jar_path = Path(task.api.jar_path or "apione-http-client-1.0.3-RELEASE.jar")
        if not jar_path.is_absolute():
            jar_path = self.bridge_dir / jar_path
        jar_path = jar_path.resolve()
        if not jar_path.is_file():
            raise RuntimeError(f"找不到 APIOne SDK JAR：{jar_path}")
        classpath = os.pathsep.join((str(self.bridge_dir), str(jar_path)))
        # 桥接字节码按 JDK 17 编译且 JDK 17 起必须 add-exports；强制重检避免沿用
        # 旧的否定缓存导致漏加 add-exports 而运行失败。
        jvm_args = java_env.jvm_extra_args_for_path(task.api.java_path or "java")
        command = [
            task.api.java_path or "java",
            *jvm_args,
            "-cp",
            classpath,
            "ApioneBatchSdkBridge",
            task.api.api_name,
            task.api.region,
            task.api.request_url,
            task.api.method or "POST",
            task.api.media_type or "application/json",
            task.api.path or "",
            json.dumps(task.api.headers, ensure_ascii=False, separators=(",", ":")),
            json.dumps(task.api.query_params, ensure_ascii=False, separators=(",", ":")),
            json.dumps(body, ensure_ascii=False, separators=(",", ":")),
        ]
        started = time.time()
        # 不使用 text=True 的严格 UTF-8 解码：Windows 下 java 默认代码页可能非 UTF-8，
        # 严格解码会抛 UnicodeDecodeError。先按 UTF-8 解码，失败再以 replace 兜底，
        # 避免一次乱码输出直接崩掉整个批量任务。
        completed = subprocess.run(command, env=env, capture_output=True, timeout=120)
        stdout = self._decode(completed.stdout)
        stderr = self._decode(completed.stderr)
        elapsed_ms = round((time.time() - started) * 1000)
        # 非零退出必须判为失败：JDK 17 要求 add-exports，缺参会抛 IllegalAccessError，
        # 此时只要 stdout 末行是旧 dict 也不应被当成功。先校验 returncode，再解析 JSON。
        if completed.returncode != 0:
            raise RuntimeError(
                f"SDK 调用失败；退出码={completed.returncode}；stderr={stderr.strip()}"
            )
        response = self._extract_json(stdout)
        if response is None:
            raise RuntimeError(
                f"SDK 调用未返回 JSON；退出码={completed.returncode}；stderr={stderr.strip()}"
            )
        return {"elapsed_ms": elapsed_ms, "response": response, "stdout": stdout.strip(), "stderr": stderr.strip()}

    @staticmethod
    def _decode(raw: bytes | None) -> str:
        if not raw:
            return ""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _extract_json(stdout: str) -> dict[str, Any] | None:
        for line in reversed(stdout.splitlines()):
            try:
                value = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        return None
