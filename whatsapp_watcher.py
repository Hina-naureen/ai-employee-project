"""
whatsapp_watcher.py
────────────────────
Gold Tier — Personal AI Employee

Monitors WhatsApp Web via Playwright, detects unread messages that
contain trigger keywords, and saves each one as a Markdown task file
in AI_Employee_Vault/Needs_Action/.

Setup (one-time):
  1. Install Playwright:
         pip install playwright
         playwright install chromium
  2. Run this script — a Chromium window opens showing WhatsApp Web.
  3. Scan the QR code with your phone (WhatsApp > Linked Devices).
  4. The session is saved to whatsapp_session/ so you only scan once.

Run:
    python whatsapp_watcher.py

Environment variables (all optional):
    WA_POLL_INTERVAL    seconds between checks           (default: 15)
    WA_HEADLESS         "true" to hide the browser       (default: false)
    WA_SESSION_DIR      path to store browser session    (default: ./whatsapp_session)
"""

import io
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Playwright import (optional — graceful error if not installed) ─────────────
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    _PW_AVAILABLE = True
except ImportError:
    _PW_AVAILABLE = False

from base_watcher import BaseWatcher, NEEDS_ACTION

# ── Config ─────────────────────────────────────────────────────────────────────
POLL_INTERVAL = int(os.environ.get("WA_POLL_INTERVAL", "15"))
HEADLESS      = os.environ.get("WA_HEADLESS", "false").lower() == "true"
SESSION_DIR   = Path(os.environ.get("WA_SESSION_DIR", "whatsapp_session")).resolve()

# ── Keyword → priority mapping ─────────────────────────────────────────────────
# Each entry: (priority_label, set_of_trigger_keywords)
# Evaluated top-to-bottom; first match wins.
_PRIORITY_RULES: list[tuple[str, set[str]]] = [
    ("critical", {
        "urgent", "emergency", "asap", "immediately",
        "critical", "911", "sos",
    }),
    ("high", {
        "invoice", "payment", "pay", "billing", "overdue",
        "help", "stuck", "blocked", "deadline", "due today",
        "important", "action required", "please respond",
    }),
    ("normal", {
        "meeting", "schedule", "reminder", "follow up",
        "update", "check", "confirm", "request",
    }),
]

# All trigger keywords (flat set — used for quick pre-filter)
_ALL_TRIGGERS: set[str] = {kw for _, kws in _PRIORITY_RULES for kw in kws}

# ── WhatsApp Web selectors ─────────────────────────────────────────────────────
# These CSS/aria selectors target WhatsApp Web's DOM structure.
# WhatsApp occasionally updates its markup; update these if they break.

# Unread chat badge (the green number bubble on a conversation row)
_SEL_UNREAD_BADGE   = "span[aria-label*='unread message']"

# The parent conversation panel containing the badge
_SEL_CHAT_ROW       = "div[role='listitem']"

# Contact/group name inside a conversation row
_SEL_CONTACT_NAME   = "span[dir='auto'][title]"

# Last message preview text inside a conversation row
_SEL_MSG_PREVIEW    = "span.x1iyjqo2"   # WhatsApp Web internal class

# Open chat message bubbles (visible after clicking a conversation)
_SEL_MSG_BUBBLE     = "div.message-in span.selectable-text span[dir='ltr']"

# QR code element — used to detect if we're on the login screen
_SEL_QR_CODE        = "div[data-ref]"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_priority(text: str) -> str | None:
    """
    Return the priority label if *text* contains any trigger keyword,
    or None if no trigger matches (message should be ignored).
    """
    corpus = text.lower()
    for priority, keywords in _PRIORITY_RULES:
        if any(kw in corpus for kw in keywords):
            return priority
    return None


def _safe_filename(sender: str, msg_id: str) -> str:
    """Build a safe snake_case filename from sender + short id."""
    name = re.sub(r"[^\w\s-]", "", sender.lower().strip())
    name = re.sub(r"[\s_-]+", "_", name)[:30].strip("_") or "unknown"
    return f"whatsapp_{name}_{msg_id}.md"


