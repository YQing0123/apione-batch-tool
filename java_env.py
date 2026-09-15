#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Java 运行环境检测与安装引导（纯 Python 标准库，无第三方依赖）。

APIOne SDK 通过官方 JAR 完成鉴权与 HTTP 请求。发行目录内的桥接字节码
``ApioneBatchSdkBridge.class``（含 ``$1`` / ``$2`` 匿名 ``TypeToken`` 类）是
**按 JDK 17 编译**的（``class version 61.0``），因此运行环境必须至少具备
**JDK 17**；低于 JDK 17 会在运行时抛出 ``UnsupportedClassVersionError``。

该 JAR 同时还使用了 JDK 内部
``com.sun.org.apache.xerces.internal.impl.dv.util`` 包下的工具类，JDK 17 起该
内部类默认不可访问，**必须**附带
``--add-exports=java.xml/com.sun.org.apache.xerces.internal.impl.dv.util=ALL-UNNAMED``。

因此本模块对外提供：

- ``detect_java(java_path)``：检测 java 命令是否存在、版本是否达标（至少 JDK 17）。
- ``parse_major_version(text)``：从 ``java -version`` 输出解析主版本号。
- ``jvm_extra_args(major)`` / ``jvm_extra_args_for_path(java_path)``：按版本给出
  JVM 额外参数（JDK 17+ 需要 add-exports）。
- ``get_install_guide(platform_name, reason)``：生成跨平台、非静默的安装命令与
  官方下载地址。
- ``open_official_page(url)`` / ``run_install_command(guide)``：在用户明确同意后
  打开官方安装页或交互式执行安装命令（Windows 不写脚本文件）。

所有安装命令均为**非静默**安装（不使用 ``--silent`` / ``/quiet`` 等参数），
且 Windows 分支不会把命令写入任何 .bat/.ps1 脚本文件。
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime
import webbrowser
from dataclasses import dataclass
from typing import Optional


# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

# 桥接字节码 ApioneBatchSdkBridge.class 按 JDK 17（class version 61.0）编译，
# 因此运行环境最低要求 JDK 17；低于 17 会触发 UnsupportedClassVersionError。
# 若未来需要支持更低版本，需先用 `javac --release 8` 重新编译该桥接类。
MIN_JAVA_MAJOR = 17

# JDK 17 起必须使用 add-exports 才能访问内部 Base64 工具类；由于桥接本身也要求
# JDK 17，低于 17 的版本本就不可运行，故 add-exports 的触发阈值同样为 17。
ADD_EXPORTS_MIN_MAJOR = 17

ADD_EXPORTS_FLAG = (
    "--add-exports=java.xml/com.sun.org.apache.xerces.internal.impl.dv.util=ALL-UNNAMED"
)

# 执行 java -version 的超时时间（秒）。
_VERSION_TIMEOUT = 30


# 各平台“标准、非静默”的安装命令与官方下载页。
# - macOS：Homebrew（cask 安装会弹出系统安装器，需输入密码，非静默）。
# - Windows：winget（使用 --interactive 弹出官方安装向导，非静默；
#   也可打开 Microsoft OpenJDK 官方下载页手动安装）。
# - Linux：以 Debian/Ubuntu 的 apt 为例（RHEL 系可改用 dnf/yum）。
INSTALL_SPECS: dict[str, dict] = {
    "darwin": {
        "label": "macOS（Homebrew）",
        "commands": ["brew install --cask temurin17"],
        "official_url": "https://adoptium.net/zh-CN/temurin/releases/",
        "note": (
            "Homebrew 会下载并打开系统安装器，期间可能需要输入登录密码，"
            "属于非静默安装。安装完成后重启本工具即可。"
        ),
    },
    "windows": {
        "label": "Windows（winget 或 Microsoft OpenJDK 官方下载）",
        "commands": ["winget install --interactive Microsoft.OpenJDK.17"],
        "official_url": "https://learn.microsoft.com/zh-cn/java/openjdk/download",
        "note": (
            "winget 使用 --interactive 会弹出官方安装向导（非静默）。"
            "也可直接打开 Microsoft OpenJDK 官方下载页手动安装。不会写入任何脚本文件。"
        ),
    },
    "linux": {
        "label": "Linux（以 Debian/Ubuntu 为例）",
        "commands": [
            "sudo apt-get update",
            "sudo apt-get install -y openjdk-17-jdk",
        ],
        "official_url": "https://adoptium.net/zh-CN/temurin/releases/",
        "note": (
            "使用系统包管理器安装 JDK 17；RHEL/CentOS 可改用 "
            "`sudo dnf install -y java-17-openjdk-devel`。安装完成后重启本工具即可。"
        ),
    },
}


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #

@dataclass
class JavaInfo:
    """一次 Java 环境检测的结果。"""

    available: bool
    java_path: str
    version_text: str = ""
    major: Optional[int] = None
    meets_requirement: bool = False
    needs_add_exports: bool = False
    error: str = ""


