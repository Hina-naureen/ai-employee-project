"""
local_agent.py
───────────────
Platinum Tier — Personal AI Employee

Local-side agent.  Handles tasks that require physical presence or
human oversight:

    • HITL approvals  — polls Pending_Approval/ and displays pending items
    • Stale task detection — flags In_Progress/ tasks idle for >1 hour
    • Payment summaries   — surfaces payment-related tasks for quick review
    • Rejection analysis  — logs why tasks were rejected

Designed to run on the user's local machine while cloud_agent runs
on a VM / cloud server.

Run standalone:
    python local_agent.py

Or start via orchestrator (recommended):
    from local_agent import LocalAgent
    LocalAgent().start()

Environment variables (all optional):
    LOCAL_POLL_INTERVAL    seconds between checks      (default: 30)
    LOCAL_STALE_HOURS      hours before task is stale  (default: 1)
"""

import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Vault paths ────────────────────────────────────────────────────────────────
VAULT_DIR        = Path(__file__).resolve().parent / "AI_Employee_Vault"
NEEDS_ACTION     = VAULT_DIR / "Needs_Action"
IN_PROGRESS      = VAULT_DIR / "In_Progress"
PENDING_APPROVAL = VAULT_DIR / "Pending_Approval"
APPROVED         = VAULT_DIR / "Approved"
REJECTED         = VAULT_DIR / "Rejected"
DONE             = VAULT_DIR / "Done"
LOGS_DIR         = VAULT_DIR / "Logs"

# ── Config ─────────────────────────────────────────────────────────────────────
POLL_INTERVAL = int(os.environ.get("LOCAL_POLL_INTERVAL", "30"))
STALE_HOURS   = float(os.environ.get("LOCAL_STALE_HOURS", "1"))

from audit_logger import log_action, log_system
from base_watcher import BaseWatcher


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _task_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(
        f for f in folder.iterdir()
        if f.is_file()
        and not f.name.startswith(".")
        and f.suffix.lower() not in {".tmp", ".part", ".swp", ".bak"}
    )


def _age_hours(f: Path) -> float:
    return (time.time() - f.stat().st_mtime) / 3600


