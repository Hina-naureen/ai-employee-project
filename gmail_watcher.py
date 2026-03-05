"""
gmail_watcher.py
────────────────
Silver Tier — Personal AI Employee

Polls Gmail for unread messages, converts each one into a structured
Markdown task file, and saves it to AI_Employee_Vault/Needs_Action/.

Setup (one-time):
  1. Go to https://console.cloud.google.com/
  2. Create a project → Enable the Gmail API
  3. OAuth consent screen → Add your Gmail as a test user
  4. Credentials → Create OAuth 2.0 Client ID (Desktop app)
  5. Download JSON → save as  credentials.json  in this folder
  6. Run this file once — a browser window will open for sign-in
  7. token.json will be created automatically for future runs

Dependencies:
    pip install google-api-python-client google-auth google-auth-oauthlib

Run:
    python gmail_watcher.py
"""

import os
import re
import time
import base64
import textwrap
from pathlib import Path

# ── Gmail / Google Auth imports ───────────────────────────────────────────────
try:
    from googleapiclient.discovery import build
    from googleapiclient.errors   import HttpError
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    _GMAIL_AVAILABLE = True
except ImportError:
    _GMAIL_AVAILABLE = False

from base_watcher import BaseWatcher, NEEDS_ACTION

# ── Config ────────────────────────────────────────────────────────────────────
CREDENTIALS_FILE = Path(__file__).resolve().parent / "credentials.json"
TOKEN_FILE       = Path(__file__).resolve().parent / "token.json"

# Only request read + modify (no send/delete permissions)
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# How often to check for new mail (seconds)
POLL_INTERVAL = int(os.environ.get("GMAIL_POLL_INTERVAL", "60"))

# Max messages to fetch per poll cycle
MAX_RESULTS = int(os.environ.get("GMAIL_MAX_RESULTS", "10"))

# Keywords that raise priority to "high"
HIGH_PRIORITY_KW = {
    "urgent", "asap", "immediately", "critical", "emergency",
    "action required", "important", "deadline", "overdue", "today",
}

