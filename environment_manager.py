#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行环境检查与便携 Python runtime 安装。"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

import java_env
from updater import detect_platform


ProgressCallback = Callable[[str, int, Optional[int], str], None]
MIN_PYTHON = (3, 9)


@dataclass(frozen=True)
class EnvironmentItem:
    name: str
    ok: bool
    detail: str
    repairable: bool = False


@dataclass(frozen=True)
class EnvironmentReport:
    platform_key: str
    items: tuple[EnvironmentItem, ...]
    java_info: java_env.JavaInfo
    java_guide: Optional[java_env.InstallGuide]

    @property
    def supported(self) -> bool:
        return all(item.ok for item in self.items)

    @property
    def can_install_runtime(self) -> bool:
        return any(item.repairable for item in self.items)


def bundled_python_path(app_root: Path, platform_key: Optional[str] = None) -> Path:
    """返回发行目录内应使用的便携 Python 路径。"""
    key = platform_key or _platform_key()
    if key == "windows-x64":
        return app_root / "runtime" / key / "python.exe"
    return app_root / "runtime" / key / "bin" / "python3"


def _platform_key() -> str:
    try:
        return detect_platform()
    except Exception:
        machine = platform.machine().lower()
        if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
            return "macos-arm64"
        if sys.platform == "darwin":
            return "macos-x64"
        if sys.platform.startswith("win"):
            return "windows-x64"
        return "linux-x64"


def check_environment(app_root: Path, java_path: str = "java") -> EnvironmentReport:
    """检查当前发行目录和本机执行环境。"""
    platform_key = _platform_key()
    items: list[EnvironmentItem] = []
    runtime_path = bundled_python_path(app_root, platform_key)
    if runtime_path.is_file() and (os.name == "nt" or os.access(runtime_path, os.X_OK)):
        python_detail = f"便携 Python：{runtime_path.relative_to(app_root)}"
        python_ok = True
        python_repairable = False
    elif sys.version_info >= MIN_PYTHON:
        python_detail = f"当前 Python {sys.version_info.major}.{sys.version_info.minor} 可运行，但未找到便携 runtime"
        python_ok = True
        python_repairable = True
    else:
        python_detail = f"Python 版本过低：当前 {sys.version_info.major}.{sys.version_info.minor}，需要 {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+"
        python_ok = False
        python_repairable = True
    items.append(EnvironmentItem("Python 运行时", python_ok, python_detail, python_repairable))

    java_info = java_env.detect_java(java_path, use_cache=False)
    java_guide = java_env.get_install_guide(reason=java_info.error) if not java_info.meets_requirement else None
    java_detail = (
        f"Java {java_info.major or '未知'}：{java_info.java_path}"
        if java_info.meets_requirement
        else (java_info.error or "未找到可用 Java")
    )
    items.append(EnvironmentItem("Java 运行环境", java_info.meets_requirement, java_detail, False))

    jar_path = app_root / "apione-http-client-1.0.3-RELEASE.jar"
    items.append(EnvironmentItem("APIOne SDK JAR", jar_path.is_file(), str(jar_path), False))
    bridge_ok = all((app_root / name).is_file() for name in ("ApioneBatchSdkBridge.class", "ApioneBatchSdkBridge$1.class", "ApioneBatchSdkBridge$2.class"))
    items.append(EnvironmentItem("Java Bridge", bridge_ok, "Bridge 字节码完整" if bridge_ok else "缺少 Bridge 字节码", False))

    update_dir = app_root / "update"
    try:
        update_dir.mkdir(parents=True, exist_ok=True)
        probe = update_dir / ".environment-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError as exc:
        writable = False
        write_detail = f"程序目录不可写：{exc}"
    else:
        write_detail = "程序目录可写，可安装环境包"
    items.append(EnvironmentItem("安装目录权限", writable, write_detail, False))
    return EnvironmentReport(platform_key, tuple(items), java_info, java_guide)