def _build_markdown(sender: str, message: str, priority: str, timestamp: str) -> str:
    """Render a WhatsApp message as a Markdown task file."""
    suggested = _suggest_actions(message, priority)
    action_lines = "\n".join(f"- {a}" for a in suggested)

    return (
        f"---\n"
        f"type: whatsapp\n"
        f"sender: {sender}\n"
        f"priority: {priority}\n"
        f"status: pending\n"
        f"received: {timestamp}\n"
        f"---\n"
        f"\n"
        f"## Message Content\n"
        f"\n"
        f"{message.strip()}\n"
        f"\n"
        f"## Suggested Actions\n"
        f"\n"
        f"{action_lines}\n"
    )


def _suggest_actions(message: str, priority: str) -> list[str]:
    """Return context-aware suggested actions."""
    corpus = message.lower()
    actions: list[str] = []

    if any(kw in corpus for kw in {"invoice", "payment", "billing", "pay", "overdue"}):
        actions.append("Review and process payment / forward to finance")
    if any(kw in corpus for kw in {"?", "please reply", "let me know", "confirm", "respond"}):
        actions.append("Reply to sender")
    if any(kw in corpus for kw in {"meeting", "schedule", "call", "zoom", "teams"}):
        actions.append("Check calendar and confirm meeting")
    if any(kw in corpus for kw in {"help", "stuck", "blocked", "issue", "problem"}):
        actions.append("Assist or escalate the issue")
    if priority == "critical":
        actions.insert(0, "Respond immediately — marked CRITICAL")

    return actions if actions else ["Review message and respond"]


# ── Main watcher class ─────────────────────────────────────────────────────────

