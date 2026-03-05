"""
audit_logger.py
────────────────
Gold Tier — Personal AI Employee

Thread-safe audit logger.  Records every AI action to:
    AI_Employee_Vault/Audit_Log.md

Import and use from any module:
    from audit_logger import log_action

    log_action("Task analyzed", "urgent_task.txt", "Needs_Action")
    log_action("Plan generated", "PLAN_foo.md", "Plans", extra="3 steps")
    log_action("Task moved", "foo.txt", "Done")
"""

import io
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Paths ──────────────────────────────────────────────────────────────────────
VAULT_DIR  = Path(__file__).resolve().parent / "AI_Employee_Vault"
AUDIT_FILE = VAULT_DIR / "Audit_Log.md"

# ── Thread safety ──────────────────────────────────────────────────────────────
_lock = threading.Lock()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _init_log() -> None:
    """Create Audit_Log.md with a header if it does not exist yet."""
    if not AUDIT_FILE.exists():
        VAULT_DIR.mkdir(parents=True, exist_ok=True)
        AUDIT_FILE.write_text(
            "# AI Employee — Audit Log\n"
            "> Every action taken by the AI Employee is recorded here.\n"
            "> Do not edit manually.\n"
            "\n"
            "---\n"
            "\n",
            encoding="utf-8",
        )


# ── Public API ─────────────────────────────────────────────────────────────────

def log_action(
    action: str,
    file: str,
    result: str,
    extra: str | None = None,
    source: str = "AI Employee",
) -> None:
    """
    Append one audit entry to Audit_Log.md.

    Parameters
    ----------
    action  : what happened          e.g. "Task analyzed"
    file    : the file involved       e.g. "urgent_task.txt"
    result  : the outcome / destination e.g. "Needs_Action"
    extra   : optional detail line    e.g. "priority: high"
    source  : which module logged it  e.g. "RalphLoop" (default "AI Employee")
    """
    with _lock:
        _init_log()
        extra_line = f"Extra: {extra}\n" if extra else ""
        entry = (
            f"[{_now_str()}]\n"
            f"Action: {action}\n"
            f"File: {file}\n"
            f"Result: {result}\n"
            f"{extra_line}"
            f"Source: {source}\n"
            f"\n"
            f"---\n"
            f"\n"
        )
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(entry)


def log_system(event: str, source: str = "System") -> None:
    """
    Log a system-level event (startup, shutdown, error) with no file context.
    """
    with _lock:
        _init_log()
        entry = (
            f"[{_now_str()}]\n"
            f"Event: {event}\n"
            f"Source: {source}\n"
            f"\n"
            f"---\n"
            f"\n"
        )
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(entry)


def get_log_path() -> Path:
    """Return the absolute path to the audit log file."""
    return AUDIT_FILE


# ── Standalone test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Audit log: {AUDIT_FILE}")
    log_system("Audit logger initialised", source="audit_logger.py")
    log_action("Task analyzed",   "urgent_task.txt",  "Needs_Action", source="test")
    log_action("Plan generated",  "PLAN_sample.md",   "Plans",        extra="3 steps", source="test")
    log_action("Task moved",      "sample_task.md",   "Done",         source="test")
    print(f"Written 3 test entries to {AUDIT_FILE}")
    print(AUDIT_FILE.read_text(encoding="utf-8"))
