"""
base_watcher.py
───────────────
Silver Tier — Personal AI Employee

Abstract base class that every watcher must implement.
Provides shared utilities: logging, vault path resolution,
safe file writing, and a standard run-loop interface.

All watchers (FilesystemWatcher, GmailWatcher, etc.) inherit from BaseWatcher.
"""

import io
import sys
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Vault root (shared by all watchers) ──────────────────────────────────────
VAULT_DIR        = Path(__file__).resolve().parent / "AI_Employee_Vault"
INBOX            = VAULT_DIR / "Inbox"
NEEDS_ACTION     = VAULT_DIR / "Needs_Action"
PLANS            = VAULT_DIR / "Plans"
PENDING_APPROVAL = VAULT_DIR / "Pending_Approval"
APPROVED         = VAULT_DIR / "Approved"
REJECTED         = VAULT_DIR / "Rejected"
DONE             = VAULT_DIR / "Done"
DASHBOARD        = VAULT_DIR / "Dashboard.md"


class BaseWatcher(ABC):
    """
    Abstract base class for all Personal AI Employee watchers.

    Subclasses must implement:
        start()   — begin watching / polling
        stop()    — gracefully shut down
        process() — handle a single incoming item

    Shared utilities available to all subclasses:
        self.log(msg)
        self.ensure_vault_dirs()
        self.safe_write(path, content)
        self.now_str()
    """

    def __init__(self, name: str = "BaseWatcher"):
        self.name       = name
        self._running   = False
        self._lock      = threading.Lock()
        self.ensure_vault_dirs()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @abstractmethod
    def start(self) -> None:
        """Start the watcher. Should block (or start a background thread)."""

    @abstractmethod
    def stop(self) -> None:
        """Signal the watcher to stop cleanly."""

    @abstractmethod
    def process(self, item) -> None:
        """
        Process a single incoming item (file path, email dict, etc.).
        Called internally by the watcher's polling / event loop.
        """

    # ── Shared utilities ──────────────────────────────────────────────────────

    def log(self, message: str) -> None:
        """Print a timestamped, prefixed log line."""
        print(f"[{self.now_str()}]  [{self.name}]  {message}", flush=True)

    @staticmethod
    def now_str() -> str:
        """Return a human-readable UTC timestamp."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    @staticmethod
    def ensure_vault_dirs() -> None:
        """Create all vault sub-folders if they do not already exist."""
        for folder in (
            INBOX, NEEDS_ACTION, PLANS,
            PENDING_APPROVAL, APPROVED, REJECTED, DONE,
        ):
            folder.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def safe_write(path: Path, content: str) -> Path:
        """
        Write *content* to *path*, avoiding collisions by appending a counter
        when the file already exists.  Returns the actual path written.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            stem, suffix = path.stem, path.suffix
            counter = 1
            while path.exists():
                path = path.parent / f"{stem}_{counter}{suffix}"
                counter += 1
        path.write_text(content, encoding="utf-8")
        return path

    # ── Optional hooks (subclasses may override) ──────────────────────────────

    def on_start(self) -> None:
        """Called once just before the watcher starts its loop."""

    def on_stop(self) -> None:
        """Called once just after the watcher stops its loop."""

    def on_error(self, error: Exception) -> None:
        """Called when an unhandled exception occurs inside process()."""
        self.log(f"ERROR — {error}")

    # ── Helper: print a banner ────────────────────────────────────────────────

    def print_banner(self, extra_lines: list[str] | None = None) -> None:
        """Print a startup banner to the terminal."""
        width = 55
        print("=" * width)
        print(f"   Personal AI Employee — {self.name}")
        print(f"   Silver Tier  |  Started: {self.now_str()}")
        if extra_lines:
            for line in extra_lines:
                print(f"   {line}")
        print("=" * width + "\n")