def _load_remote_manifest(manifest_url: str) -> dict:
    parsed = urllib.parse.urlparse(manifest_url)
    if parsed.scheme not in {"https", "http"}:
        raise RuntimeError("远程更新清单必须使用 HTTP(S) 地址")
    request = urllib.request.Request(manifest_url, headers={"Accept": "application/json", "User-Agent": "APIOne-Batch-Environment/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(4 * 1024 * 1024 + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"无法获取远程环境清单：{exc}") from exc
    if len(body) > 4 * 1024 * 1024:
        raise RuntimeError("远程环境清单超过 4 MiB")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"远程环境清单不是有效 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("远程环境清单顶层必须是对象")
    return value


def _safe_extract(archive: Path, destination: Path, progress: Optional[ProgressCallback]) -> None:
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        total = sum(max(info.file_size, 0) for info in members) or 1
        completed = 0
        for info in members:
            normalized = info.filename.replace("\\", "/")
            relative = PurePosixPath(normalized)
            if not normalized or relative.is_absolute() or ".." in relative.parts or any(part in {"", "."} for part in relative.parts):
                raise RuntimeError(f"环境包包含不安全路径：{info.filename!r}")
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type == stat.S_IFLNK:
                raise RuntimeError(f"环境包包含符号链接：{info.filename!r}")
            target = destination.joinpath(*relative.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                target.chmod(mode)
            completed += max(info.file_size, 0)
            if progress:
                progress("extract", completed, total, f"解压 {info.filename}")


def install_environment_package(
    manifest_url: str,
    app_root: Path,
    platform_key: str,
    progress: Optional[ProgressCallback] = None,
) -> Path:
    """下载并安装指定平台的便携 runtime 环境包。"""
    manifest = _load_remote_manifest(manifest_url)
    packages = manifest.get("environment_packages")
    if not isinstance(packages, dict) or not isinstance(packages.get(platform_key), dict):
        raise RuntimeError(f"远程清单没有 {platform_key} 环境包")
    package = packages[platform_key]
    url = package.get("url")
    expected = str(package.get("sha256", "")).lower()
    if not isinstance(url, str) or not url.strip() or len(expected) != 64:
        raise RuntimeError("环境包清单缺少有效 url 或 SHA-256")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"https", "http"}:
        raise RuntimeError("环境包地址必须使用 HTTP(S)")

    update_dir = app_root / "update"
    update_dir.mkdir(parents=True, exist_ok=True)
    archive = update_dir / f"environment-{platform_key}.zip"
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "APIOne-Batch-Environment/1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            length = response.headers.get("Content-Length")
            total = int(length) if length and length.isdigit() else None
            completed = 0
            if progress:
                progress("download", 0, total, "开始下载环境包")
            with archive.with_suffix(".zip.part").open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    completed += len(chunk)
                    if progress:
                        progress("download", completed, total, "正在下载环境包")
        actual = digest.hexdigest()
        if actual != expected:
            raise RuntimeError(f"环境包 SHA-256 校验失败：期望 {expected}，实际 {actual}")
        os.replace(archive.with_suffix(".zip.part"), archive)
        with tempfile.TemporaryDirectory(prefix="environment-", dir=update_dir) as temp_dir:
            extracted = Path(temp_dir) / "extracted"
            extracted.mkdir()
            _safe_extract(archive, extracted, progress)
            children = [child for child in extracted.iterdir() if child.name != "__MACOSX"]
            payload = children[0] if len(children) == 1 and children[0].is_dir() else extracted
            runtime = payload / "runtime"
            if not runtime.is_dir():
                raise RuntimeError("环境包中缺少 runtime 目录")
            shutil.copytree(runtime, app_root / "runtime", dirs_exist_ok=True)
        if progress:
            progress("complete", 1, 1, "环境包安装完成")
        return app_root / "runtime"
    finally:
        archive.with_suffix(".zip.part").unlink(missing_ok=True)
        archive.unlink(missing_ok=True)

