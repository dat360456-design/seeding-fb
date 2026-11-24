"""Ba tác vụ seeding: Join, Post+Share, Scan+Comment."""
from __future__ import annotations

import random
import re
import threading
from typing import Callable
from urllib.parse import quote

from playwright.async_api import Page

from .browser import BrowserEngine
from .config import (
    JoinConfig,
    PostShareConfig,
    RunConfig,
    ScanCommentConfig,
)


class StopRequested(Exception):
    """Raise từ trong task khi stop_event được set."""


class BaseTask:
    def __init__(
        self,
        engine: BrowserEngine,
        cfg: RunConfig,
        stop_event: threading.Event,
        log: Callable[[str, str], None],
    ) -> None:
        self.engine = engine
        self.cfg = cfg
        self.stop = stop_event
        self.log = log
        self._commented_urls: set[str] = set()

    def _check_stop(self) -> None:
        if self.stop.is_set():
            raise StopRequested()

    async def _sleep_with_stop(self, lo: float, hi: float) -> None:
        total = random.uniform(lo, hi)
        elapsed = 0.0
        while elapsed < total:
            self._check_stop()
            step = min(0.5, total - elapsed)
            await self.engine.human_pause(step, step)
            elapsed += step


# ==========================================================================
# A. JOIN GROUP
# ==========================================================================
class JoinTask(BaseTask):
    def __init__(self, *args, jcfg: JoinConfig, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.jcfg = jcfg

    async def run(self) -> None:
        page = self.engine._require_page()

        urls = list(self.jcfg.urls)
        if self.jcfg.mode == "keywords":
            urls = await self._discover_by_keywords(page)
        elif self.jcfg.mode == "urls" and not urls:
            self.log("ERR", "không có URL group nào")
            return

        urls = [u for u in urls if u.strip()][: self.jcfg.max_joins]
        self.log("INFO", f"bắt đầu join {len(urls)} group")

        for idx, url in enumerate(urls, 1):
            self._check_stop()
            self.log("INFO", f"[{idx}/{len(urls)}] mở {url}")
            try:
                ok, reason = await self._join_one(page, url)
                if ok:
                    self.log("OK", f"  → đã gửi yêu cầu tham gia: {url}")
                else:
                    self.log("INFO", f"  → bỏ qua ({reason}): {url}")
            except StopRequested:
                raise
            except Exception as exc:
                self.log("ERR", f"  → lỗi {type(exc).__name__}: {url}")

            if idx < len(urls):
                self.log(
                    "INFO",
                    f"  → nghỉ {self.cfg.delay_join_min:g}–"
                    f"{self.cfg.delay_join_max:g}s",
                )
                await self._sleep_with_stop(
                    self.cfg.delay_join_min, self.cfg.delay_join_max
                )

    async def _join_one(self, page: Page, url: str) -> tuple[bool, str]:
        await page.goto(url, wait_until="domcontentloaded")
        await self.engine.human_pause(2.0, 4.0)

        if await self.engine.exists("joined_marker", timeout=3_000):
            return False, "đã tham gia từ trước"
        if await self.engine.exists("pending_marker", timeout=2_000):
            return False, "đang chờ duyệt"

        clicked = await self.engine.click("join_button", timeout=6_000)
        if not clicked:
            return False, "không tìm thấy nút Join"

        await self.engine.human_pause(1.5, 3.0)

        # Nhóm riêng tư đôi khi bật form câu hỏi — không tự trả lời,
        # log để người dùng xử lý thủ công.
        if await self.engine.exists("join_questions_dialog", timeout=2_000):
            return False, "nhóm yêu cầu trả lời câu hỏi — cần thao tác tay"
        return True, "sent"

    async def _discover_by_keywords(self, page: Page) -> list[str]:
        """Tìm group qua search Facebook, trả về URL group tìm được."""
        urls: list[str] = []
        for kw in self.jcfg.keywords:
            self._check_stop()
            self.log("INFO", f"tìm group theo từ khoá: {kw}")
            search_url = (
                "https://www.facebook.com/search/groups/?q=" + quote(kw)
            )
            await page.goto(search_url, wait_until="domcontentloaded")
            await self.engine.human_pause(2.0, 4.0)
            await self.engine.scroll_feed(rounds=2)

            links = page.locator("a[href*='/groups/']")
            count = await links.count()
            for i in range(count):
                href = await links.nth(i).get_attribute("href")
                if not href:
                    continue
                m = re.search(r"(/groups/[^/?#]+)", href)
                if m:
                    full = "https://www.facebook.com" + m.group(1)
                    if full not in urls:
                        urls.append(full)
            await self._sleep_with_stop(
                self.cfg.delay_join_min / 2, self.cfg.delay_join_max / 2
            )
        return urls


# ==========================================================================
# B. POST + SHARE
# ==========================================================================
class PostShareTask(BaseTask):
    def __init__(self, *args, pcfg: PostShareConfig, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.pcfg = pcfg

    async def run(self) -> None:
        page = self.engine._require_page()

        if not self.pcfg.origin_group_url:
            self.log("ERR", "chưa chọn group đăng gốc")
            return
        if not self.pcfg.content.strip():
            self.log("ERR", "nội dung bài đăng trống")
            return

        self.log("INFO", "đăng bài gốc…")
        post_url = await self._create_post(page)
        if not post_url:
            self.log("ERR", "không lấy được permalink — dừng share")
            return

        self.log("OK", f"bài gốc: {post_url}")
        if not self.pcfg.share_targets:
            self.log("INFO", "không có group đích để share")
            return

        for idx, target in enumerate(self.pcfg.share_targets, 1):
            self._check_stop()
            self.log("INFO", f"[{idx}/{len(self.pcfg.share_targets)}] share → {target}")
            try:
                ok = await self._share_to_group(page, post_url, target)
                if ok:
                    self.log("OK", f"  → share xong: {target}")
                else:
                    self.log("INFO", f"  → bỏ qua: {target}")
            except StopRequested:
                raise
            except Exception as exc:
                self.log("ERR", f"  → lỗi {type(exc).__name__}: {target}")

            if idx < len(self.pcfg.share_targets):
                self.log(
                    "INFO",
                    f"  → nghỉ {self.cfg.delay_share_min:g}–"
                    f"{self.cfg.delay_share_max:g}s",
                )
                await self._sleep_with_stop(
                    self.cfg.delay_share_min, self.cfg.delay_share_max
                )

    async def _create_post(self, page: Page) -> str | None:
        await page.goto(self.pcfg.origin_group_url, wait_until="domcontentloaded")
        await self.engine.human_pause(2.5, 4.5)

        if not await self.engine.click("composer_trigger", timeout=10_000):
            self.log("ERR", "không mở được composer")
            return None
        await self.engine.human_pause(1.5, 2.5)

        if not await self.engine.fill("composer_textbox", self.pcfg.content):
            self.log("ERR", "không điền được nội dung")
            return None

        if self.pcfg.media_paths:
            loc = await self.engine.find("composer_media_input", timeout=5_000)
            if loc:
                try:
                    await loc.set_input_files(self.pcfg.media_paths)
                    await self.engine.human_pause(3.0, 5.0)
                except Exception as exc:
                    self.log("ERR", f"upload media fail: {type(exc).__name__}")

        if not await self.engine.click("composer_post_button", timeout=8_000):
            self.log("ERR", "không bấm được Post")
            return None

        await self.engine.human_pause(6.0, 10.0)

        # Lấy permalink từ bài mới nhất trong feed
        loc = await self.engine.find("post_permalink_marker", timeout=10_000)
        if loc:
            href = await loc.get_attribute("href")
            if href:
                if href.startswith("/"):
                    href = "https://www.facebook.com" + href
                return href.split("?")[0]
        return None

    async def _share_to_group(
        self, page: Page, post_url: str, target_group: str
    ) -> bool:
        await page.goto(post_url, wait_until="domcontentloaded")
        await self.engine.human_pause(2.0, 4.0)

        if not await self.engine.click("share_button_on_post", timeout=8_000):
            return False
        await self.engine.human_pause(1.0, 2.0)

        if not await self.engine.click("share_to_group_option", timeout=5_000):
            return False
        await self.engine.human_pause(1.5, 2.5)

        group_id = self._extract_group_id(target_group)
        if group_id:
            if not await self.engine.fill("share_group_search_input", group_id):
                return False
        else:
            if not await self.engine.fill("share_group_search_input", target_group):
                return False
        await self.engine.human_pause(1.5, 3.0)

        if not await self.engine.click("share_group_result_item", timeout=5_000):
            return False
        await self.engine.human_pause(1.0, 2.0)

        return await self.engine.click("share_submit_button", timeout=6_000)

    @staticmethod
    def _extract_group_id(url: str) -> str | None:
        m = re.search(r"/groups/([^/?#]+)", url)
        return m.group(1) if m else None


# ==========================================================================
# C. SCAN + COMMENT
# ==========================================================================
class ScanCommentTask(BaseTask):
    def __init__(self, *args, scfg: ScanCommentConfig, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.scfg = scfg
        self._commented = 0

    async def run(self) -> None:
        page = self.engine._require_page()

        if not self.scfg.keywords:
            self.log("ERR", "chưa nhập từ khoá dò")
            return
        if not self.scfg.comments:
            self.log("ERR", "chưa nhập mẫu comment")
            return

        targets = self.scfg.target_urls or ["https://www.facebook.com/"]
        self.log(
            "INFO",
            f"quét {len(targets)} nguồn, tối đa {self.scfg.max_comments} comment",
        )

        for url in targets:
            if self._commented >= self.scfg.max_comments:
                break
            self._check_stop()
            self.log("INFO", f"quét feed: {url}")
            await page.goto(url, wait_until="domcontentloaded")
            await self.engine.human_pause(2.0, 4.0)

            await self._scan_page(page)

        self.log("DONE", f"kết thúc — đã comment {self._commented} bài")

    async def _scan_page(self, page: Page) -> None:
        for _ in range(self.scfg.scroll_rounds):
            if self._commented >= self.scfg.max_comments:
                return
            self._check_stop()

            posts = page.locator(
                "div[role='feed'] > div, div[data-pagelet^='FeedUnit']"
            )
            n = await posts.count()
            for i in range(n):
                if self._commented >= self.scfg.max_comments:
                    return
                self._check_stop()
                try:
                    if await self._maybe_comment(posts.nth(i)):
                        self._commented += 1
                        await self._sleep_with_stop(
                            self.cfg.delay_comment_min,
                            self.cfg.delay_comment_max,
                        )
                except StopRequested:
                    raise
                except Exception as exc:
                    self.log("ERR", f"  comment fail: {type(exc).__name__}")

            await self.engine.scroll_feed(rounds=1)

    async def _maybe_comment(self, post) -> bool:
        try:
            text = await post.inner_text(timeout=3_000)
        except Exception:
            return False
        if not text:
            return False

        if not any(kw.lower() in text.lower() for kw in self.scfg.keywords):
            return False

        # Chống comment lặp: lấy link bài nếu có
        try:
            link = await post.locator("a[href*='/posts/']").first.get_attribute(
                "href", timeout=2_000
            )
        except Exception:
            link = None
        if link and link in self._commented_urls:
            return False

        comment_btn = post.get_by_role("button", name=re.compile("Comment|Bình luận", re.I))
        try:
            if await comment_btn.count() == 0:
                return False
            await comment_btn.first.click(timeout=4_000)
        except Exception:
            return False

        await self.engine.human_pause(0.8, 1.8)

        box = post.get_by_role("textbox", name=re.compile("comment", re.I))
        if await box.count() == 0:
            box = post.locator("div[contenteditable='true'][role='textbox']")
        if await box.count() == 0:
            return False

        content = random.choice(self.scfg.comments)
        try:
            target = box.first
            await target.click(timeout=3_000)
            await target.type(content, delay=random.randint(30, 80))
            await self.engine.human_pause(0.5, 1.2)
            await target.press("Enter")
        except Exception:
            return False

        if link:
            self._commented_urls.add(link)
        self.log("OK", f"  comment: {content[:60]}…")
        return True