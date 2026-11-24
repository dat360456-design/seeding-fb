"""CustomTkinter GUI — 4 tab + log real-time + start/stop an toàn."""
from __future__ import annotations

import asyncio
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Any

import customtkinter as ctk

from .browser import BrowserEngine
from .config import (
    JoinConfig,
    PostShareConfig,
    RunConfig,
    ScanCommentConfig,
)
from .tasks import JoinTask, PostShareTask, ScanCommentTask, StopRequested


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ==========================================================================
# WORKER — chạy toàn bộ Playwright trong thread riêng
# ==========================================================================
class Worker(threading.Thread):
    """Thread chạy asyncio loop; giao tiếp với GUI qua ``event_q``."""

    def __init__(
        self,
        cfg: RunConfig,
        task_factory,
        event_q: queue.Queue[tuple[str, str]],
        stop_event: threading.Event,
    ) -> None:
        super().__init__(daemon=True)
        self.cfg = cfg
        self.task_factory = task_factory
        self.event_q = event_q
        self.stop_event = stop_event
        self.loop: asyncio.AbstractEventLoop | None = None

    def emit(self, level: str, msg: str) -> None:
        self.event_q.put((level, msg))

    def run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except Exception as exc:
            self.emit("ERR", f"worker crash: {type(exc).__name__}: {exc}")
        finally:
            try:
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            finally:
                self.loop.close()
            self.emit("DONE", "worker kết thúc")

    async def _main(self) -> None:
        engine = BrowserEngine(self.cfg, self.emit)
        try:
            self.emit("INFO", "khởi động Chromium…")
            await engine.start()
            self.emit("OK", "browser sẵn sàng")

            if self.cfg.cookie_string.strip():
                ok, status = await engine.check_login()
                if ok:
                    self.emit("OK", f"đăng nhập: {status}")
                else:
                    self.emit("ERR", f"đăng nhập thất bại: {status}")
                    if status != "checkpoint":
                        return
                    self.emit("INFO", "checkpoint — hãy xử lý trong cửa sổ browser")

            task = self.task_factory(engine, self.cfg, self.stop_event, self.emit)
            await task.run()

        except StopRequested:
            self.emit("INFO", "đã dừng theo yêu cầu")
        except Exception as exc:
            self.emit("ERR", f"task lỗi: {type(exc).__name__}: {exc}")
        finally:
            self.emit("INFO", "đóng browser…")
            await engine.close()


