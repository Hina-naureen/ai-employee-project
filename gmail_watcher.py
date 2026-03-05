"""
gmail_watcher.py
────────────────
Gold Tier — Personal AI Employee

Polls Gmail for unread IMPORTANT emails, converts each one into a structured
Markdown task file, and saves it to AI_Employee_Vault/Needs_Action/.

Gold Tier upgrades over Silver:
  - Filters by IMPORTANT label (Gmail's own priority signal)
  - Detects STARRED emails → priority: critical
  - Detects attachments and lists them in the task file
  - Detects reply/thread context (is this part of a conversation?)
  - Detects Gmail category (Primary / Promotions / Updates / Social)
  - Retry logic with exponential back-off on API errors
  - Richer suggested actions based on deeper content analysis

Setup (one-time):
  1. Go to https://console.cloud.google.com/
  2. Create a project → Enable the Gmail API
  3. OAuth consent screen → Add your Gmail as a test user
  4. Credentials → Create OAuth 2.0 Client ID (Desktop app)
  5. Download JSON → save as  credentials.json  in this project folder
  6. Run once — a browser window opens for sign-in
  7. token.json is saved automatically for future runs

Dependencies:
    pip install google-api-python-client google-auth google-auth-oauthlib

Run:
    python gmail_watcher.py

Environment variables (all optional):
    GMAIL_POLL_INTERVAL   seconds between checks        (default: 60)
    GMAIL_MAX_RESULTS     max emails fetched per cycle  (default: 10)
    GMAIL_IMPORTANT_ONLY  set to "false" to fetch ALL unread (default: true)
"""

import base64
import os
import re
import sys
import time
import textwrap
from pathlib import Path

# ── Gmail / Google Auth imports ───────────────────────────────────────────────
try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    _GMAIL_AVAILABLE = True
except ImportError:
    _GMAIL_AVAILABLE = False

from base_watcher import BaseWatcher, NEEDS_ACTION

# ── Paths ─────────────────────────────────────────────────────────────────────
CREDENTIALS_FILE = Path(__file__).resolve().parent / "credentials.json"
TOKEN_FILE       = Path(__file__).resolve().parent / "token.json"

# ── Config ────────────────────────────────────────────────────────────────────
SCOPES            = ["https://www.googleapis.com/auth/gmail.modify"]
POLL_INTERVAL     = int(os.environ.get("GMAIL_POLL_INTERVAL", "60"))
MAX_RESULTS       = int(os.environ.get("GMAIL_MAX_RESULTS", "10"))
IMPORTANT_ONLY    = os.environ.get("GMAIL_IMPORTANT_ONLY", "true").lower() != "false"
MAX_BODY_CHARS    = 1500
MAX_RETRIES       = 3

# ── Priority keyword sets ─────────────────────────────────────────────────────
_CRITICAL_KW = {
    "urgent", "critical", "emergency", "immediately", "asap",
    "action required now", "time sensitive",
}
_HIGH_KW = {
    "important", "deadline", "overdue", "today", "follow up",
    "reminder", "response needed", "please reply",
}

# ── Suggested action rules: (trigger_keywords, action_label) ─────────────────
_ACTION_RULES: list[tuple[set, str]] = [
    ({"?", "please reply", "let me know", "feedback", "your thoughts", "response"},
     "Reply to sender"),
    ({"invoice", "payment", "billing", "receipt", "purchase", "quote"},
     "Review payment / forward to finance"),
    ({"meeting", "calendar", "schedule", "invite", "zoom", "teams", "google meet"},
     "Accept / decline meeting invite"),
    ({"attached", "attachment", "document", "report", "file", "pdf", "spreadsheet"},
     "Review attached document"),
    ({"deadline", "due date", "due by", "overdue", "submit by"},
     "Check deadline and take action"),
    ({"contract", "agreement", "sign", "signature", "approval"},
     "Review and sign / approve"),
    ({"unsubscribe", "newsletter", "promotion", "offer", "deal"},
     "Unsubscribe or archive"),
    ({"interview", "application", "job", "position", "role"},
     "Respond to interview / job request"),
]
_DEFAULT_ACTIONS = ["Reply", "Archive", "Follow up"]

