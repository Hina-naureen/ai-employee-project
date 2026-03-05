"""
report_generator.py
────────────────────
Gold Tier — Personal AI Employee

Generates a comprehensive weekly productivity report by analysing all
task files across the vault.  The report covers:

  - Task throughput (completed / rejected / pending)
  - Priority breakdown (urgent vs normal)
  - Category breakdown (email, payment, meeting, etc.)
  - Top active days / busiest periods
  - LinkedIn post summary
  - Bottleneck detection (old pending tasks)
  - Actionable recommendations

Reports are saved to:
    AI_Employee_Vault/Reports/Weekly_Report_<YYYY-MM-DD>.md

Run standalone:
    python report_generator.py

Or call from the scheduler / other modules:
    from report_generator import ReportGenerator
    ReportGenerator().run()

Environment variables (all optional):
    REPORT_LOOKBACK_DAYS   days of history to include  (default: 7)
"""

import io
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Vault paths ────────────────────────────────────────────────────────────────
VAULT_DIR        = Path(__file__).resolve().parent / "AI_Employee_Vault"
INBOX            = VAULT_DIR / "Inbox"
NEEDS_ACTION     = VAULT_DIR / "Needs_Action"
PLANS            = VAULT_DIR / "Plans"
PENDING_APPROVAL = VAULT_DIR / "Pending_Approval"
APPROVED         = VAULT_DIR / "Approved"
REJECTED         = VAULT_DIR / "Rejected"
DONE             = VAULT_DIR / "Done"
SOCIAL_LOG       = VAULT_DIR / "Social_Posts.md"
REPORTS_DIR      = VAULT_DIR / "Reports"

# ── Config ─────────────────────────────────────────────────────────────────────
LOOKBACK_DAYS = int(os.environ.get("REPORT_LOOKBACK_DAYS", "7"))

# ── Category keyword detector ─────────────────────────────────────────────────
_CATEGORY_RULES: list[tuple[str, set]] = [
    ("Email",    {"email", "reply", "message", "inbox", "sender", "subject", "gmail_id"}),
    ("Payment",  {"payment", "invoice", "billing", "vendor", "pay", "receipt", "finance"}),
    ("Meeting",  {"meeting", "schedule", "calendar", "agenda", "sync", "standup", "invite"}),
    ("Strategy", {"strategy", "roadmap", "plan", "initiative", "objective", "goal", "proposal"}),
    ("Report",   {"report", "analysis", "data", "metrics", "kpi", "dashboard", "analytics"}),
    ("Code",     {"code", "build", "deploy", "feature", "fix", "bug", "develop", "release"}),
    ("Design",   {"design", "ui", "ux", "prototype", "mockup", "branding", "figma"}),
    ("Research", {"research", "investigate", "explore", "study", "gather", "survey"}),
    ("Document", {"document", "write", "policy", "procedure", "guide", "sop", "wiki"}),
]

# ── Priority keywords ─────────────────────────────────────────────────────────
_URGENT_KW = {"urgent", "critical", "asap", "immediately", "emergency", "priority: critical",
               "priority: high", "deadline", "today", "overdue"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def log(msg: str) -> None:
    print(f"[{now_str()}]  [Report]  {msg}", flush=True)


def _task_files(folder: Path) -> list[Path]:
    """Return all non-hidden task files inside *folder*."""
    if not folder.exists():
        return []
    return sorted(
        f for f in folder.iterdir()
        if f.is_file()
        and not f.name.startswith(".")
        and f.suffix.lower() not in {".tmp", ".part", ".swp", ".bak"}
    )


def _file_mtime_dt(f: Path) -> datetime:
    """Return the file's modification time as an aware UTC datetime."""
    return datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)


def _classify_category(text: str, filename: str) -> str:
    """Return the best-matching category label for a task file."""
    corpus = (text + " " + filename).lower()
    for category, keywords in _CATEGORY_RULES:
        if any(kw in corpus for kw in keywords):
            return category
    return "General"


def _is_urgent(text: str, filename: str) -> bool:
    corpus = (text + " " + filename).lower()
    return any(kw in corpus for kw in _URGENT_KW)


def _age_hours(f: Path) -> float:
    return (time.time() - f.stat().st_mtime) / 3600


def _count_social_posts() -> int:
    """Count the number of posts logged in Social_Posts.md."""
    if not SOCIAL_LOG.exists():
        return 0
    text = SOCIAL_LOG.read_text(encoding="utf-8", errors="ignore")
    return len(re.findall(r"^\*\*Status:\*\* Simulated", text, re.MULTILINE))


def _read_safe(f: Path) -> str:
    try:
        return f.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


# ── Core analytics ────────────────────────────────────────────────────────────