@dataclass
class InstallGuide:
    """面向用户的不支持原因与安装引导。"""

    platform: str
    label: str
    commands: list[str]
    official_url: Optional[str]
    note: str
    reason: str = ""


# --------------------------------------------------------------------------- #
# 平台识别
# --------------------------------------------------------------------------- #

def get_platform() -> str:
    """返回归一化平台名：darwin / linux / windows。"""
    name = sys.platform
    if name == "darwin":
        return "darwin"
    if name.startswith("win"):
        return "windows"
    return "linux"


# --------------------------------------------------------------------------- #
# 版本解析
# --------------------------------------------------------------------------- #

_VERSION_RE = re.compile(r'version "([^"]+)"')


def parse_major_version(text: str) -> Optional[int]:
    """从 ``java -version`` 输出解析主版本号。

    支持的常见格式：
      - ``java version "1.8.0_361"``            -> 8
      - ``openjdk version "11.0.2" 2019-...``   -> 11
      - ``java version "17.0.1"``               -> 17
      - ``openjdk version "21"``                -> 21

    解析失败时返回 ``None``。
    """
    if not text:
        return None
    match = _VERSION_RE.search(text)
    if not match:
        return None
    raw = match.group(1).strip()
    # JDK 8 及更早使用 "1.8.0_xxx" 形式。
    if raw.startswith("1."):
        tail = raw[2:]
        head = re.split(r"[._]", tail, 1)[0]
    else:
        head = re.split(r"[._]", raw, 1)[0]
    try:
        return int(head)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# 检测
# --------------------------------------------------------------------------- #

# 缓存结构：每条记录附带写入时间戳，便于对“缺失/不合格”结果做有限过期，
# 避免用户安装 Java 后仍长期使用旧的否定缓存。
_cache: dict[str, JavaInfo] = {}
_cache_time: dict[str, float] = {}

# “缺失/不合格”结果的缓存有效期（秒）。需要引导安装的场景下，用户安装完成后
# 再次点击执行仍应重新检测；过期后会触发一次真实 java -version 调用。
_NEGATIVE_CACHE_TTL = 60.0


def _prune_cache(now: float) -> None:
    """清理过期的否定结果，并防止缓存无界增长。"""
    expired = [
        key
        for key, ts in _cache_time.items()
        if now - ts >= _NEGATIVE_CACHE_TTL and not _cache.get(key, JavaInfo(available=False, java_path=key)).meets_requirement
    ]
    for key in expired:
        _cache.pop(key, None)
        _cache_time.pop(key, None)
    if len(_cache) > 16:
        extra = sorted(_cache_time, key=_cache_time.get)[: len(_cache) - 16]
        for key in extra:
            _cache.pop(key, None)
            _cache_time.pop(key, None)


