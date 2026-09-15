from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ParameterConfig:
    name: str
    value_type: str = "string"
    mode: str = "fixed"
    value: Any = ""
    values: list[Any] = field(default_factory=list)
    min_value: Any = None
    max_value: Any = None

    def generate(self) -> Any:
        if not self.name.strip():
            raise ValueError("请求参数名不能为空")
        if self.mode == "fixed":
            return self._coerce(self.value)
        if self.mode == "choice":
            if not self.values:
                raise ValueError(f"参数 {self.name} 的候选值为空")
            return self._coerce(random.choice(self.values))
        if self.mode == "random_range":
            if self.min_value is None or self.max_value is None:
                raise ValueError(f"参数 {self.name} 缺少随机范围")
            if self.value_type == "integer":
                return random.randint(int(self.min_value), int(self.max_value))
            if self.value_type == "number":
                return random.uniform(float(self.min_value), float(self.max_value))
            raise ValueError(f"参数 {self.name} 只有数字类型支持范围随机")
        raise ValueError(f"参数 {self.name} 的生成模式不支持：{self.mode}")

    def _coerce(self, value: Any) -> Any:
        if self.value_type == "integer":
            return int(value)
        if self.value_type == "number":
            return float(value)
        if self.value_type == "boolean":
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in {"true", "1", "yes", "是"}:
                return True
            if text in {"false", "0", "no", "否"}:
                return False
            raise ValueError(f"参数 {self.name} 的布尔值无效：{value}")
        if self.value_type == "null":
            return None
        return value


@dataclass
class ApiConfig:
    request_url: str
    api_name: str
    region: str = "INTER"
    method: str = "POST"
    media_type: str = "application/json"
    path: str = ""
    jar_path: str = ""
    java_path: str = "java"
    headers: dict[str, str] = field(default_factory=dict)
    query_params: dict[str, str] = field(default_factory=dict)


@dataclass
class ScheduleConfig:
    total_count: int = 1
    start_at: str = ""
    end_at: str = ""
    frequency_mode: str = "fixed"
    fixed_interval_seconds: float = 5.0
    random_min_seconds: float = 3.0
    random_max_seconds: float = 10.0
    retry_count: int = 0

    def validate(self) -> None:
        if self.total_count < 1:
            raise ValueError("调用总次数必须大于 0")
        if self.retry_count < 0:
            raise ValueError("重试次数不能为负数")
        if self.fixed_interval_seconds < 0:
            raise ValueError("固定间隔不能为负数")
        if self.random_min_seconds < 0 or self.random_max_seconds < 0:
            raise ValueError("随机间隔不能为负数")
        if self.random_min_seconds > self.random_max_seconds:
            raise ValueError("随机最小间隔不能大于最大间隔")
        if self.frequency_mode not in {"fixed", "random"}:
            raise ValueError("频率模式必须是 fixed 或 random")
        for label, value in (("开始时间", self.start_at), ("结束时间", self.end_at)):
            if value:
                try:
                    datetime.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError(f"{label}格式无效，应为 YYYY-MM-DD HH:MM:SS 或 ISO 8601") from exc
        if self.start_at and self.end_at:
            start = datetime.fromisoformat(self.start_at)
            end = datetime.fromisoformat(self.end_at)
            if (start.tzinfo is None) != (end.tzinfo is None):
                raise ValueError("开始时间和结束时间必须同时使用时区或同时不使用时区")
            if start >= end:
                raise ValueError("结束时间必须晚于开始时间")


@dataclass
class TaskConfig:
    task_id: str
    task_name: str
    enabled: bool = True
    api: ApiConfig = field(default_factory=lambda: ApiConfig("", ""))
    parameters: list[ParameterConfig] = field(default_factory=list)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    access_key: str = ""
    secret_key: str = ""
    created_at: str = ""
    updated_at: str = ""

    def validate(self) -> None:
        """校验任务配置，尽量在启动线程前发现输入错误。"""
        if not self.task_name.strip():
            raise ValueError("任务名称不能为空")
        if not self.api.request_url.strip():
            raise ValueError("请求地址不能为空")
        if not self.api.api_name.strip():
            raise ValueError("API Name 不能为空")
        if not self.access_key.strip() or not self.secret_key.strip():
            raise ValueError("AK/SK 不能为空")
        if not self.parameters:
            raise ValueError("至少配置一个请求参数")
        names = [parameter.name.strip() for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("请求参数名不能重复")
        for parameter in self.parameters:
            parameter.generate()  # 同时校验类型、范围和候选值
        self.schedule.validate()

    def request_body(self) -> dict[str, Any]:
        names = [parameter.name.strip() for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("请求参数名不能重复")
        return {name: parameter.generate() for name, parameter in zip(names, self.parameters)}

    def to_dict(self, include_secrets: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_secrets:
            data.pop("access_key", None)
            data.pop("secret_key", None)
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "TaskConfig":
        api = ApiConfig(**data.get("api", {}))
        schedule = ScheduleConfig(**data.get("schedule", {}))
        parameters = [ParameterConfig(**item) for item in data.get("parameters", [])]
        known = {"task_id", "task_name", "enabled", "access_key", "secret_key", "created_at", "updated_at"}
        values = {key: data.get(key, "") for key in known}
        values["enabled"] = bool(data.get("enabled", True))
        values["api"] = api
        values["schedule"] = schedule
        values["parameters"] = parameters
        return TaskConfig(**values)