def analyse_vault(lookback_days: int = LOOKBACK_DAYS) -> dict:
    """
    Scan all vault folders and return a comprehensive stats dict.
    Files older than *lookback_days* are still counted in totals but
    flagged separately so the report can highlight bottlenecks.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    # Collect file lists
    done_files     = _task_files(DONE)
    rejected_files = _task_files(REJECTED)
    pending_files  = _task_files(NEEDS_ACTION) + _task_files(PLANS) + _task_files(PENDING_APPROVAL)
    all_files      = done_files + rejected_files + pending_files

    # ── Throughput ────────────────────────────────────────────────────────────
    completed_this_week  = [f for f in done_files     if _file_mtime_dt(f) >= cutoff]
    rejected_this_week   = [f for f in rejected_files if _file_mtime_dt(f) >= cutoff]

    # ── Priority ──────────────────────────────────────────────────────────────
    urgent_done    = sum(1 for f in done_files     if _is_urgent(_read_safe(f), f.name))
    urgent_pending = sum(1 for f in pending_files  if _is_urgent(_read_safe(f), f.name))

    # ── Category breakdown ────────────────────────────────────────────────────
    category_counter: Counter = Counter()
    for f in all_files:
        cat = _classify_category(_read_safe(f), f.name)
        category_counter[cat] += 1

    # ── Daily throughput (done files by day) ──────────────────────────────────
    daily_done: Counter = Counter()
    for f in done_files:
        day = _file_mtime_dt(f).strftime("%Y-%m-%d")
        daily_done[day] += 1

    # ── Bottleneck detection ──────────────────────────────────────────────────
    stale_threshold_hrs = 24
    stale_files = [f for f in pending_files if _age_hours(f) > stale_threshold_hrs]

    # ── Social posts ──────────────────────────────────────────────────────────
    social_count = _count_social_posts()

    # ── Plan files ────────────────────────────────────────────────────────────
    plan_files = _task_files(PLANS)

    return {
        "lookback_days":        lookback_days,
        "cutoff":               cutoff,
        "total_all_time":       len(all_files),
        "total_done":           len(done_files),
        "total_rejected":       len(rejected_files),
        "total_pending":        len(pending_files),
        "completed_this_week":  len(completed_this_week),
        "rejected_this_week":   len(rejected_this_week),
        "urgent_done":          urgent_done,
        "urgent_pending":       urgent_pending,
        "category_breakdown":   dict(category_counter.most_common()),
        "daily_done":           dict(sorted(daily_done.items())),
        "stale_files":          stale_files,
        "social_posts":         social_count,
        "plan_files":           len(plan_files),
        "pending_approval":     len(_task_files(PENDING_APPROVAL)),
        "approved":             len(_task_files(APPROVED)),
    }


# ── Recommendations engine ────────────────────────────────────────────────────

def _build_recommendations(stats: dict) -> list[str]:
    recs: list[str] = []

    if stats["urgent_pending"] > 0:
        recs.append(
            f"**{stats['urgent_pending']} urgent task(s) still pending** — "
            "resolve or escalate immediately."
        )
    if stats["stale_files"]:
        recs.append(
            f"**{len(stats['stale_files'])} task(s) have been sitting untouched for >24 hours** "
            "— review and process them."
        )
    if stats["pending_approval"] > 0:
        recs.append(
            f"**{stats['pending_approval']} task(s) awaiting human approval** — "
            "check the Pending_Approval folder."
        )
    if stats["total_pending"] > 10:
        recs.append(
            "**High task backlog detected.** Consider increasing processing frequency "
            "or delegating tasks."
        )
    completion_rate = (
        stats["completed_this_week"] /
        max(stats["completed_this_week"] + stats["rejected_this_week"], 1)
    ) * 100
    if completion_rate < 70:
        recs.append(
            f"**Completion rate this week is {completion_rate:.0f}%** — review rejection "
            "reasons and improve task quality at intake."
        )
    if stats["social_posts"] == 0:
        recs.append(
            "**No LinkedIn posts made yet.** Run `linkedin_poster.py` to auto-share your "
            "work updates."
        )
    if not recs:
        recs.append("All systems healthy — keep up the great work!")

    return recs


# ── Report builder ────────────────────────────────────────────────────────────

def build_report(stats: dict) -> str:
    """Compose the full Markdown report string."""
    generated_at = now_str()
    report_date  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    week_start   = (datetime.now(timezone.utc) - timedelta(days=stats["lookback_days"])).strftime("%Y-%m-%d")

    # ── Throughput section ────────────────────────────────────────────────────
    total_processed = stats["completed_this_week"] + stats["rejected_this_week"]
    completion_rate = (
        (stats["completed_this_week"] / total_processed * 100) if total_processed else 0
    )

    # ── Category table ────────────────────────────────────────────────────────
    if stats["category_breakdown"]:
        cat_rows = "\n".join(
            f"| {cat} | {count} |"
            for cat, count in stats["category_breakdown"].items()
        )
    else:
        cat_rows = "| — | — |"

    # ── Daily throughput table ─────────────────────────────────────────────────
    if stats["daily_done"]:
        daily_rows = "\n".join(
            f"| {day} | {count} |"
            for day, count in stats["daily_done"].items()
        )
    else:
        daily_rows = "| — | — |"

    # ── Stale files list ──────────────────────────────────────────────────────
    if stats["stale_files"]:
        stale_list = "\n".join(
            f"  - `{f.name}` (idle {_age_hours(f):.1f} hrs)"
            for f in stats["stale_files"]
        )
    else:
        stale_list = "  _None — all tasks are being processed promptly._"

    # ── Recommendations ───────────────────────────────────────────────────────
    recs = _build_recommendations(stats)
    rec_list = "\n".join(f"{i+1}. {r}" for i, r in enumerate(recs))

    # ── Assemble report ───────────────────────────────────────────────────────
    return (
        f"# Weekly Productivity Report\n"
        f"\n"
        f"> Generated by the Personal AI Employee — Gold Tier  \n"
        f"> Period: **{week_start}** to **{report_date}** ({stats['lookback_days']} days)  \n"
        f"> Report generated: {generated_at}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Executive Summary\n"
        f"\n"
        f"| Metric | Value |\n"
        f"|--------|-------|\n"
        f"| Tasks Completed (this week) | {stats['completed_this_week']} |\n"
        f"| Tasks Rejected (this week) | {stats['rejected_this_week']} |\n"
        f"| Completion Rate | {completion_rate:.0f}% |\n"
        f"| Currently Pending | {stats['total_pending']} |\n"
        f"| Awaiting Human Approval | {stats['pending_approval']} |\n"
        f"| Plans Created | {stats['plan_files']} |\n"
        f"| LinkedIn Posts | {stats['social_posts']} |\n"
        f"| Urgent Tasks Resolved | {stats['urgent_done']} |\n"
        f"| Urgent Tasks Still Pending | {stats['urgent_pending']} |\n"
        f"| All-Time Tasks Tracked | {stats['total_all_time']} |\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Task Category Breakdown\n"
        f"\n"
        f"| Category | Count |\n"
        f"|----------|-------|\n"
        f"{cat_rows}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Daily Completion Trend\n"
        f"\n"
        f"| Date | Tasks Completed |\n"
        f"|------|----------------|\n"
        f"{daily_rows}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Bottleneck Analysis\n"
        f"\n"
        f"Tasks that have been pending for more than 24 hours:\n"
        f"\n"
        f"{stale_list}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## AI Recommendations\n"
        f"\n"
        f"{rec_list}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"_This report was auto-generated. All data sourced from AI_Employee_Vault._\n"
    )


# ── Save report ───────────────────────────────────────────────────────────────

def save_report(report_text: str) -> Path:
    """Save the report to AI_Employee_Vault/Reports/ and return the path."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date_slug = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"Weekly_Report_{date_slug}.md"
    report_path.write_text(report_text, encoding="utf-8")
    return report_path


