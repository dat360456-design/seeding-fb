"""Cấu hình runtime, selector registry, và các hằng số an toàn."""
from __future__ import annotations

from dataclasses import dataclass, field


# ==========================================================================
# SELECTOR REGISTRY
# Mỗi entry là list các locator ưu tiên giảm dần. BrowserEngine thử lần lượt
# cho đến khi match. Khi Facebook đổi DOM, thêm selector mới vào đầu list.
# ==========================================================================
SELECTORS: dict[str, list[dict]] = {
    # ── AUTH ──────────────────────────────────────────────────────────
    "logged_in_marker": [
        {"type": "role", "role": "navigation", "name": "Facebook"},
        {"type": "css", "selector": "[aria-label='Facebook']"},
        {"type": "css", "selector": "div[role='banner']"},
        {"type": "css", "selector": "[data-pagelet='LeftNav']"},
    ],
    "login_page_marker": [
        {"type": "css", "selector": "input[name='pass']"},
        {"type": "role", "role": "button", "name": "Log in"},
        {"type": "css", "selector": "form[data-testid='royal_login_form']"},
    ],
    "checkpoint_marker": [
        {"type": "role", "role": "heading", "name": "Checkpoint"},
        {"type": "role", "role": "heading", "name": "Security check"},
        {"type": "css", "selector": "div[aria-label*='checkpoint' i]"},
    ],

    # ── GROUP JOIN ────────────────────────────────────────────────────
    # Button join thường có accessible name chứa "Join" + tên group.
    # Dùng get_by_role để bắt mọi biến thể aria-label.
    "join_button": [
        {"type": "role", "role": "button", "name": "Join group"},
        {"type": "role", "role": "button", "name": "Join Group"},
        {"type": "role", "role": "button", "name": "Tham gia nhóm"},
        {"type": "css", "selector": "div[aria-label^='Join' i][role='button']"},
        {"type": "css", "selector": "div[role='button']:has-text('Join')"},
    ],
    "joined_marker": [
        {"type": "role", "role": "button", "name": "Joined"},
        {"type": "role", "role": "button", "name": "Đã tham gia"},
        {"type": "css", "selector": "div[role='button'][aria-label*='Joined' i]"},
    ],
    "pending_marker": [
        {"type": "role", "role": "button", "name": "Cancel request"},
        {"type": "role", "role": "button", "name": "Pending"},
    ],
    "join_questions_dialog": [
        {"type": "role", "role": "dialog", "name": "Questions"},
        {"type": "css", "selector": "div[role='dialog']:has-text('questions')"},
    ],

    # ── COMPOSER (tạo bài đăng) ───────────────────────────────────────
    "composer_trigger": [
        {"type": "role", "role": "button", "name": "What's on your mind?"},
        {"type": "role", "role": "button", "name": "Write something..."},
        {"type": "css", "selector": "div[role='button']:has-text(\"What's on your mind\")"},
        {"type": "css", "selector": "[data-pagelet='Composer']"},
    ],
    "composer_textbox": [
        {"type": "role", "role": "textbox", "name": "What's on your mind?"},
        {"type": "css", "selector": "div[role='dialog'] div[contenteditable='true']"},
        {"type": "css", "selector": "div[contenteditable='true'][role='textbox']"},
    ],
    "composer_post_button": [
        # data-testid ổn định nhất khi Facebook còn dùng
        {"type": "css", "selector": "div[aria-label='Post'][role='button']"},
        {"type": "css", "selector": "button[data-testid='react-composer-post-button']"},
        {"type": "role", "role": "button", "name": "Post"},
        {"type": "role", "role": "button", "name": "Đăng"},
        {"type": "css", "selector": "div[role='dialog'] div[role='button']:has-text('Post')"},
    ],
    "composer_media_input": [
        {"type": "css", "selector": "input[type='file'][accept*='image']"},
        {"type": "css", "selector": "input[data-testid='media-sprout']"},
    ],

    # ── POST / SHARE ──────────────────────────────────────────────────
    "post_permalink_marker": [
        {"type": "css", "selector": "a[href*='/posts/']"},
        {"type": "css", "selector": "a[href*='story_fbid=']"},
    ],
    "share_button_on_post": [
        {"type": "role", "role": "button", "name": "Share"},
        {"type": "role", "role": "button", "name": "Chia sẻ"},
        {"type": "css", "selector": "div[aria-label='Share'][role='button']"},
    ],
    "share_to_group_option": [
        {"type": "role", "role": "menuitem", "name": "Share to a group"},
        {"type": "role", "role": "menuitem", "name": "Share to Group"},
        {"type": "css", "selector": "div[role='menuitem']:has-text('group')"},
    ],
    "share_group_search_input": [
        {"type": "role", "role": "textbox", "name": "Search for a group"},
        {"type": "css", "selector": "input[placeholder*='group' i]"},
    ],
    "share_group_result_item": [
        {"type": "role", "role": "option"},
        {"type": "css", "selector": "div[role='option']"},
    ],
    "share_submit_button": [
        {"type": "role", "role": "button", "name": "Share"},
        {"type": "role", "role": "button", "name": "Post"},
    ],

    # ── FEED / COMMENT ────────────────────────────────────────────────
    "feed_post_container": [
        {"type": "css", "selector": "div[role='feed'] > div"},
        {"type": "css", "selector": "div[data-pagelet^='FeedUnit']"},
        {"type": "css", "selector": "div[aria-posinset]"},
    ],
    "post_text_block": [
        {"type": "css", "selector": "div[data-ad-preview='message']"},
        {"type": "css", "selector": "div[dir='auto']"},
    ],
    "comment_button_on_post": [
        {"type": "role", "role": "button", "name": "Comment"},
        {"type": "role", "role": "button", "name": "Bình luận"},
    ],
    "comment_textbox": [
        {"type": "role", "role": "textbox", "name": "Write a comment..."},
        {"type": "role", "role": "textbox", "name": "Comment as"},
        {"type": "css", "selector": "div[contenteditable='true'][role='textbox']"},
    ],
    "comment_submit": [
        {"type": "role", "role": "button", "name": "Comment"},
        {"type": "css", "selector": "div[aria-label='Comment'][role='button']"},
    ],
}