class WhatsAppWatcher(BaseWatcher):
    """
    Gold Tier WhatsApp Web monitor.

    Opens a persistent Chromium browser session, watches WhatsApp Web
    for unread messages containing trigger keywords, and saves each
    match as a Markdown task file in AI_Employee_Vault/Needs_Action/.
    """

    def __init__(self):
        super().__init__(name="WhatsAppWatcher")
        self._running   = False
        self._seen_ids: set[str] = set()   # deduplication: "sender::preview"

    # ── Playwright session ────────────────────────────────────────────────────

    def _launch_browser(self, playwright):
        """
        Launch (or resume) a persistent Chromium session.
        The session data is stored in SESSION_DIR so the QR code only
        needs to be scanned once.
        """
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(
            str(SESSION_DIR),
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
            viewport={"width": 1280, "height": 800},
        )
        page = context.pages[0] if context.pages else context.new_page()
        return context, page

    def _wait_for_login(self, page) -> bool:
        """
        Navigate to WhatsApp Web and block until the QR code is scanned
        and the chat list is visible.  Returns False if timeout.
        """
        self.log("Navigating to WhatsApp Web...")
        page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")

        # Check if already logged in (chat list visible within 5 s)
        try:
            page.wait_for_selector("div#pane-side", timeout=5_000)
            self.log("Already logged in.")
            return True
        except PWTimeout:
            pass

        # Not logged in — prompt user to scan QR
        self.log("WhatsApp Web login required.")
        self.log(">>> Please scan the QR code in the browser window. <<<")
        try:
            page.wait_for_selector("div#pane-side", timeout=120_000)   # 2-minute window
            self.log("Login successful.")
            return True
        except PWTimeout:
            self.log("ERROR: QR scan timeout (120 s). Restart and try again.")
            return False

    # ── Unread detection ──────────────────────────────────────────────────────

    def _get_unread_chats(self, page) -> list[dict]:
        """
        Scan the chat list panel for conversations with an unread badge.
        Returns a list of dicts: {sender, preview, chat_index}.
        """
        unread_chats: list[dict] = []

        try:
            chat_rows = page.query_selector_all(_SEL_CHAT_ROW)
        except Exception:
            return unread_chats

        for idx, row in enumerate(chat_rows):
            # Only process rows that have an unread badge
            badge = row.query_selector(_SEL_UNREAD_BADGE)
            if not badge:
                continue

            # Extract contact name
            name_el = row.query_selector(_SEL_CONTACT_NAME)
            sender  = name_el.get_attribute("title") if name_el else f"Contact_{idx}"
            if not sender:
                sender = f"Contact_{idx}"

            # Extract message preview text
            preview_el = row.query_selector(_SEL_MSG_PREVIEW)
            preview    = preview_el.inner_text().strip() if preview_el else ""

            unread_chats.append({
                "sender":      sender,
                "preview":     preview,
                "chat_index":  idx,
                "row":         row,
            })

        return unread_chats

    def _open_chat_and_read(self, page, chat: dict) -> str:
        """
        Click a chat row to open it, then read the most recent incoming
        message bubble.  Falls back to the preview text if clicking fails.
        """
        try:
            chat["row"].click()
            page.wait_for_selector(_SEL_MSG_BUBBLE, timeout=4_000)
            bubbles  = page.query_selector_all(_SEL_MSG_BUBBLE)
            if bubbles:
                # Take the last (most recent) bubble
                return bubbles[-1].inner_text().strip()
        except (PWTimeout, Exception):
            pass

        return chat["preview"]   # safe fallback

    # ── Processing ────────────────────────────────────────────────────────────

    def process(self, item: dict) -> None:
        """
        Evaluate one unread chat.  If a trigger keyword matches,
        save a Markdown task file to Needs_Action/.
        """
        sender  = item["sender"]
        message = item["message"]

        priority = _detect_priority(message)
        if priority is None:
            self.log(f"  Skipping (no trigger keyword): {sender!r}")
            return

        timestamp = self.now_str()
        msg_id    = f"{int(time.time())}_{abs(hash(sender + message[:20])) % 10_000:04d}"
        filename  = _safe_filename(sender, msg_id)
        dest_path = NEEDS_ACTION / filename

        content   = _build_markdown(sender, message, priority, timestamp)
        saved     = self.safe_write(dest_path, content)

        self.log(
            f"Task saved -> {saved.name}  "
            f"[from: {sender!r}]  [priority: {priority}]"
        )

    # ── Poll cycle ────────────────────────────────────────────────────────────

    def _poll_once(self, page) -> None:
        """Run one scan of the WhatsApp Web chat list."""
        self.log("Scanning for unread messages...")
        unread = self._get_unread_chats(page)

        if not unread:
            self.log("No unread messages with trigger keywords.")
            return

        self.log(f"Found {len(unread)} unread chat(s). Checking content...")

        for chat in unread:
            sender  = chat["sender"]
            # Read full message text (opens chat if needed)
            message = self._open_chat_and_read(page, chat)

            # Dedup key: sender + first 60 chars of message
            dedup_key = f"{sender}::{message[:60]}"
            if dedup_key in self._seen_ids:
                self.log(f"  Already processed: {sender!r} — skipping.")
                continue
            self._seen_ids.add(dedup_key)

            self.process({"sender": sender, "message": message})

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not _PW_AVAILABLE:
            raise ImportError(
                "Playwright is not installed.\n"
                "Run:  pip install playwright && playwright install chromium"
            )

        self.print_banner([
            f"Poll interval : every {POLL_INTERVAL}s",
            f"Headless mode : {'yes' if HEADLESS else 'no (browser visible)'}",
            f"Session dir   : {SESSION_DIR}",
            f"Tasks saved to: AI_Employee_Vault/Needs_Action/",
            "Press Ctrl+C to stop.",
        ])
        self.on_start()
        self._running = True

        with sync_playwright() as pw:
            context, page = self._launch_browser(pw)
            try:
                if not self._wait_for_login(page):
                    return

                while self._running:
                    try:
                        self._poll_once(page)
                    except PWTimeout:
                        self.log("Page timeout during poll — will retry next cycle.")
                    except Exception as exc:
                        self.on_error(exc)

                    self.log(f"Sleeping {POLL_INTERVAL}s until next check...")
                    time.sleep(POLL_INTERVAL)

            except KeyboardInterrupt:
                self.log("Shutdown signal received.")
            finally:
                context.close()
                self.stop()

    def stop(self) -> None:
        self._running = False
        self.on_stop()
        self.log("WhatsAppWatcher stopped.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    WhatsAppWatcher().start()