# ── Main class ────────────────────────────────────────────────────────────────

class ReportGenerator:
    """
    Gold Tier weekly report generator.

    Scans the entire vault, computes productivity metrics, and writes a
    rich Markdown report to AI_Employee_Vault/Reports/.

    Usage:
        ReportGenerator().run()           # generate report now
        ReportGenerator(lookback_days=14).run()   # fortnight report
    """

    def __init__(self, lookback_days: int = LOOKBACK_DAYS):
        self.lookback_days = lookback_days

    def run(self) -> Path:
        """Generate and save the report. Returns the saved report path."""
        log(f"Collecting vault statistics (last {self.lookback_days} days)...")
        stats = analyse_vault(self.lookback_days)

        log("Building report...")
        report_text = build_report(stats)

        path = save_report(report_text)
        log(f"Report saved -> {path}")
        log(
            f"Summary: Completed={stats['completed_this_week']}  "
            f"Pending={stats['total_pending']}  "
            f"Rejected={stats['rejected_this_week']}  "
            f"Completion rate={stats['completed_this_week'] / max(stats['completed_this_week'] + stats['rejected_this_week'], 1) * 100:.0f}%"
        )
        return path


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("   Personal AI Employee — Weekly Report Generator")
    print("   Gold Tier  |  Vault Analytics")
    print("=" * 55)
    print(f"   Vault dir   : {VAULT_DIR}")
    print(f"   Reports dir : {REPORTS_DIR}")
    print(f"   Lookback    : {LOOKBACK_DAYS} days")
    print(f"   Started     : {now_str()}")
    print("=" * 55 + "\n")

    report_path = ReportGenerator().run()

    print()
    print("=" * 55)
    print(f"   Report written to:")
    print(f"   {report_path}")
    print("=" * 55)