# ── Gmail category label mapping ──────────────────────────────────────────────
_CATEGORY_LABELS = {
    "CATEGORY_PERSONAL":   "Primary",
    "CATEGORY_SOCIAL":     "Social",
    "CATEGORY_PROMOTIONS": "Promotions",
    "CATEGORY_UPDATES":    "Updates",
    "CATEGORY_FORUMS":     "Forums",
}


# ── Helper functions ──────────────────────────────────────────────────────────

def _header(headers: list[dict], name: str) -> str:
    """Extract a single header value by name (case-insensitive)."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _detect_priority(subject: str, snippet: str, labels: list[str]) -> str:
    """
    Determine priority using Gmail labels first, then keyword analysis.
    STARRED  → critical
    IMPORTANT keyword match → high
    Default  → normal
    """
    if "STARRED" in labels:
        return "critical"
    corpus = (subject + " " + snippet).lower()
    if any(kw in corpus for kw in _CRITICAL_KW):
        return "critical"
    if any(kw in corpus for kw in _HIGH_KW):
        return "high"
    return "normal"


def _detect_category(labels: list[str]) -> str:
    """Return a human-readable email category from Gmail label IDs."""
    for label_id, name in _CATEGORY_LABELS.items():
        if label_id in labels:
            return name
    return "Primary"


def _is_reply(subject: str, history_id: str, thread_id: str, msg_id: str) -> bool:
    """Return True if this message appears to be part of an existing thread."""
    return subject.lower().startswith("re:") or thread_id != msg_id


def _detect_attachments(payload: dict) -> list[str]:
    """
    Walk the MIME tree and collect attachment filenames.
    Returns a list of filename strings (may be empty).
    """
    attachments = []
    mime = payload.get("mimeType", "")

    if mime.startswith("multipart"):
        for part in payload.get("parts", []):
            filename = part.get("filename", "")
            if filename:
                attachments.append(filename)
            # Recurse into nested multipart
            attachments.extend(_detect_attachments(part))

    return attachments


def _decode_body(payload: dict) -> str:
    """
    Extract plain-text body from a Gmail message payload.
    Handles simple text/plain and multipart/alternative messages.
    """
    mime = payload.get("mimeType", "")

    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    if mime.startswith("multipart"):
        for part in payload.get("parts", []):
            result = _decode_body(part)
            if result:
                return result

    return ""


def _safe_filename(text: str, max_len: int = 48) -> str:
    """Convert arbitrary text to a safe snake_case filename fragment."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    text = re.sub(r"^_+|_+$", "", text)
    return text[:max_len] or "email"


def _suggest_actions(subject: str, snippet: str, attachments: list[str]) -> list[str]:
    """Return contextually relevant suggested actions."""
    corpus = (subject + " " + snippet + " " + " ".join(attachments)).lower()
    actions = []
    for keywords, action in _ACTION_RULES:
        if any(kw in corpus for kw in keywords):
            actions.append(action)
    if attachments and "Review attached document" not in actions:
        actions.insert(0, "Review attached document")
    return actions if actions else _DEFAULT_ACTIONS