# Suggested actions mapped to content keywords
_ACTION_RULES: list[tuple[set, str]] = [
    ({"question", "?", "please reply", "let me know", "feedback", "response"},
     "Reply to sender"),
    ({"invoice", "payment", "billing", "receipt", "purchase"},
     "Review payment / forward to finance"),
    ({"meeting", "calendar", "schedule", "invite", "zoom", "teams"},
     "Accept / decline meeting invite"),
    ({"report", "document", "attached", "attachment", "file"},
     "Review attached document"),
    ({"deadline", "due", "overdue", "reminder"},
     "Check deadline and take action"),
    ({"unsubscribe", "newsletter", "promotion"},
     "Unsubscribe or archive"),
]
_DEFAULT_ACTIONS = ["Reply", "Archive", "Follow up"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_priority(subject: str, snippet: str) -> str:
    """Return 'high' if any high-priority keyword is found, else 'normal'."""
    corpus = (subject + " " + snippet).lower()
    return "high" if any(kw in corpus for kw in HIGH_PRIORITY_KW) else "normal"


def _suggest_actions(subject: str, snippet: str) -> list[str]:
    """Return a list of contextually relevant suggested actions."""
    corpus = (subject + " " + snippet).lower()
    actions = []
    for keywords, action in _ACTION_RULES:
        if any(kw in corpus for kw in keywords):
            actions.append(action)
    return actions if actions else _DEFAULT_ACTIONS


def _safe_filename(text: str, max_len: int = 50) -> str:
    """
    Convert arbitrary text to a safe, snake_case filename fragment.
    Strips special characters and truncates.
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)          # remove punctuation
    text = re.sub(r"[\s_-]+", "_", text)           # spaces → underscores
    text = re.sub(r"^_+|_+$", "", text)            # strip leading/trailing _
    return text[:max_len] or "email"


def _decode_body(payload: dict) -> str:
    """
    Extract plain-text body from a Gmail message payload.
    Handles simple messages and multipart/alternative.
    """
    mime = payload.get("mimeType", "")

    # Simple plain-text part
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    # Multipart — recurse into parts
    if mime.startswith("multipart"):
        for part in payload.get("parts", []):
            result = _decode_body(part)
            if result:
                return result

    return ""


def _build_markdown(
    msg_id: str,
    sender: str,
    subject: str,
    date: str,
    body: str,
    snippet: str,
    priority: str,
    actions: list[str],
) -> str:
    """Render a Gmail message as a Markdown task file."""
    # Use snippet as body fallback; truncate long bodies
    display_body = (body.strip() or snippet.strip())[:1200]
    # Wrap long lines for readability
    display_body = textwrap.fill(display_body, width=100) if display_body else "(no content)"

    action_lines = "\n".join(f"- {a}" for a in actions)

    return (
        f"---\n"
        f"type: email\n"
        f"from: {sender}\n"
        f"subject: {subject}\n"
        f"date: {date}\n"
        f"priority: {priority}\n"
        f"status: pending\n"
        f"gmail_id: {msg_id}\n"
        f"---\n"
        f"\n"
        f"## Email Content\n"
        f"\n"
        f"{display_body}\n"
        f"\n"
        f"## Suggested Actions\n"
        f"\n"
        f"{action_lines}\n"
    )


# ── Main watcher class ────────────────────────────────────────────────────────

class GmailWatcher(BaseWatcher):
    """
    Polls Gmail for unread messages and converts each one into a structured
    Markdown task file saved to AI_Employee_Vault/Needs_Action/.
    """

    def __init__(self):
        super().__init__(name="GmailWatcher")
        self._service  = None
        self._running  = False
        self._seen_ids: set[str] = set()   # avoid reprocessing in same session

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _authenticate(self):
        """
        Run the OAuth2 flow and return an authorised Gmail service client.
        On first run a browser window opens for sign-in.
        On subsequent runs token.json is used automatically.
        """
        if not CREDENTIALS_FILE.exists():
            raise FileNotFoundError(
                f"credentials.json not found at {CREDENTIALS_FILE}\n"
                "  1. Go to https://console.cloud.google.com/\n"
                "  2. Enable Gmail API -> Create OAuth 2.0 Client ID (Desktop)\n"
                "  3. Download the JSON -> rename to credentials.json\n"
                "  4. Place it in the project root folder.\n"
            )

        creds = None
        if TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                self.log("Refreshing access token...")
                creds.refresh(Request())
            else:
                self.log("Opening browser for Gmail sign-in...")
                flow  = InstalledAppFlow.from_client_secrets_file(
                    str(CREDENTIALS_FILE), SCOPES
                )
                creds = flow.run_local_server(port=0)

            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            self.log(f"Token saved -> {TOKEN_FILE.name}")

        return build("gmail", "v1", credentials=creds)

    # ── Fetching ──────────────────────────────────────────────────────────────

    def _fetch_unread(self) -> list[dict]:
        """Return a list of full message dicts for unread Inbox messages."""
        results = (
            self._service.users()
            .messages()
            .list(
                userId="me",
                labelIds=["INBOX", "UNREAD"],
                maxResults=MAX_RESULTS,
            )
            .execute()
        )
        messages = results.get("messages", [])
        full_msgs = []
        for msg in messages:
            if msg["id"] in self._seen_ids:
                continue
            full = (
                self._service.users()
                .messages()
                .get(userId="me", id=msg["id"], format="full")
                .execute()
            )
            full_msgs.append(full)
        return full_msgs

    def _extract_header(self, headers: list[dict], name: str) -> str:
        """Pull a single header value by name (case-insensitive)."""
        for h in headers:
            if h["name"].lower() == name.lower():
                return h["value"]
        return ""

    # ── Processing ────────────────────────────────────────────────────────────

    def process(self, message: dict) -> None:
        """
        Convert a single Gmail message dict into a Markdown task file
        and save it to Needs_Action/.
        """
        msg_id  = message["id"]
        payload = message.get("payload", {})
        headers = payload.get("headers", [])
        snippet = message.get("snippet", "")

        sender  = self._extract_header(headers, "From")
        subject = self._extract_header(headers, "Subject") or "(no subject)"
        date    = self._extract_header(headers, "Date")    or self.now_str()
        body    = _decode_body(payload)

        priority = _detect_priority(subject, snippet)
        actions  = _suggest_actions(subject, snippet)

        # Build filename:  email_<safe-subject>_<msg-id-prefix>.md
        safe_sub  = _safe_filename(subject)
        filename  = f"email_{safe_sub}_{msg_id[:8]}.md"
        dest_path = NEEDS_ACTION / filename

        content = _build_markdown(
            msg_id=msg_id,
            sender=sender,
            subject=subject,
            date=date,
            body=body,
            snippet=snippet,
            priority=priority,
            actions=actions,
        )

        saved = self.safe_write(dest_path, content)
        self.log(f"Saved task -> {saved.name}  (priority: {priority})")

        # Mark message as read so it is not re-fetched next cycle
        self._service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()

        self._seen_ids.add(msg_id)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Authenticate, then poll Gmail on a fixed interval."""
        if not _GMAIL_AVAILABLE:
            raise ImportError(
                "Gmail dependencies not installed.\n"
                "Run:  pip install google-api-python-client google-auth google-auth-oauthlib"
            )

        self.print_banner([
            f"Checking Gmail every {POLL_INTERVAL}s",
            f"Saving tasks to: Needs_Action/",
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

    def _poll_once(self) -> None:
        """Run a single fetch-and-process cycle."""
        self.log("Checking for unread messages...")
        try:
            messages = self._fetch_unread()
            if not messages:
                self.log("No new unread messages.")
                return
            self.log(f"Found {len(messages)} unread message(s). Processing...")
            for msg in messages:
                try:
                    self.process(msg)
                except Exception as exc:
                    self.on_error(exc)
        except HttpError as exc:
            self.log(f"Gmail API error: {exc}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    watcher = GmailWatcher()
    watcher.start()