# ==========================================================================
# APP
# ==========================================================================
class App(ctk.CTk):
    POLL_MS = 100

    def __init__(self) -> None:
        super().__init__()
        self.title("FB Seeder — L4/L7 không liên quan, đây là browser automation")
        self.geometry("1180x820")
        self.minsize(1000, 720)

        self.event_q: queue.Queue[tuple[str, str]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: Worker | None = None
        self._log_lock = threading.Lock()

        self._build_ui()
        self.after(self.POLL_MS, self._poll_queue)

    # ── UI ────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        ctk.CTkLabel(
            header,
            text="FB SEEDER",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text="playwright · customtkinter",
            text_color="gray60",
            font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=(10, 0))

        self.status_chip = ctk.CTkLabel(
            header,
            text="IDLE",
            fg_color=("gray75", "gray25"),
            corner_radius=10,
            padx=12,
            height=26,
        )
        self.status_chip.pack(side="right")

        self.tabs = ctk.CTkTabview(self, corner_radius=12)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=16, pady=6)

        for name in ("Cấu hình & Cookie", "Join Group", "Đăng & Share", "Dò & Comment"):
            self.tabs.add(name)

        self._build_tab_config(self.tabs.tab("Cấu hình & Cookie"))
        self._build_tab_join(self.tabs.tab("Join Group"))
        self._build_tab_post(self.tabs.tab("Đăng & Share"))
        self._build_tab_scan(self.tabs.tab("Dò & Comment"))

        log_frame = ctk.CTkFrame(self, corner_radius=12)
        log_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(6, 14))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            log_frame, text="LIVE LOG", font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 4))

        self.log_box = ctk.CTkTextbox(
            log_frame,
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word",
            state="disabled",
        )
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 12))
        self.log_box.tag_config("OK", foreground="#4ade80")
        self.log_box.tag_config("ERR", foreground="#f87171")
        self.log_box.tag_config("INFO", foreground="#93c5fd")
        self.log_box.tag_config("FETCH", foreground="#fbbf24")
        self.log_box.tag_config("SYS", foreground="#a3a3a3")

        ctrl = ctk.CTkFrame(self, fg_color="transparent")
        ctrl.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))
        ctrl.grid_columnconfigure(0, weight=1)

        self.stop_btn = ctk.CTkButton(
            ctrl,
            text="■ STOP",
            fg_color="#7f1d1d",
            hover_color="#991b1b",
            width=140,
            command=self._on_stop,
            state="disabled",
        )
        self.stop_btn.grid(row=0, column=1, padx=(0, 8))

        self.clear_btn = ctk.CTkButton(
            ctrl,
            text="Clear log",
            fg_color="transparent",
            border_width=1,
            width=110,
            command=self._clear_log,
        )
        self.clear_btn.grid(row=0, column=2)

    # ── TAB 1: config ─────────────────────────────────────────────────
    def _build_tab_config(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(parent, text="Cookie (header hoặc JSON)").grid(
            row=0, column=0, sticky="nw", padx=12, pady=(12, 4)
        )
        self.cookie_box = ctk.CTkTextbox(parent, height=110)
        self.cookie_box.grid(row=1, column=0, columnspan=2, sticky="ew",
                              padx=12, pady=(0, 10))
        self.cookie_box.insert(
            "1.0",
            "c_user=...; xs=...; datr=...;\n"
            "# hoặc dán JSON array từ Cookie-Editor",
        )

        ctk.CTkLabel(parent, text="Delay join (s)").grid(
            row=2, column=0, sticky="w", padx=12
        )
        jf = ctk.CTkFrame(parent, fg_color="transparent")
        jf.grid(row=2, column=1, sticky="w", pady=4)
        self.join_min = self._num_entry(jf, "10")
        ctk.CTkLabel(jf, text="→").pack(side="left", padx=4)
        self.join_max = self._num_entry(jf, "20")

        ctk.CTkLabel(parent, text="Delay share (s)").grid(
            row=3, column=0, sticky="w", padx=12
        )
        sf = ctk.CTkFrame(parent, fg_color="transparent")
        sf.grid(row=3, column=1, sticky="w", pady=4)
        self.share_min = self._num_entry(sf, "60")
        ctk.CTkLabel(sf, text="→").pack(side="left", padx=4)
        self.share_max = self._num_entry(sf, "180")

        ctk.CTkLabel(parent, text="Delay comment (s)").grid(
            row=4, column=0, sticky="w", padx=12
        )
        cf = ctk.CTkFrame(parent, fg_color="transparent")
        cf.grid(row=4, column=1, sticky="w", pady=4)
        self.comment_min = self._num_entry(cf, "45")
        ctk.CTkLabel(cf, text="→").pack(side="left", padx=4)
        self.comment_max = self._num_entry(cf, "120")

        self.headless_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            parent, text="Headless (không hiển thị browser)",
            variable=self.headless_var,
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=12, pady=8)

        self.check_login_btn = ctk.CTkButton(
            parent, text="Kiểm tra đăng nhập", command=self._on_check_login
        )
        self.check_login_btn.grid(row=6, column=0, sticky="w", padx=12, pady=12)

        warn = ctk.CTkLabel(
            parent,
            text=(
                "Khuyến nghị an toàn: ≤8 post/ngày, ≤40 comment/ngày, "
                "≤2–3 comment liên tiếp, dừng ngay khi thấy cảnh báo throttle."
            ),
            text_color="#fbbf24",
            wraplength=820,
            justify="left",
        )
        warn.grid(row=7, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 12))

    # ── TAB 2: join ───────────────────────────────────────────────────
    def _build_tab_join(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(3, weight=1)

        self.join_mode = tk.StringVar(value="urls")
        mode_frame = ctk.CTkFrame(parent, fg_color="transparent")
        mode_frame.grid(row=0, column=0, sticky="w", padx=12, pady=10)
        ctk.CTkRadioButton(
            mode_frame, text="Theo danh sách link",
            variable=self.join_mode, value="urls",
        ).pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(
            mode_frame, text="Theo từ khoá",
            variable=self.join_mode, value="keywords",
        ).pack(side="left")

        ctk.CTkLabel(
            parent, text="Mỗi dòng một URL hoặc một từ khoá"
        ).grid(row=1, column=0, sticky="w", padx=12)

        self.join_input = ctk.CTkTextbox(parent, height=180)
        self.join_input.grid(row=2, column=0, sticky="nsew", padx=12, pady=6)

        ctl = ctk.CTkFrame(parent, fg_color="transparent")
        ctl.grid(row=3, column=0, sticky="sw", padx=12, pady=(0, 10))
        ctk.CTkLabel(ctl, text="Max join:").pack(side="left")
        self.join_limit = ctk.CTkEntry(ctl, width=70)
        self.join_limit.insert(0, "10")
        self.join_limit.pack(side="left", padx=(6, 16))

        ctk.CTkButton(
            ctl, text="▶ BẮT ĐẦU JOIN", command=self._on_start_join
        ).pack(side="left")

    # ── TAB 3: post/share ─────────────────────────────────────────────
    def _build_tab_post(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(parent, text="Group đăng gốc (URL)").grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 0)
        )
        self.origin_entry = ctk.CTkEntry(parent, placeholder_text="https://facebook.com/groups/...")
        self.origin_entry.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))

        ctk.CTkLabel(parent, text="Nội dung bài đăng").grid(
            row=2, column=0, sticky="w", padx=12
        )
        self.post_content = ctk.CTkTextbox(parent, height=140)
        self.post_content.grid(row=3, column=0, sticky="nsew", padx=12, pady=4)

        media_row = ctk.CTkFrame(parent, fg_color="transparent")
        media_row.grid(row=4, column=0, sticky="ew", padx=12, pady=4)
        ctk.CTkButton(
            media_row, text="Chọn ảnh…", width=110, command=self._pick_media
        ).pack(side="left")
        self.media_label = ctk.CTkLabel(media_row, text="(chưa chọn)", text_color="gray60")
        self.media_label.pack(side="left", padx=8)

        ctk.CTkLabel(parent, text="Group đích để share (mỗi dòng 1 URL)").grid(
            row=5, column=0, sticky="w", padx=12, pady=(8, 0)
        )
        self.share_targets = ctk.CTkTextbox(parent, height=110)
        self.share_targets.grid(row=6, column=0, sticky="nsew", padx=12, pady=4)

        ctk.CTkButton(
            parent, text="▶ ĐĂNG & SHARE", command=self._on_start_post
        ).grid(row=7, column=0, sticky="w", padx=12, pady=10)

        self._media_paths: list[str] = []

    # ── TAB 4: scan ───────────────────────────────────────────────────
    def _build_tab_scan(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(3, weight=1)
        parent.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(parent, text="Từ khoá dò (mỗi dòng 1 từ)").grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 0)
        )
        self.scan_keywords = ctk.CTkTextbox(parent, height=70)
        self.scan_keywords.grid(row=1, column=0, sticky="ew", padx=12, pady=4)

        ctk.CTkLabel(parent, text="Nguồn quét (URL group/feed, mỗi dòng 1)").grid(
            row=2, column=0, sticky="w", padx=12
        )
        self.scan_targets = ctk.CTkTextbox(parent, height=80)
        self.scan_targets.grid(row=3, column=0, sticky="nsew", padx=12, pady=4)

        ctk.CTkLabel(parent, text="Mẫu comment (mỗi dòng 1, xoay vòng ngẫu nhiên)").grid(
            row=4, column=0, sticky="w", padx=12
        )
        self.scan_comments = ctk.CTkTextbox(parent, height=80)
        self.scan_comments.grid(row=5, column=0, sticky="nsew", padx=12, pady=4)

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.grid(row=6, column=0, sticky="w", padx=12, pady=8)
        ctk.CTkLabel(row, text="Max comment:").pack(side="left")
        self.scan_limit = ctk.CTkEntry(row, width=70)
        self.scan_limit.insert(0, "10")
        self.scan_limit.pack(side="left", padx=(6, 16))
        ctk.CTkButton(
            row, text="▶ QUÉT & COMMENT", command=self._on_start_scan
        ).pack(side="left")

    # ── helpers ───────────────────────────────────────────────────────
    @staticmethod
    def _num_entry(parent, default: str) -> ctk.CTkEntry:
        e = ctk.CTkEntry(parent, width=70)
        e.insert(0, default)
        e.pack(side="left")
        return e

    def _collect_run_cfg(self) -> RunConfig | None:
        try:
            cfg = RunConfig(
                cookie_string=self.cookie_box.get("1.0", "end").strip(),
                headless=bool(self.headless_var.get()),
                delay_join_min=float(self.join_min.get()),
                delay_join_max=float(self.join_max.get()),
                delay_share_min=float(self.share_min.get()),
                delay_share_max=float(self.share_max.get()),
                delay_comment_min=float(self.comment_min.get()),
                delay_comment_max=float(self.comment_max.get()),
            )
        except ValueError:
            messagebox.showerror("Lỗi", "Delay phải là số")
            return None
        if cfg.delay_join_max < cfg.delay_join_min:
            messagebox.showerror("Lỗi", "delay_join_max phải ≥ min")
            return None
        return cfg

    # ── actions ───────────────────────────────────────────────────────
    def _on_check_login(self) -> None:
        cfg = self._collect_run_cfg()
        if cfg is None:
            return
        self._start_worker(lambda e, c, s, log: _LoginProbe(e, c, log), cfg)

    def _on_start_join(self) -> None:
        cfg = self._collect_run_cfg()
        if cfg is None:
            return
        raw = self.join_input.get("1.0", "end").strip()
        items = [line.strip() for line in raw.splitlines() if line.strip()]
        mode = self.join_mode.get()
        jcfg = JoinConfig(
            mode=mode,
            urls=items if mode == "urls" else [],
            keywords=items if mode == "keywords" else [],
            max_joins=int(self.join_limit.get() or 10),
        )
        self._start_worker(
            lambda e, c, s, log: JoinTask(e, c, s, log, jcfg=jcfg), cfg
        )

    def _on_start_post(self) -> None:
        cfg = self._collect_run_cfg()
        if cfg is None:
            return
        targets = [
            line.strip()
            for line in self.share_targets.get("1.0", "end").splitlines()
            if line.strip()
        ]
        pcfg = PostShareConfig(
            content=self.post_content.get("1.0", "end").strip(),
            media_paths=list(self._media_paths),
            origin_group_url=self.origin_entry.get().strip(),
            share_targets=targets,
        )
        self._start_worker(
            lambda e, c, s, log: PostShareTask(e, c, s, log, pcfg=pcfg), cfg
        )

    def _on_start_scan(self) -> None:
        cfg = self._collect_run_cfg()
        if cfg is None:
            return
        kw = [l.strip() for l in self.scan_keywords.get("1.0", "end").splitlines() if l.strip()]
        cm = [l.strip() for l in self.scan_comments.get("1.0", "end").splitlines() if l.strip()]
        tg = [l.strip() for l in self.scan_targets.get("1.0", "end").splitlines() if l.strip()]
        scfg = ScanCommentConfig(
            keywords=kw,
            comments=cm,
            target_urls=tg,
            max_comments=int(self.scan_limit.get() or 10),
        )
        self._start_worker(
            lambda e, c, s, log: ScanCommentTask(e, c, s, log, scfg=scfg), cfg
        )

    def _on_stop(self) -> None:
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self._append("SYS", "đã gửi tín hiệu dừng…")
            self._set_status("STOPPING", "#fbbf24")

    def _pick_media(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Chọn ảnh",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.gif")],
        )
        if paths:
            self._media_paths = list(paths)
            self.media_label.configure(
                text=f"{len(paths)} ảnh: " + ", ".join(p.split("/")[-1] for p in paths)
            )

    # ── worker plumbing ───────────────────────────────────────────────
    def _start_worker(self, factory, cfg: RunConfig) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("Đang chạy", "Một tác vụ đang chạy")
            return

        self.stop_event = threading.Event()
        self.worker = Worker(cfg, factory, self.event_q, self.stop_event)
        self.worker.start()

        self.stop_btn.configure(state="normal")
        self._set_status("RUNNING", "#ef4444")

    def _poll_queue(self) -> None:
        try:
            while True:
                level, msg = self.event_q.get_nowait()
                self._append(level, msg)
                if level == "DONE":
                    self.stop_btn.configure(state="disabled")
                    self._set_status("IDLE", ("gray75", "gray25"))
        except queue.Empty:
            pass

        if self.worker and not self.worker.is_alive():
            self.stop_btn.configure(state="disabled")
            self._set_status("IDLE", ("gray75", "gray25"))

        self.after(self.POLL_MS, self._poll_queue)

    def _append(self, level: str, msg: str) -> None:
        import time as _t
        line = f"{_t.strftime('%H:%M:%S')} [{level:^5}] {msg}\n"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line, level)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _set_status(self, text: str, color) -> None:
        self.status_chip.configure(text=text, fg_color=color)


# ==========================================================================
# LOGIN PROBE — task giả để kiểm tra cookie
# ==========================================================================
class _LoginProbe:
    def __init__(self, engine: BrowserEngine, cfg: RunConfig, log) -> None:
        self.engine = engine
        self.cfg = cfg
        self.log = log

    async def run(self) -> None:
        ok, status = await self.engine.check_login()
        self.log("OK" if ok else "ERR", f"login probe: {status}")