def _build_markdown(
    msg_id: str,
    sender: str,
    subject: str,
    date: str,
    body: str,
    snippet: str,
    priority: str,
    category: str,
    is_reply: bool,
    attachments: list[str],
    actions: list[str],
) -> str:
    """Render a Gmail message as a Markdown task file."""
    display_body = (body.strip() or snippet.strip())[:MAX_BODY_CHARS]
    display_body = (
        textwrap.fill(display_body, width=100)
        if display_body else "(no content)"
    )

    action_lines      = "\n".join(f"- {a}" for a in actions)
    attachment_section = ""
    if attachments:
        att_lines = "\n".join(f"- `{a}`" for a in attachments)
        attachment_section = (
            f"\n"
            f"## Attachments\n"
            f"\n"
            f"{att_lines}\n"
        )

    thread_note = " *(part of existing thread)*" if is_reply else ""

    return (
        f"---\n"
        f"type: email\n"
        f"from: {sender}\n"
        f"subject: {subject}{thread_note}\n"
        f"date: {date}\n"
        f"priority: {priority}\n"
        f"category: {category}\n"
        f"has_attachments: {'yes' if attachments else 'no'}\n"
        f"status: pending\n"
        f"gmail_id: {msg_id}\n"
        f"---\n"
        f"\n"
        f"## Email Content\n"
        f"\n"
        f"{display_body}\n"
        f"{attachment_section}"
        f"\n"
        f"## Suggested Actions\n"
        f"\n"
        f"{action_lines}\n"
    )


# ── API retry helper ──────────────────────────────────────────────────────────