def detect_java(java_path: str = "java", use_cache: bool = True) -> JavaInfo:
    """检测指定 java 命令是否存在、版本是否满足 APIOne SDK 要求（至少 JDK 17）。

    :param java_path: java 可执行文件，可为 ``java``（走 PATH）或绝对路径。
    :param use_cache: 是否复用同一 java_path 的上一次检测结果（默认启用，
                     避免批量调用时反复执行 ``java -version``）。缺失/不合格结果
                     会在 :data:`_NEGATIVE_CACHE_TTL` 秒后过期；传入 ``False``
                     可强制重新检测（例如用户刚完成安装、需要立即重试）。
    :return: :class:`JavaInfo`
    """
    now = datetime.now().timestamp()
    if use_cache and java_path in _cache and java_path in _cache_time:
        if _cache[java_path].meets_requirement or now - _cache_time[java_path] < _NEGATIVE_CACHE_TTL:
            return _cache[java_path]
    _prune_cache(now)

    info = JavaInfo(available=False, java_path=java_path)

    # shutil.which 对 "java" 会在 PATH 中查找；对绝对路径则校验是否存在。
    exe = shutil.which(java_path) or java_path
    try:
        proc = subprocess.run(
            [exe, "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_VERSION_TIMEOUT,
        )
    except FileNotFoundError:
        info.error = f"未找到 Java 命令：{java_path}"
        _cache[java_path], _cache_time[java_path] = info, now
        return info
    except OSError as exc:
        info.error = f"无法执行 Java 命令（{java_path}）：{exc}"
        _cache[java_path], _cache_time[java_path] = info, now
        return info
    except subprocess.TimeoutExpired:
        info.error = "执行 java -version 超时"
        _cache[java_path], _cache_time[java_path] = info, now
        return info

    # 退出码非零（例如 macOS 仅装了无 JRE 的 /usr/bin/java stub，会返回非 0 且无
    # 版本输出）或没有任何版本输出时，视为 Java 环境不可用，引导用户安装。
    version_text = f"{proc.stdout or ''}{proc.stderr or ''}".strip()
    if proc.returncode != 0 or not version_text:
        info.error = (
            "java 命令存在但无法返回有效版本（可能尚未安装 JRE，或运行环境不完整）。"
            "请安装 JDK 17 后重试。"
        )
        _cache[java_path], _cache_time[java_path] = info, now
        return info

    info.available = True
    info.version_text = version_text
    info.major = parse_major_version(version_text)

    if info.major is None:
        # 命令可用但版本无法解析：保守地按 JDK 17+ 处理，并给出提示而非静默通过。
        info.error = "无法解析 Java 版本号，将按 JDK 17+ 方式处理（已附带 add-exports）。"
        info.meets_requirement = True
        info.needs_add_exports = True
    else:
        info.meets_requirement = info.major >= MIN_JAVA_MAJOR
        info.needs_add_exports = info.major >= ADD_EXPORTS_MIN_MAJOR
        if not info.meets_requirement:
            info.error = (
                f"Java 版本过低：当前为 Java {info.major}，"
                f"APIOne SDK 至少需要 Java {MIN_JAVA_MAJOR}（推荐 JDK 17）。"
            )

    _cache[java_path], _cache_time[java_path] = info, now
    return info


def clear_cache() -> None:
    """清空检测结果缓存。用户完成安装、需要立即强制重检时调用。"""
    _cache.clear()
    _cache_time.clear()


# --------------------------------------------------------------------------- #
# JVM 额外参数
# --------------------------------------------------------------------------- #

def jvm_extra_args(major: Optional[int]) -> list[str]:
    """根据 Java 主版本返回需要附带的 JVM 额外参数。

    由于桥接字节码要求 JDK 17，检测阶段已拒绝低于 17 的版本，这里只处理 17+：

    - ``major >= 17``（本工具支持的正式范围）：返回 add-exports 参数（JDK 17 必须）。
    - ``major is None``（版本无法解析）：保守返回 add-exports 参数，避免漏加导致
      ``NoClassDefFoundError`` / ``IllegalAccessError``。
    """
    if major is None or major >= 17:
        return [ADD_EXPORTS_FLAG]
    return []


def jvm_extra_args_for_path(java_path: str = "java") -> list[str]:
    """按检测到的 java 主版本返回 JVM 额外参数（强制重检，避免沿用旧的缓存）。"""
    return jvm_extra_args(detect_java(java_path, use_cache=False).major)


# --------------------------------------------------------------------------- #
# 安装引导
# --------------------------------------------------------------------------- #

def get_install_guide(platform_name: Optional[str] = None, reason: str = "") -> InstallGuide:
    """生成当前（或指定）平台的非静默安装引导。"""
    plat = platform_name or get_platform()
    spec = INSTALL_SPECS.get(plat, INSTALL_SPECS["linux"])
    return InstallGuide(
        platform=plat,
        label=spec["label"],
        commands=list(spec["commands"]),
        official_url=spec.get("official_url"),
        note=spec["note"],
        reason=reason,
    )


def open_official_page(url: Optional[str]) -> bool:
    """在默认浏览器打开官方安装页。成功返回 True。"""
    if not url:
        return False
    return webbrowser.open(url)


def run_install_command(guide: InstallGuide, platform_name: Optional[str] = None) -> None:
    """在用户明确同意后，交互式执行安装命令（非静默）。

    - Windows：直接以交互方式运行 winget（弹出官方安装向导），**不写入任何脚本文件**。
    - macOS / Linux：在终端中执行安装命令（本工具通常由 .command/.sh/.bat
      在终端内启动，安装输出可见）。

    仅执行命令本身，不等待其完成、不捕获其输出。
    """
    plat = platform_name or get_platform()
    if plat == "windows":
        # 不写脚本文件：直接交互式运行 winget，由 winget 自身弹出安装向导。
        cmd = guide.commands[0] if guide.commands else None
        if cmd:
            parts = cmd.split()
            subprocess.Popen(parts)
        return
    # macOS / Linux：拼接为单条 shell 命令执行。
    joined = " && ".join(guide.commands)
    if joined:
        subprocess.Popen(joined, shell=True)


# --------------------------------------------------------------------------- #
# 便捷组合
# --------------------------------------------------------------------------- #

def validate_java(java_path: str = "java") -> tuple[JavaInfo, Optional[InstallGuide]]:
    """检测 Java 环境；不满足时附带安装引导。

    :return: ``(JavaInfo, InstallGuide | None)``
    """
    info = detect_java(java_path)
    guide = None
    if not info.meets_requirement:
        guide = get_install_guide(reason=info.error)
    return info, guide


if __name__ == "__main__":
    result = detect_java()
    print(f"java_path   : {result.java_path}")
    print(f"available   : {result.available}")
    print(f"major       : {result.major}")
    print(f"meets       : {result.meets_requirement}")
    print(f"add-exports : {result.needs_add_exports}")
    if result.error:
        print(f"error       : {result.error}")
        print("install     :")
        g = get_install_guide()
        for c in g.commands:
            print(f"  $ {c}")
        if g.official_url:
            print(f"  official: {g.official_url}")