def _read_yaml_field(text: str, field: str) -> str:
    match = re.search(rf"^{re.escape(field)}:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _humanise(filename: str) -> str:
    name = filename
    for prefix in ("PLAN_", "PENDING_", "PAY_", "URGENT_", "email_", "whatsapp_"):
        name = name.removeprefix(prefix)
    name = Path(name).stem
    return re.sub(r"_+", " ", name).strip().title()


def _write_platinum_log(msg: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / "Audit_Log.md"
    if not log_file.exists():
        log_file.write_text(
            "# Platinum Tier — Audit Log\n"
            "> Structured log of all Platinum agent actions.\n\n---\n\n",
            encoding="utf-8",
        )
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{ts}]  {msg}\n")


# ── Approval display ───────────────────────────────────────────────────────────

def _format_approval_summary(task_file: Path) -> str:
    """Return a human-readable summary of one pending approval file."""
    try:
        text = task_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return f"  [unreadable] {task_file.name}"

    action    = _read_yaml_field(text, "action")    or "unknown action"
    recipient = _read_yaml_field(text, "recipient") or "unknown"
    reason    = _read_yaml_field(text, "reason")    or "sensitive task"
    original  = _read_yaml_field(text, "original_file") or task_file.name
    requested = _read_yaml_field(text, "requested_at") or "unknown"
    age       = _age_hours(task_file)

    return (
        f"  File     : {task_file.name}\n"
        f"  Original : {original}\n"
        f"  Action   : {action}\n"
        f"  Recipient: {recipient}\n"
        f"  Reason   : {reason}\n"
        f"  Requested: {requested}\n"
        f"  Age      : {age:.1f} hrs\n"
        f"  Decision : Move to Approved/ or Rejected/"
    )


# ── Stale task detection ───────────────────────────────────────────────────────

def _check_stale_tasks(log_fn) -> list[Path]:
    """Return tasks in In_Progress/ that have been there > STALE_HOURS."""
    stale = []
    for f in _task_files(IN_PROGRESS):
        if _age_hours(f) > STALE_HOURS:
            stale.append(f)
            log_fn(f"  [STALE] {f.name} has been In_Progress for {_age_hours(f):.1f} hrs")
            _write_platinum_log(f"[LocalAgent] STALE task detected: {f.name} ({_age_hours(f):.1f} hrs)")
    return stale


# ── Payment surface ────────────────────────────────────────────────────────────

def _surface_payment_tasks(log_fn) -> None:
    """Log a summary of payment-related tasks currently pending approval."""
    payment_kw = {"payment", "invoice", "billing", "pay", "receipt", "vendor"}
    payment_tasks = [
        f for f in _task_files(PENDING_APPROVAL)
        if any(kw in (f.read_text(encoding="utf-8", errors="ignore")
                      + f.name).lower() for kw in payment_kw)
    ]
    if payment_tasks:
        log_fn(f"  [PAYMENT] {len(payment_tasks)} payment task(s) awaiting approval:")
        for f in payment_tasks:
            log_fn(f"    - {f.name}")
            _write_platinum_log(f"[LocalAgent] Payment task pending: {f.name}")


# ── Main agent class ───────────────────────────────────────────────────────────

class LocalAgent(BaseWatcher):
    """
    Platinum Tier local-side agent.

    Monitors Pending_Approval/ and In_Progress/, surfaces issues
    that require human attention, and logs all activity.
    """

    def __init__(self):
        super().__init__(name="LocalAgent")
        self._running           = False
        self._last_approval_set: set[str] = set()
        self._notified_stale:    set[str] = set()

    def process(self, item) -> None:
        """Not used directly — polling loop handles all processing."""
        pass

    def _poll_approvals(self) -> None:
        """Check for new items in Pending_Approval/ and print a summary."""
        pending = _task_files(PENDING_APPROVAL)
        current_names = {f.name for f in pending}
        new_items     = current_names - self._last_approval_set

        if new_items:
            self.log(f"NEW approval request(s) detected: {len(new_items)}")
            print()
            print("=" * 55)
            print("  !! HUMAN ACTION REQUIRED — Pending Approvals !!")
            print("=" * 55)
            for f in pending:
                if f.name in new_items:
                    print()
                    print(_format_approval_summary(f))
                    print()
                    log_action(
                        action="Approval request surfaced",
                        file=f.name,
                        result="Pending_Approval",
                        source="LocalAgent",
                    )
                    _write_platinum_log(f"[LocalAgent] Approval surfaced: {f.name}")
            print("=" * 55)
            print()

        if pending and not new_items:
            # Remind about outstanding items (log only, no print spam)
            self.log(
                f"{len(pending)} approval(s) still waiting: "
                + ", ".join(f.name for f in pending[:3])
                + (" ..." if len(pending) > 3 else "")
            )

        self._last_approval_set = current_names

    def _poll_stale(self) -> None:
        """Check In_Progress/ for tasks stuck longer than STALE_HOURS."""
        for f in _task_files(IN_PROGRESS):
            if f.name in self._notified_stale:
                continue
            if _age_hours(f) > STALE_HOURS:
                self._notified_stale.add(f.name)
                self.log(
                    f"[STALE ALERT] '{f.name}' has been In_Progress "
                    f"for {_age_hours(f):.1f} hrs — may be stuck."
                )
                log_action(
                    action="Stale task detected",
                    file=f.name,
                    result=f"In_Progress ({_age_hours(f):.1f} hrs)",
                    source="LocalAgent",
                )
                _write_platinum_log(
                    f"[LocalAgent] STALE: {f.name} stuck in In_Progress for {_age_hours(f):.1f} hrs"
                )

    def _poll_rejections(self) -> None:
        """Log any newly arrived files in Rejected/ for audit."""
        for f in _task_files(REJECTED):
            key = f"rejected::{f.name}"
            if key not in self._last_approval_set:
                self._last_approval_set.add(key)
                self.log(f"[REJECTED] Task archived: {f.name}")
                _write_platinum_log(f"[LocalAgent] Task rejected: {f.name}")

    def _poll_once(self) -> None:
        self._poll_approvals()
        self._poll_stale()
        _surface_payment_tasks(self.log)
        self._poll_rejections()

    def start(self) -> None:
        for folder in (IN_PROGRESS, LOGS_DIR, PENDING_APPROVAL):
            folder.mkdir(parents=True, exist_ok=True)

        self.print_banner([
            f"Poll interval  : every {POLL_INTERVAL}s",
            f"Stale threshold: {STALE_HOURS} hr(s)",
            f"Pending_Approval: {PENDING_APPROVAL}",
            f"In_Progress    : {IN_PROGRESS}",
            "Press Ctrl+C to stop.",
        ])
        log_system("LocalAgent started", source="LocalAgent")
        _write_platinum_log("[LocalAgent] Started")
        self._running = True

        try:
            while self._running:
                self._poll_once()
                self.log(f"Sleeping {POLL_INTERVAL}s...")
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            self.log("Shutdown signal received.")
        finally:
            self.stop()

    def stop(self) -> None:
        self._running = False
        log_system("LocalAgent stopped", source="LocalAgent")
        _write_platinum_log("[LocalAgent] Stopped")
        self.on_stop()
        self.log("LocalAgent stopped.")


if __name__ == "__main__":
    LocalAgent().start()
