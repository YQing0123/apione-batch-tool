#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""APIOne 批量工具的独立更新模块（仅使用 Python 标准库）。"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Optional, Tuple, Union

APP_ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = APP_ROOT / "manifest.json"
PRESERVED_NAMES = frozenset({"data", "logs", "results", "update"})
_SEMVER_RE = re.compile(
    r"^[vV]?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ProgressCallback = Callable[[str, int, Optional[int]], None]
ManifestLocation = Union[str, os.PathLike[str]]


class UpdateError(RuntimeError):
    """更新检查或安装失败。异常消息可直接展示给用户。"""


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    latest_version: str
    platform_key: str
    available: bool
    download_url: str
    sha256: str
    release_notes: Tuple[str, ...]
    min_supported_version: Optional[str]
    mandatory: bool
    manifest_url: str


@dataclass(frozen=True)
class InstallResult:
    previous_version: str
    installed_version: str
    platform_key: str
    archive_path: Path
    backup_path: Path


def _semantic_version(value: str) -> Tuple[int, int, int, Tuple[Tuple[int, Union[int, str]], ...]]:
    if not isinstance(value, str):
        raise UpdateError(f"版本号必须是字符串，实际为 {type(value).__name__}")
    match = _SEMVER_RE.fullmatch(value.strip())
    if not match:
        raise UpdateError(f"无效的语义版本号：{value!r}")
    major, minor, patch = (int(match.group(index)) for index in range(1, 4))
    prerelease = match.group(4)
    if prerelease is None:
        identifiers: Tuple[Tuple[int, Union[int, str]], ...] = ((2, ""),)
    else:
        parsed = []
        for item in prerelease.split("."):
            if item.isdigit():
                if len(item) > 1 and item.startswith("0"):
                    raise UpdateError(f"无效的语义版本号（预发布数字含前导零）：{value!r}")
                parsed.append((0, int(item)))
            else:
                parsed.append((1, item))
        identifiers = tuple(parsed)
    return major, minor, patch, identifiers


def compare_versions(left: str, right: str) -> int:
    """按 SemVer 2.0.0 比较版本；返回 -1、0 或 1。"""
    left_value = _semantic_version(left)
    right_value = _semantic_version(right)
    left_core, right_core = left_value[:3], right_value[:3]
    if left_core != right_core:
        return -1 if left_core < right_core else 1
    left_pre, right_pre = left_value[3], right_value[3]
    if left_pre == right_pre:
        return 0
    if left_pre == ((2, ""),):
        return 1
    if right_pre == ((2, ""),):
        return -1
    for left_item, right_item in zip(left_pre, right_pre):
        if left_item == right_item:
            continue
        if left_item[0] != right_item[0]:
            return -1 if left_item[0] < right_item[0] else 1
        return -1 if left_item[1] < right_item[1] else 1
    return -1 if len(left_pre) < len(right_pre) else 1


def detect_platform() -> str:
    """返回更新清单使用的平台键。"""
    machine = platform.machine().lower().replace("_", "-")
    if sys.platform == "darwin":
        if machine in {"arm64", "aarch64"}:
            return "macos-arm64"
        if machine in {"x86-64", "amd64", "x64"}:
            return "macos-x64"
    elif sys.platform.startswith("win"):
        if machine in {"x86-64", "amd64", "x64"}:
            return "windows-x64"
    raise UpdateError(f"当前平台暂不支持自动更新：{sys.platform}/{platform.machine()}")


def _read_json_file(path: Path, description: str) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UpdateError(f"未找到{description}：{path}") from exc
    except OSError as exc:
        raise UpdateError(f"无法读取{description} {path}：{exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"{description}不是有效的 UTF-8 JSON：{path}（{exc}）") from exc
    if not isinstance(value, dict):
        raise UpdateError(f"{description}顶层必须是 JSON 对象：{path}")
    return value


def _local_path(location: ManifestLocation) -> Optional[Path]:
    if isinstance(location, os.PathLike):
        return Path(location).expanduser().resolve()
    if re.match(r"^[A-Za-z]:[\\/]", location):
        return Path(location).expanduser().resolve()
    parsed = urllib.parse.urlparse(location)
    if parsed.scheme == "":
        return Path(location).expanduser().resolve()
    if parsed.scheme == "file":
        path = urllib.request.url2pathname(parsed.path)
        if parsed.netloc and parsed.netloc not in {"", "localhost"}:
            path = f"//{parsed.netloc}{path}"
        return Path(path).resolve()
    return None


def _load_manifest(location: ManifestLocation, timeout: float) -> Tuple[Mapping[str, object], str]:
    local_path = _local_path(location)
    if local_path is not None:
        return _read_json_file(local_path, "更新清单"), str(local_path)

    text_location = str(location)
    parsed = urllib.parse.urlparse(text_location)
    if parsed.scheme not in {"http", "https"}:
        raise UpdateError(f"不支持的更新清单地址协议：{parsed.scheme or '空'}")
    request = urllib.request.Request(
        text_location,
        headers={"Accept": "application/json", "User-Agent": "APIOne-Batch-Updater/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(4 * 1024 * 1024 + 1)
            final_url = response.geturl()
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(f"无法获取更新清单 {text_location}：{exc}") from exc
    if len(body) > 4 * 1024 * 1024:
        raise UpdateError("更新清单超过 4 MiB，已拒绝处理")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"远程更新清单不是有效的 UTF-8 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise UpdateError("远程更新清单顶层必须是 JSON 对象")
    return value, final_url


def _resolve_package_url(url: str, manifest_location: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise UpdateError("当前平台的软件包缺少 url")
    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in {"http", "https", "file"}:
        return url
    if parsed.scheme:
        raise UpdateError(f"软件包使用了不支持的地址协议：{parsed.scheme}")
    manifest_path = _local_path(manifest_location)
    if manifest_path is not None:
        return str((manifest_path.parent / url).resolve())
    return urllib.parse.urljoin(manifest_location, url)


def load_current_version(app_root: Optional[os.PathLike[str]] = None) -> str:
    """读取本地当前版本号，供 GUI 展示。读取失败返回 ``unknown``。

    该函数不抛异常，适合在 UI 初始化时安全调用；版本合法性由
    :func:`check_for_update` 在真正检查更新时严格校验。
    """
    root = Path(app_root).resolve() if app_root is not None else APP_ROOT
    try:
        data = _read_json_file(root / "version.json", "本地版本文件")
    except UpdateError:
        return "unknown"
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        return "unknown"
    return version.strip()


def check_for_update(
    manifest_url: Optional[ManifestLocation] = None,
    *,
    app_root: Optional[os.PathLike[str]] = None,
    platform_key: Optional[str] = None,
    timeout: float = 15.0,
) -> UpdateInfo:
    """读取本地版本和更新清单，返回适用于当前平台的更新信息。"""
    root = Path(app_root).resolve() if app_root is not None else APP_ROOT
    current_data = _read_json_file(root / "version.json", "本地版本文件")
    current_version = current_data.get("version")
    if not isinstance(current_version, str):
        raise UpdateError("本地 version.json 缺少字符串字段 version")
    _semantic_version(current_version)

    location: ManifestLocation = manifest_url if manifest_url is not None else root / "manifest.json"
    if manifest_url is None and not Path(location).exists():
        # 本地开发/单机发行目录未配置远程清单时，返回“无更新源”而非错误弹窗。
        return UpdateInfo(
            current_version=current_version,
            latest_version=current_version,
            platform_key=platform_key or detect_platform(),
            available=False,
            download_url="",
            sha256="",
            release_notes=("当前发行目录未配置 manifest.json。",),
            min_supported_version=None,
            mandatory=False,
            manifest_url=str(location),
        )
    manifest, resolved_manifest_location = _load_manifest(location, timeout)
    latest_version = manifest.get("version")
    if not isinstance(latest_version, str):
        raise UpdateError("更新清单缺少字符串字段 version")
    _semantic_version(latest_version)

    selected_platform = platform_key or detect_platform()
    packages = manifest.get("packages")
    if not isinstance(packages, dict):
        raise UpdateError("更新清单缺少对象字段 packages")
    package = packages.get(selected_platform)
    if not isinstance(package, dict):
        raise UpdateError(f"更新清单没有当前平台的软件包：{selected_platform}")
    checksum_value = package.get("sha256", "")
    if not isinstance(checksum_value, str):
        raise UpdateError(f"{selected_platform} 软件包的 sha256 必须是字符串")
    checksum = checksum_value.strip()
    available = compare_versions(latest_version, current_version) > 0
    if available and not _SHA256_RE.fullmatch(checksum):
        raise UpdateError(f"{selected_platform} 新版本软件包缺少有效的 SHA-256")
    package_url = _resolve_package_url(package.get("url"), resolved_manifest_location)

    # release_notes 是可选字段：缺省或显式 null 都按“无发行说明”处理。
    # 注意缺省值不能写成 ()：空元组既不匹配 str 也不匹配 list，会掉进 else 分支，
    # 导致任何省略 release_notes 的清单都被判为非法，报错信息还会误导成“类型不对”。
    notes_value = manifest.get("release_notes")
    if notes_value is None:
        notes = ()
    elif isinstance(notes_value, str):
        notes = (notes_value,)
    elif isinstance(notes_value, list) and all(isinstance(item, str) for item in notes_value):
        notes = tuple(notes_value)
    else:
        raise UpdateError("更新清单字段 release_notes 必须是字符串或字符串数组")

    min_supported = manifest.get("min_supported_version")
    if min_supported is not None and not isinstance(min_supported, str):
        raise UpdateError("更新清单字段 min_supported_version 必须是字符串")
    if min_supported is not None:
        _semantic_version(min_supported)
    return UpdateInfo(
        current_version=current_version,
        latest_version=latest_version,
        platform_key=selected_platform,
        available=available,
        download_url=package_url,
        sha256=checksum.lower(),
        release_notes=notes,
        min_supported_version=min_supported,
        mandatory=min_supported is not None and compare_versions(current_version, min_supported) < 0,
        manifest_url=resolved_manifest_location,
    )


def _notify(callback: Optional[ProgressCallback], phase: str, completed: int = 0, total: Optional[int] = None) -> None:
    if callback is not None:
        callback(phase, completed, total)


def _download(url: str, destination: Path, timeout: float, callback: Optional[ProgressCallback]) -> str:
    parsed = urllib.parse.urlparse(url)
    local_path = _local_path(url)
    digest = hashlib.sha256()
    completed = 0
    part_path = destination.with_name(destination.name + ".part")
    try:
        if local_path is not None:
            source = local_path.open("rb")
            total = local_path.stat().st_size
        else:
            if parsed.scheme not in {"http", "https"}:
                raise UpdateError(f"不支持的软件包地址协议：{parsed.scheme or '空'}")
            request = urllib.request.Request(url, headers={"User-Agent": "APIOne-Batch-Updater/1"})
            source = urllib.request.urlopen(request, timeout=timeout)
            length = source.headers.get("Content-Length")
            total = int(length) if length and length.isdigit() else None
        _notify(callback, "download", 0, total)
        with source, part_path.open("wb") as output:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                completed += len(chunk)
                _notify(callback, "download", completed, total)
            output.flush()
            os.fsync(output.fileno())
        os.replace(part_path, destination)
    except UpdateError:
        part_path.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        part_path.unlink(missing_ok=True)
        raise UpdateError(f"下载更新包失败：{exc}") from exc
    return digest.hexdigest()


def _safe_extract(archive: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as package:
            for info in package.infolist():
                normalized_name = info.filename.replace("\\", "/")
                relative = PurePosixPath(normalized_name)
                if (
                    not normalized_name
                    or relative.is_absolute()
                    or ".." in relative.parts
                    or any(part in {"", "."} for part in relative.parts)
                    or (relative.parts and re.match(r"^[A-Za-z]:", relative.parts[0]))
                ):
                    raise UpdateError(f"更新包包含不安全的路径：{info.filename!r}")
                file_type = (info.external_attr >> 16) & 0o170000
                if file_type == stat.S_IFLNK:
                    raise UpdateError(f"更新包包含符号链接，已拒绝：{info.filename!r}")
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
    except UpdateError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise UpdateError(f"无法解压更新包：{exc}") from exc


def _payload_root(extracted: Path) -> Path:
    children = [child for child in extracted.iterdir() if child.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return extracted


def _install_payload(payload: Path, root: Path, backup: Path) -> None:
    new_entries = [entry for entry in payload.iterdir() if entry.name not in PRESERVED_NAMES and entry.name != "__MACOSX"]
    if not new_entries:
        raise UpdateError("更新包没有可安装的程序文件")
    current_entries = [entry for entry in root.iterdir() if entry.name not in PRESERVED_NAMES]
    backup_current = backup / "current"
    failed_new = backup / "failed-new"
    backup_current.mkdir(parents=True)
    moved_old = []
    installed_new = []
    try:
        for entry in current_entries:
            destination = backup_current / entry.name
            os.replace(entry, destination)
            moved_old.append((destination, entry))
        for entry in new_entries:
            destination = root / entry.name
            os.replace(entry, destination)
            installed_new.append(destination)
    except OSError as exc:
        rollback_errors = []
        failed_new.mkdir(parents=True, exist_ok=True)
        for installed in reversed(installed_new):
            try:
                if installed.exists() or installed.is_symlink():
                    os.replace(installed, failed_new / installed.name)
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        for stored, original in reversed(moved_old):
            try:
                if stored.exists() or stored.is_symlink():
                    os.replace(stored, original)
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        detail = f"；回滚也遇到错误：{'；'.join(rollback_errors)}" if rollback_errors else "；已恢复原版本"
        raise UpdateError(f"替换程序文件失败：{exc}{detail}") from exc


def download_and_install(
    info: UpdateInfo,
    *,
    app_root: Optional[os.PathLike[str]] = None,
    timeout: float = 60.0,
    progress: Optional[ProgressCallback] = None,
    allow_reinstall: bool = False,
) -> InstallResult:
    """下载、校验并事务式安装更新；保留 data/logs/results/update。"""
    if not isinstance(info, UpdateInfo):
        raise UpdateError("info 必须是 check_for_update 返回的 UpdateInfo")
    if not info.available and not allow_reinstall:
        raise UpdateError(f"没有可安装的新版本（当前 {info.current_version}，清单 {info.latest_version}）")
    if not _SHA256_RE.fullmatch(info.sha256):
        raise UpdateError("更新信息中的 SHA-256 无效")

    root = Path(app_root).resolve() if app_root is not None else APP_ROOT
    if not root.is_dir():
        raise UpdateError(f"工具目录不存在：{root}")
    update_dir = root / "update"
    try:
        update_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UpdateError(f"无法创建更新目录 {update_dir}：{exc}") from exc

    safe_version = re.sub(r"[^0-9A-Za-z._-]", "_", info.latest_version)
    archive = update_dir / f"APIOneBatchTool-{safe_version}-{info.platform_key}.zip"
    _notify(progress, "prepare")
    actual_checksum = _download(info.download_url, archive, timeout, progress)
    if actual_checksum.lower() != info.sha256.lower():
        archive.unlink(missing_ok=True)
        raise UpdateError(f"更新包 SHA-256 校验失败：期望 {info.sha256}，实际 {actual_checksum}")
    _notify(progress, "verify", 1, 1)

    work_dir = Path(tempfile.mkdtemp(prefix="staging-", dir=update_dir))
    extracted = work_dir / "extracted"
    extracted.mkdir()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = update_dir / "backups" / f"{timestamp}-from-{info.current_version}"
    try:
        _notify(progress, "extract")
        _safe_extract(archive, extracted)
        payload = _payload_root(extracted)
        packaged_version = _read_json_file(payload / "version.json", "更新包版本文件").get("version")
        if packaged_version != info.latest_version:
            raise UpdateError(
                f"更新包版本与清单不一致：清单 {info.latest_version}，更新包 {packaged_version!r}"
            )
        backup.mkdir(parents=True, exist_ok=False)
        _notify(progress, "install")
        _install_payload(payload, root, backup)
        _notify(progress, "complete", 1, 1)
    except UpdateError:
        if backup.exists() and not any(backup.iterdir()):
            backup.rmdir()
        raise
    except OSError as exc:
        raise UpdateError(f"准备更新时发生文件系统错误：{exc}") from exc
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return InstallResult(
        previous_version=info.current_version,
        installed_version=info.latest_version,
        platform_key=info.platform_key,
        archive_path=archive,
        backup_path=backup,
    )


__all__ = [
    "APP_ROOT",
    "DEFAULT_MANIFEST",
    "InstallResult",
    "UpdateError",
    "UpdateInfo",
    "check_for_update",
    "compare_versions",
    "detect_platform",
    "download_and_install",
    "load_current_version",
]