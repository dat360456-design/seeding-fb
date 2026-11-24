"""Lớp bọc Playwright: cookie injection, fallback locator, human-like delay."""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import Any, Callable

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    TimeoutError as PWTimeout,
    async_playwright,
)

from .config import SELECTORS, RunConfig


class BrowserEngine:
    """Quản lý vòng đời Playwright trong một thread riêng.

    Toàn bộ phương thức async được gọi từ một event loop chạy trong
    ``threading.Thread``; GUI chỉ giao tiếp qua queue.
    """

    def __init__(self, cfg: RunConfig, log: Callable[[str, str], None]) -> None:
        self.cfg = cfg
        self._log = log
        self._pw: Playwright | None = None
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None

    # ── lifecycle ─────────────────────────────────────────────────────
    async def start(self) -> Page:
        self._pw = await async_playwright().start()

        user_dir = Path(self.cfg.user_data_dir).resolve()
        user_dir.mkdir(parents=True, exist_ok=True)

        # Persistent context: cookie/localStorage tự lưu giữa các lần chạy,
        # giảm số lần phải inject cookie thủ công và giữ fingerprint ổn định
        # (khuyến nghị từ nghiên cứu về ban risk: cùng profile/IP an toàn hơn
        # đổi IP liên tục).
        self._ctx = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(user_dir),
            headless=self.cfg.headless,
            viewport={"width": 1366, "height": 900},
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        # Ẩn navigator.webdriver ở mức best-effort. Không phải stealth hoàn
        # chỉnh nhưng loại bỏ dấu hiệu rõ ràng nhất.
        await self._ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()
        self._page.set_default_timeout(self.cfg.page_timeout_ms)
        self._page.set_default_navigation_timeout(self.cfg.navigation_timeout_ms)

        if self.cfg.cookie_string.strip():
            await self.inject_cookies(self.cfg.cookie_string)

        return self._page

    async def close(self) -> None:
        try:
            if self._ctx:
                await self._ctx.close()
        finally:
            if self._pw:
                await self._pw.stop()
            self._pw = None
            self._ctx = None
            self._page = None

    # ── cookie ────────────────────────────────────────────────────────
    async def inject_cookies(self, raw: str) -> None:
        """Chấp nhận cả header string ``a=b; c=d`` và JSON array/object."""
        if self._ctx is None:
            raise RuntimeError("context chưa khởi tạo")

        cookies = self._parse_cookies(raw)
        if not cookies:
            self._log("ERR", "cookie rỗng hoặc không parse được")
            return

        # add_cookies phải chạy trước navigation đầu tiên để request
        # đầu tiên đã mang cookie.
        await self._ctx.add_cookies(cookies)
        self._log("OK", f"đã nạp {len(cookies)} cookie")

    @staticmethod
    def _parse_cookies(raw: str) -> list[dict[str, Any]]:
        raw = raw.strip()
        if not raw:
            return []

        # Thử JSON trước (Cookie-Editor export thường là array)
        if raw[0] in "[{":
            try:
                data = json.loads(raw)
                items = data if isinstance(data, list) else [data]
                out: list[dict[str, Any]] = []
                for c in items:
                    name = c.get("name") or c.get("Name")
                    value = c.get("value") or c.get("Value")
                    if not name or value is None:
                        continue
                    entry: dict[str, Any] = {
                        "name": str(name),
                        "value": str(value),
                        "domain": c.get("domain") or ".facebook.com",
                        "path": c.get("path") or "/",
                    }
                    if c.get("expirationDate"):
                        entry["expires"] = float(c["expirationDate"])
                    elif c.get("expires") not in (None, -1):
                        entry["expires"] = float(c["expires"])
                    out.append(entry)
                return out
            except json.JSONDecodeError:
                pass  # fallthrough sang header format

        # Header string: "c_user=...; xs=...; datr=..."
        out = []
        for part in raw.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, _, value = part.partition("=")
            name, value = name.strip(), value.strip()
            if name and value:
                out.append({
                    "name": name,
                    "value": value,
                    "domain": ".facebook.com",
                    "path": "/",
                })
        return out

    # ── auth check ────────────────────────────────────────────────────
    async def check_login(self) -> tuple[bool, str]:
        page = self._require_page()
        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
        await self.human_pause(1.5, 3.0)

        if await self.exists("checkpoint_marker", timeout=2_000):
            return False, "checkpoint"
        if await self.exists("login_page_marker", timeout=2_000):
            return False, "not_logged_in"
        if await self.exists("logged_in_marker", timeout=5_000):
            return True, "logged_in"
        return False, "unknown"

    # ── fallback locator ──────────────────────────────────────────────
    def _resolve_locator(self, page: Page, spec: dict) -> Locator:
        t = spec["type"]
        if t == "css":
            return page.locator(spec["selector"])
        if t == "role":
            return page.get_by_role(spec["role"], name=spec["name"])
        if t == "text":
            return page.get_by_text(spec["text"])
        raise ValueError(f"locator type không hỗ trợ: {t}")

    async def find(
        self,
        key: str,
        *,
        timeout: int = 8_000,
        page: Page | None = None,
    ) -> Locator | None:
        """Trả về locator visible đầu tiên khớp bất kỳ spec nào trong key."""
        page = page or self._require_page()
        deadline = time.monotonic() + timeout / 1000.0
        specs = SELECTORS.get(key, [])
        last_exc: Exception | None = None

        while time.monotonic() < deadline:
            for spec in specs:
                try:
                    loc = self._resolve_locator(page, spec)
                    count = await loc.count()
                    for i in range(min(count, 5)):
                        candidate = loc.nth(i)
                        if await candidate.is_visible():
                            return candidate
                except Exception as exc:
                    last_exc = exc
                    continue
            await asyncio.sleep(0.25)

        if last_exc:
            self._log("ERR", f"find '{key}' fail: {type(last_exc).__name__}")
        return None

    async def exists(self, key: str, *, timeout: int = 2_000,
                     page: Page | None = None) -> bool:
        return await self.find(key, timeout=timeout, page=page) is not None

    async def click(self, key: str, *, timeout: int = 8_000,
                    page: Page | None = None) -> bool:
        loc = await self.find(key, timeout=timeout, page=page)
        if loc is None:
            return False
        try:
            await loc.click(timeout=timeout)
            return True
        except PWTimeout:
            # Fallback: force click khi bị overlay che
            try:
                await loc.click(force=True, timeout=3_000)
                return True
            except Exception:
                return False

    async def fill(self, key: str, text: str, *, timeout: int = 8_000,
                   page: Page | None = None) -> bool:
        loc = await self.find(key, timeout=timeout, page=page)
        if loc is None:
            return False
        try:
            await loc.click(timeout=5_000)
            await loc.fill("")
            await loc.type(text, delay=random.randint(25, 70))
            return True
        except Exception:
            return False

    # ── human behavior ────────────────────────────────────────────────
    @staticmethod
    async def human_pause(lo: float, hi: float) -> None:
        await asyncio.sleep(random.uniform(lo, hi))

    async def scroll_feed(self, rounds: int = 3) -> None:
        page = self._require_page()
        for _ in range(rounds):
            await page.mouse.wheel(0, random.randint(800, 1400))
            await self.human_pause(1.2, 2.8)

    # ── internal ──────────────────────────────────────────────────────
    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("page chưa khởi tạo — gọi start() trước")
        return self._page