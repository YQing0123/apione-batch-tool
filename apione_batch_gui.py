#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""APIOne 多任务图形化批量调用工具。"""

from __future__ import annotations

import json
import os
import queue
import threading
import tkinter as tk
import uuid

from updater import UpdateError, check_for_update, download_and_install, load_current_version
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Optional

from apione_tool.models import ApiConfig, ParameterConfig, ScheduleConfig, TaskConfig
from apione_tool.scheduler import TaskScheduler
from apione_tool.sdk_runner import SdkRunner
from apione_tool.storage import TaskStorage

from environment_manager import EnvironmentReport, check_environment, install_environment_package
import java_env
from java_env import InstallGuide, JavaInfo


ROOT = Path(__file__).resolve().parent
DEFAULT_URL = "https://data-elem.digitaljx.com/apione/"
DEFAULT_API = "RS36000000000012026091420374688"
DEFAULT_JAR = "apione-http-client-1.0.3-RELEASE.jar"
REMOTE_MANIFEST_URL = "https://raw.githubusercontent.com/YQing0123/apione-batch-tool/main/manifest.json"


def _portable_jar_path(value: str) -> str:
    """Persist SDK JAR locations relative to the portable application directory."""
    text = (value or "").strip().replace("\\", "/")
    if not text:
        return DEFAULT_JAR
    path = Path(text)
    if not path.is_absolute():
        return text
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        bundled = ROOT / path.name
        return path.name if bundled.exists() else text


