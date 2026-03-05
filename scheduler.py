"""
scheduler.py
────────────
Silver Tier — Personal AI Employee

Runs on a fixed interval (default: 60 minutes) and:
  1. Scans all vault folders for task counts
  2. Lists each pending / in-progress task by name
  3. Rewrites AI_Employee_Vault/Dashboard.md with a full summary report

Run standalone:
    python scheduler.py

Or import and start in a background thread:
    from scheduler import DashboardScheduler
    DashboardScheduler().start_background()

Configuration via environment variables:
    SCHEDULER_INTERVAL_MINUTES   (default: 60)
"""

import io
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Optional: weekly report generator ─────────────────────────────────────────
try:
    from report_generator import ReportGenerator as _ReportGenerator
    _REPORT_AVAILABLE = True
except ImportError:
    _REPORT_AVAILABLE = False

# ── Optional: CEO briefing ─────────────────────────────────────────────────────
try:
    from ceo_briefing import CEOBriefing as _CEOBriefing
    _BRIEFING_AVAILABLE = True
except ImportError:
    _BRIEFING_AVAILABLE = False

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Vault paths ───────────────────────────────────────────────────────────────
VAULT_DIR        = Path(__file__).resolve().parent / "AI_Employee_Vault"
INBOX            = VAULT_DIR / "Inbox"
NEEDS_ACTION     = VAULT_DIR / "Needs_Action"
PLANS            = VAULT_DIR / "Plans"
PENDING_APPROVAL = VAULT_DIR / "Pending_Approval"
APPROVED         = VAULT_DIR / "Approved"
REJECTED         = VAULT_DIR / "Rejected"
DONE             = VAULT_DIR / "Done"
DASHBOARD        = VAULT_DIR / "Dashboard.md"

# ── Config ────────────────────────────────────────────────────────────────────
INTERVAL_MINUTES        = int(os.environ.get("SCHEDULER_INTERVAL_MINUTES", "60"))
INTERVAL_SECONDS        = INTERVAL_MINUTES * 60
WEEKLY_REPORT_INTERVAL  = 7 * 24 * 60 * 60   # 7 days in seconds


# ── Helpers ───────────────────────────────────────────────────────────────────

def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def log(msg: str) -> None:
    print(f"[{now_str()}]  [Scheduler]  {msg}", flush=True)


def _task_files(folder: Path) -> list[Path]:
    """Return all non-hidden, non-temp files inside *folder*."""
    if not folder.exists():
        return []
    return sorted(
        f for f in folder.iterdir()
        if f.is_file()
        and not f.name.startswith(".")
        and f.suffix.lower() not in {".tmp", ".part", ".swp", ".bak"}
    )


def _file_list_md(files: list[Path], indent: str = "  ") -> str:
    """Render a markdown bullet list of filenames, or a placeholder if empty."""
    if not files:
        return f"{indent}_None_"
    return "\n".join(f"{indent}- `{f.name}`" for f in files)


def _oldest_file(files: list[Path]) -> str:
    """Return the age in hours of the oldest file, or 'N/A'."""
    if not files:
        return "N/A"
    oldest_mtime = min(f.stat().st_mtime for f in files)
    age_hours = (time.time() - oldest_mtime) / 3600
    if age_hours < 1:
        return f"{int(age_hours * 60)} min"
    return f"{age_hours:.1f} hrs"


# ── Report generator ──────────────────────────────────────────────────────────

def collect_stats() -> dict:
    """Scan every vault folder and return a stats dictionary."""
    needs_action_files     = _task_files(NEEDS_ACTION)
    plans_files            = _task_files(PLANS)
    pending_approval_files = _task_files(PENDING_APPROVAL)
    done_files             = _task_files(DONE)
    rejected_files         = _task_files(REJECTED)
    approved_files         = _task_files(APPROVED)
    inbox_files            = _task_files(INBOX)

    return {
        "inbox":            inbox_files,
        "needs_action":     needs_action_files,
        "plans":            plans_files,
        "pending_approval": pending_approval_files,
        "approved":         approved_files,
        "rejected":         rejected_files,
        "done":             done_files,
        # Summary counts
        "total":     (
            len(inbox_files) + len(needs_action_files) + len(plans_files)
            + len(pending_approval_files) + len(done_files)
            + len(rejected_files) + len(approved_files)
        ),
        "pending":   len(needs_action_files) + len(plans_files) + len(pending_approval_files),
        "completed": len(done_files),
    }