def _with_retry(func, *args, retries: int = MAX_RETRIES, **kwargs):
    """
    Call *func* with exponential back-off on HttpError / transient failures.
    Raises the last exception if all retries are exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return func(*args, **kwargs).execute()
        except HttpError as exc:
            if exc.resp.status in {429, 500, 502, 503, 504}:
                wait = 2 ** attempt
                print(f"  [WARN] API error {exc.resp.status}. Retry {attempt}/{retries} in {wait}s...")
                time.sleep(wait)
                last_exc = exc
            else:
                raise          # non-retriable error — propagate immediately
        except Exception as exc:
            last_exc = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"API call failed after {retries} retries: {last_exc}")


# ── Main watcher class ────────────────────────────────────────────────────────

class GmailWatcher(BaseWatcher):
    """
    Gold Tier Gmail watcher.

    Polls Gmail for unread IMPORTANT emails, converts each into a rich
    Markdown task file, and saves it to AI_Employee_Vault/Needs_Action/.
    """

    def __init__(self):
        super().__init__(name="GmailWatcher")
        self._service  = None
        self._running  = False
        self._seen_ids: set[str] = set()

    # ── Authentication ────────────────────────────────────────────────────────

    def _authenticate(self):
        """
        OAuth2 flow. On first run opens the browser for sign-in.
        On subsequent runs token.json is used (auto-refreshed if expired).
        """
        if not CREDENTIALS_FILE.exists():
            raise FileNotFoundError(
                f"\ncredentials.json not found at {CREDENTIALS_FILE}\n\n"
                "  Setup steps:\n"
                "  1. Go to https://console.cloud.google.com/\n"
                "  2. Enable the Gmail API\n"
                "  3. OAuth consent screen -> add your Gmail as test user\n"
                "  4. Credentials -> Create OAuth 2.0 Client ID (Desktop app)\n"
                "  5. Download JSON -> rename to credentials.json\n"
                "  6. Place in project root, then run again.\n"
            )

        creds = None
        if TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                self.log("Refreshing access token...")
                creds.refresh(Request())
            else:
                self.log("Opening browser for Gmail sign-in (one-time setup)...")
                flow  = InstalledAppFlow.from_client_secrets_file(
                    str(CREDENTIALS_FILE), SCOPES
                )
                creds = flow.run_local_server(port=0)

            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            self.log(f"Token saved -> {TOKEN_FILE.name}")

        return build("gmail", "v1", credentials=creds)

    # ── Fetching ──────────────────────────────────────────────────────────────

    def _fetch_unread(self) -> list[dict]:
        """
        Fetch full message dicts for unread emails.
        When IMPORTANT_ONLY=true (default) only fetches INBOX + UNREAD + IMPORTANT.
        """
        label_ids = ["INBOX", "UNREAD"]
        if IMPORTANT_ONLY:
            label_ids.append("IMPORTANT")

        results = _with_retry(
            self._service.users().messages().list,
            userId="me",
            labelIds=label_ids,
            maxResults=MAX_RESULTS,
        )
        messages = results.get("messages", [])
        full_msgs = []
        for msg in messages:
            if msg["id"] in self._seen_ids:
                continue
            full = _with_retry(
                self._service.users().messages().get,
                userId="me",
                id=msg["id"],
                format="full",
            )
            full_msgs.append(full)
        return full_msgs

    # ── Processing ────────────────────────────────────────────────────────────

    def process(self, message: dict) -> None:
        """Convert one Gmail message dict into a Markdown task file."""
        msg_id    = message["id"]
        thread_id = message.get("threadId", msg_id)
        payload   = message.get("payload", {})
        headers   = payload.get("headers", [])
        labels    = message.get("labelIds", [])
        snippet   = message.get("snippet", "")

        sender  = _header(headers, "From")
        subject = _header(headers, "Subject") or "(no subject)"
        date    = _header(headers, "Date")    or self.now_str()
        body    = _decode_body(payload)

        priority    = _detect_priority(subject, snippet, labels)
        category    = _detect_category(labels)
        reply_flag  = _is_reply(subject, message.get("historyId", ""), thread_id, msg_id)
        attachments = _detect_attachments(payload)
        actions     = _suggest_actions(subject, snippet, attachments)

        # Filename: email_<safe-subject>_<msg-id-prefix>.md
        filename  = f"email_{_safe_filename(subject)}_{msg_id[:8]}.md"
        dest_path = NEEDS_ACTION / filename

        content = _build_markdown(
            msg_id=msg_id,
            sender=sender,
            subject=subject,
            date=date,
            body=body,
            snippet=snippet,
            priority=priority,
            category=category,
            is_reply=reply_flag,
            attachments=attachments,
            actions=actions,
        )

        saved = self.safe_write(dest_path, content)
        self.log(
            f"Task saved -> {saved.name} "
            f"[priority: {priority}] "
            f"[category: {category}] "
            f"[attachments: {len(attachments)}]"
        )

        # Mark as read so it is not re-fetched next cycle
        try:
            _with_retry(
                self._service.users().messages().modify,
                userId="me",
                id=msg_id,
                body={"removeLabelIds": ["UNREAD"]},
            )
        except Exception as exc:
            self.log(f"  Could not mark message as read: {exc}")

        self._seen_ids.add(msg_id)

    # ── Poll cycle ────────────────────────────────────────────────────────────

    def _poll_once(self) -> None:
        """Run a single fetch-and-process cycle."""
        mode = "IMPORTANT + UNREAD" if IMPORTANT_ONLY else "ALL UNREAD"
        self.log(f"Checking Gmail ({mode})...")
        try:
            messages = self._fetch_unread()
            if not messages:
                self.log("No new messages.")
                return
            self.log(f"Found {len(messages)} new message(s). Processing...")
            for msg in messages:
                try:
                    self.process(msg)
                except Exception as exc:
                    self.on_error(exc)
        except HttpError as exc:
            self.log(f"Gmail API error: {exc}")
        except Exception as exc:
            self.log(f"Unexpected error: {exc}")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not _GMAIL_AVAILABLE:
            raise ImportError(
                "Gmail packages not installed.\n"
                "Run: pip install google-api-python-client google-auth google-auth-oauthlib"
            )

        self.print_banner([
            f"Mode          : {'IMPORTANT emails only' if IMPORTANT_ONLY else 'All unread emails'}",
            f"Poll interval : every {POLL_INTERVAL}s",
            f"Tasks saved to: AI_Employee_Vault/Needs_Action/",
            "Press Ctrl+C to stop.",
        ])
        self.on_start()

        try:
            self._service = self._authenticate()
            self.log("Gmail authenticated successfully.")
        except Exception as exc:
            self.log(f"Authentication failed: {exc}")
            return

        self._running = True
        try:
            while self._running:
                self._poll_once()
                self.log(f"Sleeping {POLL_INTERVAL}s until next check...")
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            self.log("Shutdown signal received.")
        finally:
            self.stop()

    def stop(self) -> None:
        self._running = False
        self.on_stop()
        self.log("GmailWatcher stopped.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    GmailWatcher().start()