# ==========================================================================
# SAFETY CONSTANTS
# Nguồn tham khảo: DR26-06-28-HUB-05 — ~5-10 posts/ngày, <50 comments/ngày,
# không quá 2-3 comment liên tiếp, pause ngay khi bị throttle.
# ==========================================================================
@dataclass
class SafetyLimits:
    max_posts_per_day: int = 8
    max_comments_per_day: int = 40
    max_comments_in_a_row: int = 2
    max_groups_per_day: int = 20
    throttle_pause_minutes: int = 30


# ==========================================================================
# RUNTIME CONFIG
# ==========================================================================
@dataclass
class RunConfig:
    cookie_string: str = ""
    headless: bool = False
    user_data_dir: str = "fb_profile"

    delay_join_min: float = 10.0
    delay_join_max: float = 20.0

    delay_share_min: float = 60.0
    delay_share_max: float = 180.0

    delay_comment_min: float = 45.0
    delay_comment_max: float = 120.0

    page_timeout_ms: int = 45_000
    navigation_timeout_ms: int = 60_000

    safety: SafetyLimits = field(default_factory=SafetyLimits)


@dataclass
class JoinConfig:
    mode: str = "urls"                 # "urls" | "keywords"
    urls: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    max_joins: int = 10


@dataclass
class PostShareConfig:
    content: str = ""
    media_paths: list[str] = field(default_factory=list)
    origin_group_url: str = ""
    share_targets: list[str] = field(default_factory=list)


@dataclass
class ScanCommentConfig:
    keywords: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    target_urls: list[str] = field(default_factory=list)
    max_comments: int = 10
    scroll_rounds: int = 3