def write_dashboard(stats: dict) -> None:
    """Build and write Dashboard.md, preserving the activity log if it exists."""
    generated_at = now_str()

    # ── Preserve any existing activity log rows ───────────────────────────────
    existing_log_rows = ""
    if DASHBOARD.exists():
        existing = DASHBOARD.read_text(encoding="utf-8")
        import re
        # Extract everything after the activity log table header
        log_match = re.search(
            r"\| Timestamp \| Event \| File \|\n\|[-| ]+\|\n(.*)",
            existing,
            re.DOTALL,
        )
        if log_match:
            existing_log_rows = log_match.group(1).rstrip("\n")

    # ── Health / status line ──────────────────────────────────────────────────
    if stats["pending"] == 0 and not stats["inbox"]:
        health = "All clear — no pending tasks."
        health_icon = "OK"
    elif stats["pending_approval"]:
        health = f"{len(stats['pending_approval'])} task(s) awaiting human approval."
        health_icon = "REVIEW"
    elif stats["pending"] > 5:
        health = f"High workload — {stats['pending']} tasks pending."
        health_icon = "BUSY"
    else:
        health = f"{stats['pending']} task(s) in progress."
        health_icon = "ACTIVE"

    oldest_pending = _oldest_file(stats["needs_action"] + stats["plans"])

    content = (
        "# AI Employee Dashboard\n"
        "> Auto-generated by the scheduler. Do not edit manually.\n"
        "\n"
        "---\n"
        "\n"
        "## Summary\n"
        "\n"
        "| Metric | Value |\n"
        "|--------|-------|\n"
        f"| Pending Tasks | {stats['pending']} |\n"
        f"| Plans Created | {len(stats['plans'])} |\n"
        f"| Completed Tasks | {stats['completed']} |\n"
        f"| Rejected Tasks | {len(stats['rejected'])} |\n"
        f"| Awaiting Approval | {len(stats['pending_approval'])} |\n"
        f"| Oldest Pending | {oldest_pending} |\n"
        f"| System Status | {health_icon} |\n"
        f"| Last Updated | {generated_at} |\n"
        "\n"
        "---\n"
        "\n"
        "## Health\n"
        "\n"
        f"{health}\n"
        "\n"
        "---\n"
        "\n"
        "## Needs Action\n"
        f"> {len(stats['needs_action'])} task(s) require immediate attention.\n"
        "\n"
        f"{_file_list_md(stats['needs_action'])}\n"
        "\n"
        "---\n"
        "\n"
        "## Plans\n"
        f"> {len(stats['plans'])} plan(s) created and waiting to be executed.\n"
        "\n"
        f"{_file_list_md(stats['plans'])}\n"
        "\n"
        "---\n"
        "\n"
        "## Awaiting Human Approval\n"
        f"> {len(stats['pending_approval'])} task(s) need a human decision.\n"
        "\n"
        f"{_file_list_md(stats['pending_approval'])}\n"
        "\n"
        "---\n"
        "\n"
        "## Completed Tasks\n"
        f"> {stats['completed']} task(s) successfully processed.\n"
        "\n"
        f"{_file_list_md(stats['done'])}\n"
        "\n"
        "---\n"
        "\n"
        "## Rejected Tasks\n"
        f"> {len(stats['rejected'])} task(s) were rejected.\n"
        "\n"
        f"{_file_list_md(stats['rejected'])}\n"
        "\n"
        "---\n"
        "\n"
        "## Recent Activity Log\n"
        "\n"
        "| Timestamp | Event | File |\n"
        "|-----------|-------|------|\n"
    )

    # Re-attach the preserved log rows
    if existing_log_rows:
        content += existing_log_rows + "\n"

    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    DASHBOARD.write_text(content, encoding="utf-8")
    log(f"Dashboard updated → {DASHBOARD}")


# ── Scheduler class ───────────────────────────────────────────────────────────

class DashboardScheduler:
    """
    Periodically rebuilds Dashboard.md from the live vault folder counts.

    Usage — blocking (standalone):
        DashboardScheduler().start()

    Usage — background thread (alongside filesystem_watcher):
        DashboardScheduler().start_background()
    """

    def __init__(self, interval_seconds: int = INTERVAL_SECONDS):
        self.interval  = interval_seconds
        self._running  = False
        self._thread: threading.Thread | None = None
        self._last_report_time: float = 0.0   # epoch seconds of last weekly report

    def run_once(self) -> None:
        """Perform a single scan-and-write cycle (dashboard + optional weekly report)."""
        log("Running scheduled dashboard update...")
        try:
            stats = collect_stats()
            write_dashboard(stats)
            log(
                f"Done. Pending={stats['pending']}  "
                f"Plans={len(stats['plans'])}  "
                f"Completed={stats['completed']}  "
                f"Rejected={len(stats['rejected'])}"
            )
        except Exception as exc:
            log(f"ERROR during dashboard update: {exc}")

        # ── Weekly report + CEO briefing (every 7 days) ───────────────────────
        now = time.time()
        if now - self._last_report_time >= WEEKLY_REPORT_INTERVAL:
            if _REPORT_AVAILABLE:
                log("Generating weekly productivity report...")
                try:
                    path = _ReportGenerator().run()
                    log(f"Weekly report saved -> {path.name}")
                except Exception as exc:
                    log(f"ERROR generating weekly report: {exc}")

            if _BRIEFING_AVAILABLE:
                log("Generating CEO briefing...")
                try:
                    path = _CEOBriefing().run()
                    log(f"CEO briefing saved -> {path.name}")
                except Exception as exc:
                    log(f"ERROR generating CEO briefing: {exc}")

            self._last_report_time = now

    def start(self) -> None:
        """
        Blocking run loop. Runs once immediately, then every self.interval seconds.
        Stop with Ctrl+C.
        """
        self._running = True
        print("=" * 55)
        print("   Personal AI Employee — Dashboard Scheduler")
        print(f"   Interval : every {self.interval // 60} minute(s)")
        print(f"   Dashboard: {DASHBOARD}")
        print(f"   Started  : {now_str()}")
        print("=" * 55 + "\n")

        try:
            while self._running:
                self.run_once()
                log(f"Next update in {self.interval // 60} minute(s). Sleeping...")
                time.sleep(self.interval)
        except KeyboardInterrupt:
            log("Scheduler stopped by user.")
        finally:
            self._running = False

    def start_background(self) -> threading.Thread:
        """
        Start the scheduler in a daemon background thread.
        Returns the thread so the caller can join if needed.
        """
        self._thread = threading.Thread(
            target=self.start,
            name="DashboardScheduler",
            daemon=True,
        )
        self._thread.start()
        log(f"Scheduler started in background thread (interval={self.interval}s).")
        return self._thread

    def stop(self) -> None:
        """Signal the scheduler to stop after the current sleep."""
        self._running = False
        log("Scheduler stop requested.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    DashboardScheduler().start()