class ApioneBatchApp:

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("APIOne 多任务批量调用工具")
        self.root.geometry("1180x820")
        self.root.minsize(980, 700)
        self.storage = TaskStorage(ROOT)
        tasks = self.storage.load()
        self.tasks = {task.task_id: task for task in self.storage.load_secrets(tasks)}
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.task_statuses: dict[str, str] = {}
        self.scheduler = TaskScheduler(ROOT, SdkRunner(ROOT), self._on_scheduler_event)
        self.selected_task_id = ""
        self._vars()
        self._build_ui()
        self._refresh_tasks()
        if self.tasks:
            self.selected_task_id = next(iter(self.tasks))
            self.task_tree.selection_set(self.selected_task_id)
            self.task_tree.focus(self.selected_task_id)
            self._load_form(self.tasks[self.selected_task_id])
            self._load_log(self.selected_task_id)
            self._update_task_state()
        else:
            self._new_task()
        self.root.after(200, self._poll_events)

    def _vars(self) -> None:
        self.task_name = tk.StringVar()
        self.enabled = tk.BooleanVar(value=True)
        self.api_name = tk.StringVar(value=DEFAULT_API)
        self.request_url = tk.StringVar(value=DEFAULT_URL)
        self.region = tk.StringVar(value="INTER")
        self.method = tk.StringVar(value="POST")
        self.media_type = tk.StringVar(value="application/json")
        self.path = tk.StringVar(value="")
        self.headers_json = tk.StringVar(value="{}")
        self.query_json = tk.StringVar(value="{}")
        self.jar_path = tk.StringVar(value=DEFAULT_JAR)
        self.java_path = tk.StringVar(value="java")
        self.ak = tk.StringVar(value=os.getenv("APIONE_AK", ""))
        self.sk = tk.StringVar(value=os.getenv("APIONE_SK", ""))
        self.total_count = tk.StringVar(value="10")
        self.frequency_mode = tk.StringVar(value="fixed")
        self.fixed_interval = tk.StringVar(value="5")
        self.random_min = tk.StringVar(value="3")
        self.random_max = tk.StringVar(value="10")
        self.retry_count = tk.StringVar(value="0")
        self.start_at = tk.StringVar()
        self.end_at = tk.StringVar()
        self.status = tk.StringVar(value="就绪")
        self.log_search = tk.StringVar()
        self.log_match_count = tk.StringVar(value="")

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background="#edf3f1")
        style.configure("Status.TFrame", background="#dbe9e5")
        style.configure("Title.TLabel", background="#edf3f1", foreground="#17343d", font=("Avenir Next", 20, "bold"))
        style.configure("Subtitle.TLabel", background="#edf3f1", foreground="#557078", font=("Avenir Next", 10))
        style.configure("Section.TLabel", foreground="#17343d", font=("Avenir Next", 13, "bold"))
        style.configure("TaskState.TLabel", foreground="#17343d", font=("Avenir Next", 11, "bold"))
        style.configure("Muted.TLabel", foreground="#5d7378", font=("Avenir Next", 9))
        style.configure("Status.TLabel", background="#dbe9e5", foreground="#23464d", font=("Avenir Next", 9, "bold"))
        style.configure("Card.TLabelframe", background="#ffffff", bordercolor="#c8d8d4", relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background="#ffffff", foreground="#17343d", font=("Avenir Next", 10, "bold"))
        style.configure("Primary.TButton", background="#0f7181", foreground="#ffffff", borderwidth=0, padding=(10, 7), font=("Avenir Next", 10, "bold"))
        style.map("Primary.TButton", background=[("active", "#0b5a67"), ("disabled", "#aac7ca")])
        style.configure("Accent.TButton", background="#d97706", foreground="#ffffff", borderwidth=0, padding=(10, 7), font=("Avenir Next", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#b85f04"), ("disabled", "#d7b58a")])
        style.configure("Secondary.TButton", background="#ffffff", foreground="#23464d", bordercolor="#b5ccc8", padding=(9, 6))
        style.map("Secondary.TButton", background=[("active", "#e5f1ee"), ("disabled", "#eef3f2")])
        style.configure("Danger.TButton", background="#fff5f3", foreground="#b42318", bordercolor="#e7b8b2", padding=(9, 6))
        style.map("Danger.TButton", background=[("active", "#fee4e1"), ("disabled", "#f8eeee")])
        for name, background, foreground, active in (
            ("CompactPrimary.TButton", "#0f7181", "#ffffff", "#0b5a67"),
            ("CompactAccent.TButton", "#d97706", "#ffffff", "#b85f04"),
            ("CompactSecondary.TButton", "#ffffff", "#23464d", "#e5f1ee"),
            ("CompactDanger.TButton", "#fff5f3", "#b42318", "#fee4e1"),
        ):
            style.configure(name, background=background, foreground=foreground, borderwidth=1, padding=(6, 3), font=("Avenir Next", 9, "bold"))
            style.map(name, background=[("active", active), ("disabled", "#eef3f2")])
        style.configure("Hidden.TNotebook", background="#edf3f1", borderwidth=0)
        style.layout("Hidden.TNotebook.Tab", [])
        style.configure("Nav.TButton", background="#dfeae7", foreground="#557078", borderwidth=1, padding=(8, 7), font=("Avenir Next", 10, "bold"))
        style.map("Nav.TButton", background=[("active", "#cbded9")])
        style.configure("NavSelected.TButton", background="#ffffff", foreground="#0f7181", borderwidth=1, padding=(8, 7), font=("Avenir Next", 10, "bold"))
        style.map("NavSelected.TButton", background=[("active", "#f4fbf9")])
        style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff", foreground="#23464d", rowheight=30, borderwidth=0)
        style.configure("Treeview.Heading", background="#e3efec", foreground="#31545a", font=("Avenir Next", 9, "bold"), padding=(6, 6))
        style.map("Treeview", background=[("selected", "#b9ded8")], foreground=[("selected", "#17343d")])
        style.configure("TNotebook", background="#edf3f1", borderwidth=0)
        style.configure("TNotebook.Tab", width=14, padding=(0, 8), foreground="#557078")
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")], foreground=[("selected", "#0f7181")])

    def _build_ui(self) -> None:
        self._configure_styles()
        self.root.configure(background="#edf3f1")
        outer = ttk.Frame(self.root, padding=16, style="App.TFrame")
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer, style="App.TFrame")
        header.pack(fill="x", pady=(0, 14))
        title_line = ttk.Frame(header, style="App.TFrame")
        title_line.pack(fill="x")
        ttk.Label(title_line, text="APIOne 批量工作台", style="Title.TLabel").pack(side="left")
        self.version_label = ttk.Label(title_line, text=f"版本 {load_current_version(ROOT)}", style="Muted.TLabel")
        self.version_label.pack(side="left", padx=(12, 0), pady=(7, 0))
        ttk.Label(header, text="配置、运行和追踪每一个接口任务", style="Subtitle.TLabel").pack(anchor="w", pady=(3, 0))
        actions = ttk.Frame(header, style="App.TFrame")
        actions.place(relx=1.0, rely=0.0, anchor="ne")
        ttk.Button(actions, text="新建任务", command=self._new_task, style="Secondary.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="环境检查", command=self._show_environment_check, style="Secondary.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="检查更新", command=self._check_update, style="Secondary.TButton").pack(side="left")

        paned = ttk.PanedWindow(outer, orient="horizontal")
        paned.pack(fill="both", expand=True)
        left = ttk.Frame(paned, padding=(0, 0, 10, 0), style="App.TFrame")
        right = ttk.Frame(paned, padding=(10, 0, 0, 0), style="App.TFrame")
        paned.add(left, weight=1)
        paned.add(right, weight=3)

        ttk.Label(left, text="任务菜单", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        tree_frame = ttk.Frame(left, style="App.TFrame")
        tree_frame.pack(fill="both", expand=True)
        tree_scrollbar = ttk.Scrollbar(tree_frame, orient="vertical")
        tree_scrollbar.pack(side="right", fill="y")
        self.task_tree = ttk.Treeview(tree_frame, columns=("name", "enabled", "status"), show="headings", selectmode="browse", yscrollcommand=tree_scrollbar.set)
        for key, title, width in (("name", "任务名称", 220), ("enabled", "启用状态", 84), ("status", "运行状态", 84)):
            self.task_tree.heading(key, text=title)
            self.task_tree.column(key, width=width, anchor="center" if key != "name" else "w", stretch=key == "name")
        self.task_tree.pack(side="left", fill="both", expand=True)
        tree_scrollbar.configure(command=self.task_tree.yview)
        self.task_tree.bind("<<TreeviewSelect>>", self._on_task_select)

        task_panel = ttk.LabelFrame(right, text="当前任务", padding=(8, 5), style="Card.TLabelframe")
        task_panel.pack(fill="x", pady=(0, 12))
        status_group = ttk.Frame(task_panel, style="App.TFrame")
        status_group.pack(side="left", fill="x", expand=True)
        status_line = ttk.Frame(status_group, style="App.TFrame")
        status_line.pack(fill="x")
        self.task_state_label = ttk.Label(status_line, text="未选择任务", style="TaskState.TLabel")
        self.task_state_label.pack(side="left")
        self.enabled_display = ttk.Label(status_line, text="启用状态：未知", style="Muted.TLabel")
        self.enabled_display.pack(side="left", padx=(12, 0))
        self.task_activity_label = ttk.Label(status_group, textvariable=self.status, style="Muted.TLabel", wraplength=360)
        self.task_activity_label.pack(anchor="w", pady=(2, 0))
        action_buttons = ttk.Frame(task_panel, style="App.TFrame")
        action_buttons.pack(side="right")
        ttk.Button(action_buttons, text="执行一次", command=self._execute_once, style="CompactPrimary.TButton", width=9).pack(side="left", padx=(0, 4))
        ttk.Button(action_buttons, text="执行批量", command=self._execute_batch, style="CompactAccent.TButton", width=9).pack(side="left", padx=(0, 4))
        self.toggle_button = ttk.Button(action_buttons, text="停用", command=self._toggle_enabled, style="CompactSecondary.TButton", width=7)
        self.toggle_button.pack(side="left", padx=(0, 6))
        ttk.Button(action_buttons, text="停止", command=self._stop_task, style="CompactSecondary.TButton", width=7).pack(side="left", padx=(0, 4))
        ttk.Button(action_buttons, text="删除", command=self._delete_task, style="CompactDanger.TButton", width=7).pack(side="left")

        navigation = ttk.Frame(right, style="App.TFrame")
        navigation.pack(fill="x", pady=(0, 4))
        for index in range(4):
            navigation.columnconfigure(index, weight=1, uniform="task-nav")
        self.navigation = navigation
        self.nav_buttons: list[ttk.Button] = []
        self.nav_pages: list[tuple[str, str]] = []
        editor = ttk.Notebook(right, style="Hidden.TNotebook")
        editor.pack(fill="both", expand=True)
        self.editor = editor
        self._scroll_canvases: dict[str, tk.Canvas] = {}
        self.root.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self._on_mousewheel, add="+")
        self.root.bind_all("<Button-5>", self._on_mousewheel, add="+")
        self._build_basic_tab(editor)
        self._build_param_tab(editor)
        self._build_schedule_tab(editor)
        self._build_log_tab(editor)

    def _register_nav_page(self, title: str, page: ttk.Frame) -> None:
        index = len(self.nav_pages)
        self.nav_pages.append((title, str(page)))
        button = ttk.Button(self.navigation, text=title, style="Nav.TButton", command=lambda i=index: self._select_nav_page(i))
        button.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 2, 0))
        self.nav_buttons.append(button)
        if index == 0:
            self._select_nav_page(0)

    def _select_nav_page(self, index: int) -> None:
        if not self.nav_pages:
            return
        index = max(0, min(index, len(self.nav_pages) - 1))
        self.editor.select(self.nav_pages[index][1])
        for position, button in enumerate(self.nav_buttons):
            button.configure(style="NavSelected.TButton" if position == index else "Nav.TButton")

    def _scrollable_tab(self, notebook: ttk.Notebook, title: str) -> ttk.Frame:
        """Create a tab whose form content grows without shrinking the notebook."""
        outer = ttk.Frame(notebook, style="App.TFrame")
        notebook.add(outer, text=title)
        canvas = tk.Canvas(outer, background="#edf3f1", highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        content = ttk.Frame(canvas, padding=12, style="App.TFrame")
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")
        self._scroll_canvases[str(outer)] = canvas
        self._register_nav_page(title, outer)

        def update_scroll_region(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_content_width(event) -> None:
            canvas.itemconfigure(window_id, width=max(event.width, 1))

        content.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", fit_content_width)
        return content

    def _on_mousewheel(self, event) -> None:
        """Scroll the currently selected configuration page with the pointer wheel."""
        if not hasattr(self, "editor"):
            return
        canvas = self._scroll_canvases.get(self.editor.select())
        if canvas is None:
            return
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            canvas.yview_scroll(-1, "units")
        else:
            canvas.yview_scroll(1, "units")

    def _build_basic_tab(self, notebook: ttk.Notebook) -> None:
        tab = self._scrollable_tab(notebook, "接口与鉴权")
        rows = [("任务名称", self.task_name, False), ("API Name", self.api_name, False), ("请求地址", self.request_url, False), ("Region", self.region, False), ("请求方法", self.method, False), ("媒体类型", self.media_type, False), ("接口 Path", self.path, False), ("Header JSON", self.headers_json, False), ("Query JSON", self.query_json, False), ("SDK JAR（相对路径）", self.jar_path, False), ("Java 命令", self.java_path, False), ("APIONE AK", self.ak, True), ("APIONE SK", self.sk, True)]
        for row, (label, var, secret) in enumerate(rows):
            ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 8))
            ttk.Entry(tab, textvariable=var, width=86, show="*" if secret else "").grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Label(tab, text="当前启用状态").grid(row=len(rows), column=0, sticky="w", pady=5, padx=(0, 8))
        self.basic_state_label = ttk.Label(tab, text="未知", style="Muted.TLabel")
        self.basic_state_label.grid(row=len(rows), column=1, sticky="w", pady=5)
        ttk.Checkbutton(tab, text="启用任务（保存后生效）", variable=self.enabled).grid(row=len(rows) + 1, column=1, sticky="w", pady=5)
        ttk.Label(tab, text="相对程序目录填写，例如：apione-http-client-1.0.3-RELEASE.jar", style="Muted.TLabel").grid(row=len(rows) + 2, column=1, sticky="w", pady=(2, 0))
        ttk.Button(tab, text="保存接口配置", command=self._save_basic_tab, style="Primary.TButton").grid(row=len(rows) + 3, column=1, sticky="w", pady=(14, 5))
        tab.columnconfigure(1, weight=1)

    def _build_param_tab(self, notebook: ttk.Notebook) -> None:
        tab = self._scrollable_tab(notebook, "请求参数")
        head = ttk.Frame(tab)
        head.pack(fill="x")
        ttk.Label(head, text="每行一个参数 JSON", style="Section.TLabel").pack(side="left")
        ttk.Button(head, text="查看配置说明", command=self._show_parameter_help).pack(side="right")
        ttk.Label(tab, text="支持 fixed、choice、random_range；参数名相同会被拒绝。", foreground="#555").pack(anchor="w", pady=(3, 6))
        ttk.Label(tab, text='示例：{"name":"id","value_type":"integer","mode":"random_range","min_value":1,"max_value":100}', foreground="#555").pack(anchor="w", pady=(2, 6))
        self.param_text = tk.Text(tab, height=12, wrap="word")
        self.param_text.pack(fill="both", expand=True)
        ttk.Label(tab, text='多个参数示例：\n{"name":"plate_no","value_type":"string","mode":"choice","values":["沪B10739","沪E66164"]}', foreground="#555").pack(anchor="w", pady=(6, 0))
        ttk.Button(tab, text="保存请求参数", command=self._save_param_tab, style="Primary.TButton").pack(anchor="w", pady=(12, 0))

    def _build_schedule_tab(self, notebook: ttk.Notebook) -> None:
        tab = self._scrollable_tab(notebook, "调度规则")
        rows = [("调用总次数", self.total_count), ("固定间隔（秒）", self.fixed_interval), ("随机最小间隔（秒）", self.random_min), ("随机最大间隔（秒）", self.random_max), ("重试次数", self.retry_count)]
        for row, (label, var) in enumerate(rows):
            ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 8))
            ttk.Entry(tab, textvariable=var, width=18).grid(row=row, column=1, sticky="w", pady=5)
        time_row = len(rows)
        for offset, (label, var) in enumerate((("开始时间", self.start_at), ("结束时间", self.end_at))):
            ttk.Label(tab, text=label).grid(row=time_row + offset, column=0, sticky="w", pady=5, padx=(0, 8))
            entry = ttk.Entry(tab, textvariable=var, width=24)
            entry.grid(row=time_row + offset, column=1, sticky="w", pady=5)
            ttk.Button(tab, text="选择时间", command=lambda v=var: self._pick_datetime(v)).grid(row=time_row + offset, column=2, sticky="w", padx=(6, 0))
        ttk.Label(tab, text="频率模式").grid(row=1, column=2, sticky="w", padx=(35, 8))
        ttk.Combobox(tab, textvariable=self.frequency_mode, values=["fixed", "random"], state="readonly", width=18).grid(row=1, column=3, sticky="w")
        ttk.Label(tab, text="时间格式：YYYY-MM-DD HH:MM:SS；也可点击“选择时间”；留空表示不限制", foreground="#555").grid(row=time_row + 2, column=1, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Button(tab, text="保存调度规则", command=self._save_schedule_tab, style="Primary.TButton").grid(row=time_row + 3, column=1, sticky="w", pady=(14, 5))

    def _build_log_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="动态日志")
        self._register_nav_page("动态日志", tab)
        toolbar = ttk.Frame(tab, style="App.TFrame")
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(toolbar, text="搜索日志", style="Section.TLabel").pack(side="left", padx=(0, 8))
        search_entry = ttk.Entry(toolbar, textvariable=self.log_search, width=34)
        search_entry.pack(side="left")
        search_entry.bind("<Escape>", lambda _event: self.log_search.set(""))
        ttk.Button(toolbar, text="清除", command=lambda: self.log_search.set(""), style="Secondary.TButton").pack(side="left", padx=(6, 10))
        ttk.Label(toolbar, textvariable=self.log_match_count, style="Muted.TLabel").pack(side="left")
        self.log_search.trace_add("write", lambda *_args: self._highlight_log_matches(scroll_to_match=True))
        text_frame = ttk.Frame(tab, style="App.TFrame")
        text_frame.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(text_frame, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        self.log_text = tk.Text(
            text_frame, wrap="none", state="disabled", background="#fbfdfc", foreground="#23464d",
            insertbackground="#23464d", padx=10, pady=8, relief="flat", borderwidth=0,
            font=("Menlo", 10), yscrollcommand=scrollbar.set,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self.log_text.yview)
        self.log_text.tag_configure("log_match", background="#ffe08a", foreground="#17343d")

    def _highlight_log_matches(self, scroll_to_match: bool = False) -> None:
        if not hasattr(self, "log_text"):
            return
        query = self.log_search.get().strip()
        self.log_text.configure(state="normal")
        self.log_text.tag_remove("log_match", "1.0", "end")
        count = 0
        first_match = None
        if query:
            start = "1.0"
            while True:
                position = self.log_text.search(query, start, stopindex="end", nocase=True)
                if not position:
                    break
                end = f"{position}+{len(query)}c"
                self.log_text.tag_add("log_match", position, end)
                first_match = first_match or position
                count += 1
                start = end
        self.log_text.configure(state="disabled")
        self.log_match_count.set(f"匹配 {count} 处" if query else "")
        if scroll_to_match and first_match:
            self.log_text.see(first_match)

    def _selected_task(self) -> TaskConfig:
        if not self.selected_task_id or self.selected_task_id not in self.tasks:
            raise ValueError("请先选择任务")
        return self.tasks[self.selected_task_id]

    def _persist_tasks(self) -> None:
        self.storage.save(self.tasks.values())
        self.storage.save_secrets(self.tasks.values())

    @staticmethod
    def _json_object(raw: str, label: str) -> dict:
        try:
            value = json.loads(raw.strip() or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} JSON 格式有误：{exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{label} 必须是 JSON 对象")
        return value

    def _save_basic_tab(self) -> None:
        try:
            task = self._selected_task()
            headers = self._json_object(self.headers_json.get(), "Header")
            query_params = self._json_object(self.query_json.get(), "Query")
            task.task_name = self.task_name.get().strip() or "未命名任务"
            task.enabled = self.enabled.get()
            task.api = ApiConfig(
                self.request_url.get().strip(), self.api_name.get().strip(), self.region.get().strip(),
                self.method.get().strip() or "POST", self.media_type.get().strip() or "application/json",
                self.path.get().strip(), _portable_jar_path(self.jar_path.get()), self.java_path.get().strip() or "java",
                headers, query_params,
            )
            task.access_key = self.ak.get().strip()
            task.secret_key = self.sk.get().strip()
            task.updated_at = datetime.now().astimezone().isoformat()
            self._persist_tasks()
            self._refresh_tasks()
            self.status.set(f"已保存接口配置：{task.task_name}")
        except (ValueError, OSError) as exc:
            messagebox.showerror("保存失败", str(exc))

    def _parse_parameters_from_editor(self) -> list[ParameterConfig]:
        try:
            data = [json.loads(line) for line in self.param_text.get("1.0", "end").splitlines() if line.strip()]
            parameters = [ParameterConfig(**item) for item in data]
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"请求参数配置有误：{exc}") from exc
        if not parameters:
            raise ValueError("至少配置一个请求参数")
        names = [parameter.name.strip() for parameter in parameters]
        if len(names) != len(set(names)):
            raise ValueError("请求参数名不能重复")
        for parameter in parameters:
            parameter.generate()
        return parameters

    def _save_param_tab(self) -> None:
        try:
            task = self._selected_task()
            task.parameters = self._parse_parameters_from_editor()
            task.updated_at = datetime.now().astimezone().isoformat()
            self._persist_tasks()
            self.status.set(f"已保存请求参数：{task.task_name}")
        except (ValueError, OSError) as exc:
            messagebox.showerror("保存失败", str(exc))

    def _schedule_from_editor(self) -> ScheduleConfig:
        try:
            schedule = ScheduleConfig(
                int(self.total_count.get()), self.start_at.get().strip(), self.end_at.get().strip(),
                self.frequency_mode.get(), float(self.fixed_interval.get()), float(self.random_min.get()),
                float(self.random_max.get()), int(self.retry_count.get()),
            )
            schedule.validate()
            return schedule
        except (ValueError, TypeError) as exc:
            raise ValueError(f"调度配置有误：{exc}") from exc

    def _save_schedule_tab(self) -> None:
        try:
            task = self._selected_task()
            task.schedule = self._schedule_from_editor()
            task.updated_at = datetime.now().astimezone().isoformat()
            self._persist_tasks()
            self.status.set(f"已保存调度规则：{task.task_name}")
        except (ValueError, OSError) as exc:
            messagebox.showerror("保存失败", str(exc))

    def _pick_datetime(self, target: tk.StringVar) -> None:
        top = tk.Toplevel(self.root)
        top.title("选择时间")
        top.transient(self.root)
        top.grab_set()
        frame = ttk.Frame(top, padding=14)
        frame.pack(fill="both", expand=True)
        now = datetime.now()
        current = target.get().strip()
        try:
            current_dt = datetime.fromisoformat(current) if current else now
        except ValueError:
            current_dt = now
        ttk.Label(frame, text="日期（YYYY-MM-DD）").grid(row=0, column=0, sticky="w", pady=5)
        date_var = tk.StringVar(value=current_dt.strftime("%Y-%m-%d"))
        ttk.Entry(frame, textvariable=date_var, width=18).grid(row=0, column=1, pady=5)
        ttk.Label(frame, text="时间（HH:MM:SS）").grid(row=1, column=0, sticky="w", pady=5)
        time_var = tk.StringVar(value=current_dt.strftime("%H:%M:%S"))
        ttk.Entry(frame, textvariable=time_var, width=18).grid(row=1, column=1, pady=5)
        def apply() -> None:
            try:
                value = datetime.strptime(f"{date_var.get().strip()} {time_var.get().strip()}", "%Y-%m-%d %H:%M:%S")
            except ValueError:
                messagebox.showerror("时间格式有误", "请输入正确的日期和时间。", parent=top)
                return
            target.set(value.strftime("%Y-%m-%d %H:%M:%S"))
            top.destroy()
        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="确定", command=apply).pack(side="left", padx=5)
        ttk.Button(buttons, text="取消", command=top.destroy).pack(side="left")

    def _show_parameter_help(self) -> None:
        top = tk.Toplevel(self.root)
        top.title("请求参数配置说明")
        top.geometry("760x560")
        top.transient(self.root)
        text = tk.Text(top, wrap="word", padx=14, pady=14, background="#fbfcfe")
        text.pack(fill="both", expand=True)
        text.insert("1.0", '''请求参数配置说明\n\n每行一个 JSON 对象，最终会合并成请求体 JSON。\n\n字段：\n1. name：参数名，必填，不能重复。\n2. value_type：string / integer / number / boolean / null。\n3. mode：\n   fixed：固定值，使用 value。\n   choice：从 values 数组中随机选择一个值。\n   random_range：在 min_value 到 max_value 之间随机生成，仅支持 integer / number。\n\n示例：\n{"name":"id","value_type":"integer","mode":"random_range","min_value":1,"max_value":100}\n{"name":"plate_no","value_type":"string","mode":"choice","values":["沪B10739","沪E66164"]}\n{"name":"enabled","value_type":"boolean","mode":"fixed","value":true}\n\n注意：\n- integer 会生成 JSON 整数，不要把数字写成字符串。\n- 每个参数一行；JSON 必须完整。\n- choice 的 values 不能为空。\n- random_range 必须填写最小值和最大值，且最小值不能大于最大值。\n- 例如接口要求 {"id":76}，应使用 value_type=integer。''')
        text.configure(state="disabled")
        ttk.Button(top, text="关闭", command=top.destroy).pack(pady=8)

    def _check_update(self) -> None:
        """检查远程 manifest，并在用户确认后安装更新。"""
        try:
            info = check_for_update(REMOTE_MANIFEST_URL, app_root=ROOT)
        except UpdateError as exc:
            messagebox.showerror("检查更新失败", f"远程更新清单暂时不可用：{exc}")
            return
        if not info.available:
            messagebox.showinfo("检查更新", f"当前已是最新版本（{info.current_version}）。")
            return
        notes = "\n".join(info.release_notes) if info.release_notes else "是否下载并安装？"
        prompt = f"发现新版本 {info.latest_version}。\n\n{notes}"
        if not messagebox.askyesno("发现新版本", prompt):
            return
        try:
            result = download_and_install(info, app_root=ROOT)
        except UpdateError as exc:
            messagebox.showerror("更新失败", str(exc))
            return
        messagebox.showinfo("更新完成", f"已更新到 {result.installed_version}。请重新启动工具。\n备份位置：{result.backup_path}")
        self.status.set("更新完成，请重新启动工具")

    def _show_environment_check(self) -> None:
        """显示环境检查结果，并允许安装缺失的便携 Python runtime。"""
        top = tk.Toplevel(self.root)
        top.title("环境检查")
        top.geometry("720x540")
        top.minsize(640, 460)
        top.transient(self.root)
        top.grab_set()

        frame = ttk.Frame(top, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="运行环境检查", font=("Avenir Next", 15, "bold")).pack(anchor="w")
        summary = tk.StringVar()
        ttk.Label(frame, textvariable=summary, style="Muted.TLabel", wraplength=660).pack(anchor="w", pady=(4, 12))
        rows = ttk.Frame(frame)
        rows.pack(fill="both", expand=True)
        progress = ttk.Progressbar(frame, mode="determinate", maximum=1, value=0)
        progress.pack(fill="x", pady=(12, 3))
        progress_text = tk.StringVar(value="")
        ttk.Label(frame, textvariable=progress_text, style="Muted.TLabel").pack(anchor="w")

        report: EnvironmentReport = check_environment(ROOT, self.java_path.get().strip() or "java")
        runtime_button = ttk.Button(frame, text="下载环境包并安装")
        java_button = ttk.Button(frame, text="安装 Java 17+")
        refresh_button = ttk.Button(frame, text="重新检查")

        def render(current: EnvironmentReport) -> None:
            nonlocal report
            report = current
            for child in rows.winfo_children():
                child.destroy()
            for row, item in enumerate(current.items):
                state_text = "支持" if item.ok else "不支持"
                if item.ok and item.repairable:
                    state_text = "可运行"
                state_color = "#18794e" if item.ok and not item.repairable else ("#a15c00" if item.ok else "#b42318")
                ttk.Label(rows, text=item.name, width=18, anchor="w", font=("Avenir Next", 10, "bold")).grid(row=row, column=0, sticky="nw", pady=7)
                ttk.Label(rows, text=state_text, foreground=state_color, width=8, anchor="w").grid(row=row, column=1, sticky="nw", pady=7)
                ttk.Label(rows, text=item.detail, style="Muted.TLabel", wraplength=470, justify="left").grid(row=row, column=2, sticky="nw", pady=7)
            rows.columnconfigure(2, weight=1)
            summary.set("全部环境满足运行要求" if current.supported else "存在不满足项，请安装或修复后重新检查")
            runtime_button.configure(state="normal" if current.can_install_runtime else "disabled")
            java_button.configure(state="normal" if current.java_guide else "disabled")

        def refresh() -> None:
            render(check_environment(ROOT, self.java_path.get().strip() or "java"))
            progress.configure(value=0)
            progress_text.set("")

        def show_progress(phase: str, completed: int, total: Optional[int], text: str) -> None:
            def update() -> None:
                if not top.winfo_exists():
                    return
                if total:
                    progress.stop()
                    progress.configure(mode="determinate", maximum=total, value=completed)
                else:
                    progress.configure(mode="indeterminate")
                    progress.start(12)
                progress_text.set(text)
            self.root.after(0, update)

        def download_runtime() -> None:
            runtime_button.configure(state="disabled")
            refresh_button.configure(state="disabled")
            progress.stop()
            progress.configure(mode="indeterminate")
            progress.start(12)
            progress_text.set("正在准备下载环境包…")

            def worker() -> None:
                try:
                    install_environment_package(REMOTE_MANIFEST_URL, ROOT, report.platform_key, show_progress)
                except Exception as exc:  # noqa: BLE001
                    self.root.after(0, lambda: messagebox.showerror("环境包安装失败", str(exc), parent=top))
                else:
                    self.root.after(0, lambda: progress_text.set("环境包安装完成，请重新检查"))
                finally:
                    def finish() -> None:
                        progress.stop()
                        refresh_button.configure(state="normal")
                        runtime_button.configure(state="normal")
                    self.root.after(0, finish)

            threading.Thread(target=worker, name="environment-installer", daemon=True).start()

        def install_java() -> None:
            if report.java_guide:
                java_env.run_install_command(report.java_guide, java_env.get_platform())
                progress_text.set("已启动 Java 安装向导，完成后点击“重新检查”")

        runtime_button.configure(command=download_runtime)
        java_button.configure(command=install_java)
        refresh_button.configure(command=refresh)
        render(report)
        button_row = ttk.Frame(frame)
        button_row.pack(fill="x", pady=(14, 0))
        runtime_button.pack(in_=button_row, side="left", padx=(0, 8))
        java_button.pack(in_=button_row, side="left", padx=(0, 8))
        refresh_button.pack(in_=button_row, side="left")
        ttk.Button(button_row, text="关闭", command=top.destroy).pack(side="right")

    def _new_task(self) -> None:
        task_id = f"task-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        task = TaskConfig(task_id=task_id, task_name="新建任务", api=ApiConfig(DEFAULT_URL, DEFAULT_API, jar_path=DEFAULT_JAR), parameters=[ParameterConfig("id", "integer", "random_range", min_value=1, max_value=100)])
        self.tasks[task_id] = task
        self.task_statuses[task_id] = "待保存配置"
        self.selected_task_id = task_id
        self._load_form(task)
        self._refresh_tasks()
        self._update_task_state()
        self.status.set("已新建任务，请填写配置后保存")

    def _on_task_select(self, _event=None) -> None:
        selected = self.task_tree.selection()
        if not selected:
            return
        task_id = selected[0]
        if task_id in self.tasks:
            self.selected_task_id = task_id
            self.status.set(self.task_statuses.get(task_id, "任务已加载"))
            self._load_form(self.tasks[task_id])
            self._load_log(task_id)
            self._update_task_state()

    def _load_form(self, task: TaskConfig) -> None:
        self.task_name.set(task.task_name)
        self.enabled.set(task.enabled)
        if hasattr(self, "basic_state_label"):
            self.basic_state_label.configure(text="已启用" if task.enabled else "已停用")
        self.api_name.set(task.api.api_name)
        self.request_url.set(task.api.request_url)
        self.region.set(task.api.region)
        self.method.set(task.api.method)
        self.media_type.set(task.api.media_type)
        self.path.set(task.api.path)
        self.headers_json.set(json.dumps(task.api.headers, ensure_ascii=False))
        self.query_json.set(json.dumps(task.api.query_params, ensure_ascii=False))
        self.jar_path.set(_portable_jar_path(task.api.jar_path))
        self.java_path.set(task.api.java_path)
        self.ak.set(task.access_key or os.getenv("APIONE_AK", ""))
        self.sk.set(task.secret_key or os.getenv("APIONE_SK", ""))
        schedule = task.schedule
        self.total_count.set(str(schedule.total_count))
        self.frequency_mode.set(schedule.frequency_mode)
        self.fixed_interval.set(str(schedule.fixed_interval_seconds))
        self.random_min.set(str(schedule.random_min_seconds))
        self.random_max.set(str(schedule.random_max_seconds))
        self.retry_count.set(str(schedule.retry_count))
        self.start_at.set(schedule.start_at)
        self.end_at.set(schedule.end_at)
        self.param_text.delete("1.0", "end")
        self.param_text.insert("1.0", "\n".join(json.dumps(parameter.__dict__, ensure_ascii=False) for parameter in task.parameters))

    def _form_task(self) -> TaskConfig:
        if not self.selected_task_id:
            raise ValueError("请先选择任务")
        task_id = self.selected_task_id
        try:
            parameters = self._parse_parameters_from_editor()
            schedule = self._schedule_from_editor()
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"参数或调度配置有误：{exc}") from exc
        if not self.ak.get().strip() or not self.sk.get().strip():
            raise ValueError("请填写 AK/SK，或设置 APIONE_AK/APIONE_SK")
        existing = self.tasks[task_id]
        task = TaskConfig(task_id=task_id, task_name=self.task_name.get().strip() or "未命名任务", enabled=self.enabled.get())
        headers = self._json_object(self.headers_json.get(), "Header")
        query_params = self._json_object(self.query_json.get(), "Query")
        task.api = ApiConfig(self.request_url.get().strip(), self.api_name.get().strip(), self.region.get().strip(), self.method.get().strip() or "POST", self.media_type.get().strip() or "application/json", self.path.get().strip(), _portable_jar_path(self.jar_path.get()), self.java_path.get().strip() or "java", headers, query_params)
        task.parameters = parameters
        task.schedule = schedule
        task.access_key = self.ak.get().strip()
        task.secret_key = self.sk.get().strip()
        task.created_at = existing.created_at or datetime.now().astimezone().isoformat()
        task.updated_at = datetime.now().astimezone().isoformat()
        task.validate()
        self.tasks[task_id] = task
        return task

    def _clear_form(self) -> None:
        draft = TaskConfig(
            task_id="",
            task_name="",
            api=ApiConfig(DEFAULT_URL, DEFAULT_API, jar_path=DEFAULT_JAR),
            parameters=[ParameterConfig("id", "integer", "random_range", min_value=1, max_value=100)],
        )
        self._load_form(draft)
        self._load_log("")

    def _delete_task(self) -> None:
        if not self.selected_task_id or self.selected_task_id not in self.tasks:
            return
        task_name = self.tasks[self.selected_task_id].task_name
        if not messagebox.askyesno(
            "删除任务",
            f"确认删除任务“{task_name}”？\n\n只删除任务配置，日志和结果文件会保留。",
        ):
            return
        deleted_id = self.selected_task_id
        self.scheduler.stop(deleted_id)
        del self.tasks[deleted_id]
        self.task_statuses.pop(deleted_id, None)
        self._persist_tasks()
        if self.tasks:
            self.selected_task_id = next(iter(self.tasks))
            self._load_form(self.tasks[self.selected_task_id])
            self._load_log(self.selected_task_id)
        else:
            self.selected_task_id = ""
            self._clear_form()
        self._refresh_tasks()
        self.status.set("任务已删除；日志和结果文件已保留")

    def _execute_once(self) -> None:
        self._execute(True)

    def _execute_batch(self) -> None:
        self._execute(False)

    def _execute(self, once: bool) -> None:
        try:
            task = self._form_task()
            # 执行一次 / 批量前先检测 Java 运行环境（强制重检，避免沿用旧的否定缓存）。
            java_path = task.api.java_path or "java"
            info = java_env.detect_java(java_path, use_cache=False)
            if not info.meets_requirement:
                guide = java_env.get_install_guide(reason=info.error)
                self._show_java_requirement(info, guide)
                return
            self.storage.save(self.tasks.values())
            self.storage.save_secrets(self.tasks.values())
            if not task.enabled and not once:
                raise ValueError("任务当前已停用，请先勾选“启用任务”")
            (self.scheduler.execute_once if once else self.scheduler.execute_batch)(task)
            self.status.set("任务已启动")
            self._refresh_tasks()
            self._update_task_state()
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("无法执行", str(exc))

    def _show_java_requirement(self, info: JavaInfo, guide: InstallGuide) -> None:
        """Java 环境不满足时弹窗：说明原因、展示版本与安装命令，用户明确同意后才安装。"""
        top = tk.Toplevel(self.root)
        top.title("需要 Java 运行环境")
        top.geometry("580x500")
        top.resizable(False, False)
        top.transient(self.root)
        top.grab_set()

        frm = ttk.Frame(top, padding=14)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="无法启动：缺少可用的 Java 运行环境", font=("Arial", 13, "bold")).pack(anchor="w")
        ttk.Label(frm, text=info.error or "未检测到可用的 Java 运行环境", foreground="#b00020", wraplength=540).pack(anchor="w", pady=(6, 2))

        if info.available:
            detail = f"Java 命令：{info.java_path}\nJava 版本：{info.major if info.major is not None else '未知'}\n版本信息：{info.version_text or '（无）'}"
        else:
            detail = f"Java 命令：{info.java_path}\n状态：未找到（未安装或未加入 PATH）"
        ttk.Label(frm, text=detail, foreground="#333", wraplength=540, justify="left").pack(anchor="w", pady=(2, 8))

        ttk.Label(
            frm,
            text=f"请在「{guide.label}」上安装 Java {java_env.MIN_JAVA_MAJOR}+（推荐 JDK 17），安装后重新点击“执行”。",
            wraplength=540,
        ).pack(anchor="w", pady=(0, 6))

        ttk.Label(frm, text="安装命令（也可复制后自行执行）：").pack(anchor="w", pady=(4, 2))
        cmd_box = tk.Text(frm, height=5, wrap="word", background="#f5f5f5")
        cmd_box.pack(fill="x")
        cmd_box.insert("1.0", "\n".join(guide.commands))
        cmd_box.configure(state="disabled")

        ttk.Label(frm, text=guide.note, foreground="#555", wraplength=540).pack(anchor="w", pady=(6, 0))

        btn_row = ttk.Frame(frm)
        btn_row.pack(fill="x", pady=(12, 0))
        plat = java_env.get_platform()

        def copy_commands() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(guide.commands))
            self.status.set("已复制安装命令到剪贴板")

        def do_run_install() -> None:
            # 用户明确点击“安装”即视为同意；执行交互式安装（非静默）。
            java_env.run_install_command(guide, plat)
            top.destroy()

        def do_open_page() -> None:
            # 用户明确点击“打开官方安装页”即视为同意；打开官方下载地址。
            java_env.open_official_page(guide.official_url)
            top.destroy()

        ttk.Button(btn_row, text="复制命令", command=copy_commands).pack(side="left", padx=(0, 6))
        if plat == "windows":
            ttk.Button(btn_row, text="用 winget 安装（交互）", command=do_run_install).pack(side="left", padx=(0, 6))
        else:
            ttk.Button(btn_row, text="在终端执行安装命令", command=do_run_install).pack(side="left", padx=(0, 6))
        if guide.official_url:
            ttk.Button(btn_row, text="打开官方安装页", command=do_open_page).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="取消", command=top.destroy).pack(side="right")

    def _toggle_enabled(self) -> None:
        if not self.selected_task_id or self.selected_task_id not in self.tasks:
            return
        task = self.tasks[self.selected_task_id]
        task.enabled = not task.enabled
        self.storage.save(self.tasks.values())
        self.storage.save_secrets(self.tasks.values())
        self._update_task_state()
        self.status.set("任务已启用" if task.enabled else "任务已停用")

    def _stop_task(self) -> None:
        if self.selected_task_id:
            self.scheduler.stop(self.selected_task_id)
            self.status.set("已请求停止")
            self._update_task_state()

    def _update_task_state(self) -> None:
        if not hasattr(self, "task_state_label"):
            return
        if not self.selected_task_id or self.selected_task_id not in self.tasks:
            self.task_state_label.configure(text="未选择任务")
            self.enabled_display.configure(text="启用状态：未知")
            self.status.set("未选择任务")
            if hasattr(self, "toggle_button"):
                self.toggle_button.configure(text="启用任务", state="disabled")
            return
        task = self.tasks[self.selected_task_id]
        running = self.scheduler.is_running(task.task_id)
        self.enabled_display.configure(text=f"启用状态：{'已启用' if task.enabled else '已停用'}")
        self.task_state_label.configure(text=f"运行状态：{'运行中' if running else '空闲'}")
        if hasattr(self, "toggle_button"):
            self.toggle_button.configure(text="停用任务" if task.enabled else "启用任务", state="normal")

    def _refresh_tasks(self) -> None:
        """原位刷新任务列表，避免重建 Treeview 触发表单重载。"""
        existing_ids = set(self.task_tree.get_children())
        task_ids = set(self.tasks)
        for stale_id in existing_ids - task_ids:
            self.task_tree.delete(stale_id)
        for task_id, task in self.tasks.items():
            values = (task.task_name, "已启用" if task.enabled else "已停用", self._tree_status(task))
            if self.task_tree.exists(task_id):
                self.task_tree.item(task_id, values=values)
            else:
                self.task_tree.insert("", "end", iid=task_id, values=values)
        if self.selected_task_id in self.tasks:
            self.task_tree.selection_set(self.selected_task_id)
            self.task_tree.focus(self.selected_task_id)
        self._update_task_state()

    def _tree_status(self, task: TaskConfig) -> str:
        if self.scheduler.is_running(task.task_id):
            return "运行中"
        message = self.task_statuses.get(task.task_id, "")
        if "失败" in message:
            return "失败"
        if "任务已完成" in message:
            return "已完成"
        if "任务已停止" in message:
            return "已停止"
        return "就绪"

    def _on_scheduler_event(self, task_id: str, message: str) -> None:
        self.events.put((task_id, message))

    def _poll_events(self) -> None:
        try:
            while True:
                task_id, message = self.events.get_nowait()
                self.task_statuses[task_id] = message
                if task_id == self.selected_task_id:
                    self.status.set(message)
                    self._load_log(task_id)
                    self._append_log(message)
                self._refresh_tasks()
                self._update_task_state()
        except queue.Empty:
            pass
        self.root.after(200, self._poll_events)

    def _load_log(self, task_id: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        path = ROOT / "logs" / f"{task_id}.log" if task_id else None
        if path and path.exists():
            self.log_text.insert("1.0", path.read_text(encoding="utf-8", errors="replace"))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self._highlight_log_matches(scroll_to_match=False)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{datetime.now():%H:%M:%S}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self._highlight_log_matches(scroll_to_match=False)


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("clam")
    except tk.TclError:
        pass
    ApioneBatchApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
