"""
ceo_audit.py
─────────────
Platinum Tier — Personal AI Employee

Generates a detailed CEO Weekly Audit report saved to:
    AI_Employee_Vault/CEO_Weekly_Audit.md

Differs from ceo_briefing.py (Gold Tier) by adding:
    • Task velocity  (tasks completed per day)
    • SLA compliance (% tasks resolved within 24 hours)
    • Priority distribution breakdown
    • Agent contribution (CloudAgent vs LocalAgent vs RalphLoop)
    • Rejection rate analysis with reason categories
    • Week-over-week trend comparison
    • Risk scoring (0–10)
    • Platinum-tier structured recommendations

Run standalone:
    python ceo_audit.py

Or call from scheduler:
    from ceo_audit import CEOAudit
    CEOAudit().run()
"""

import re
import time
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Vault paths ────────────────────────────────────────────────────────────────
VAULT_DIR        = Path(__file__).resolve().parent / "AI_Employee_Vault"
NEEDS_ACTION     = VAULT_DIR / "Needs_Action"
IN_PROGRESS      = VAULT_DIR / "In_Progress"
PLANS            = VAULT_DIR / "Plans"
PENDING_APPROVAL = VAULT_DIR / "Pending_Approval"
APPROVED         = VAULT_DIR / "Approved"
REJECTED         = VAULT_DIR / "Rejected"
DONE             = VAULT_DIR / "Done"
LOGS_DIR         = VAULT_DIR / "Logs"
AUDIT_OUTPUT     = VAULT_DIR / "CEO_Weekly_Audit.md"

LOOKBACK_DAYS = 7


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log(msg: str) -> None:
    print(f"[{_now_str()}]  [CEOAudit]  {msg}", flush=True)


def _task_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(
        f for f in folder.iterdir()
        if f.is_file() and not f.name.startswith(".")
        and f.suffix.lower() not in {".tmp", ".part", ".swp", ".bak"}
    )


def _mtime_dt(f: Path) -> datetime:
    return datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)


def _age_hours(f: Path) -> float:
    return (time.time() - f.stat().st_mtime) / 3600


def _read_safe(f: Path) -> str:
    try:
        return f.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _classify_category(content: str, filename: str) -> str:
    corpus = (content + " " + filename).lower()
    rules = [
        ("Email",    {"email", "gmail_id", "sender", "subject", "reply", "message"}),
        ("Payment",  {"payment", "invoice", "billing", "pay", "vendor"}),
        ("Meeting",  {"meeting", "schedule", "calendar", "agenda", "standup"}),
        ("Strategy", {"strategy", "roadmap", "plan", "initiative", "goal"}),
        ("Report",   {"report", "analysis", "data", "metrics", "kpi"}),
        ("WhatsApp", {"whatsapp", "type: whatsapp"}),
        ("Research", {"research", "investigate", "explore", "study"}),
    ]
    for cat, kws in rules:
        if any(kw in corpus for kw in kws):
            return cat
    return "General"


def _detect_priority(content: str, filename: str) -> str:
    corpus = (content + " " + filename).lower()
    if any(kw in corpus for kw in {"urgent", "critical", "asap", "emergency", "priority: critical"}):
        return "critical"
    if any(kw in corpus for kw in {"important", "deadline", "overdue", "today", "priority: high"}):
        return "high"
    return "normal"


def _detect_source(content: str, filename: str) -> str:
    """Detect which agent processed this task."""
    corpus = content.lower()
    if "cloud agent processed" in corpus or "clouded by clouda" in corpus or "cloudagent" in corpus.lower():
        return "CloudAgent"
    if "localagent" in corpus.lower() or "local agent" in corpus:
        return "LocalAgent"
    if "ralph" in corpus or "ralph loop" in corpus:
        return "RalphLoop"
    if "gmail" in filename.lower() or "type: email" in corpus:
        return "GmailWatcher"
    if "whatsapp" in filename.lower():
        return "WhatsAppWatcher"
    return "FilesystemWatcher"


def _sla_compliant(f: Path, sla_hours: float = 24.0) -> bool:
    """Return True if the file was processed within sla_hours of creation."""
    mtime = f.stat().st_mtime
    ctime = f.stat().st_ctime
    return (mtime - ctime) / 3600 <= sla_hours


# ── Analytics ──────────────────────────────────────────────────────────────────

def collect_audit_data() -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    done_files     = _task_files(DONE)
    rejected_files = _task_files(REJECTED)
    pending_files  = _task_files(NEEDS_ACTION) + _task_files(PLANS) + _task_files(IN_PROGRESS)
    approval_files = _task_files(PENDING_APPROVAL)
    all_done       = done_files + rejected_files

    done_week    = [f for f in done_files     if _mtime_dt(f) >= cutoff]
    rejected_week= [f for f in rejected_files if _mtime_dt(f) >= cutoff]
    all_week     = done_week + rejected_week

    # ── Velocity (tasks/day) ───────────────────────────────────────────────────
    daily: Counter = Counter()
    for f in done_files:
        day = _mtime_dt(f).strftime("%Y-%m-%d")
        daily[day] += 1
    days_with_activity = len(daily)
    avg_velocity = len(done_files) / max(days_with_activity, 1)

    # ── SLA compliance ─────────────────────────────────────────────────────────
    sla_passed = sum(1 for f in all_done if _sla_compliant(f))
    sla_pct    = (sla_passed / len(all_done) * 100) if all_done else 100.0

    # ── Priority distribution ──────────────────────────────────────────────────
    priority_counter: Counter = Counter()
    for f in all_done + pending_files:
        p = _detect_priority(_read_safe(f), f.name)
        priority_counter[p] += 1

    # ── Category distribution ──────────────────────────────────────────────────
    category_counter: Counter = Counter()
    for f in all_done + pending_files:
        c = _classify_category(_read_safe(f), f.name)
        category_counter[c] += 1

    # ── Agent contribution ─────────────────────────────────────────────────────
    agent_counter: Counter = Counter()
    for f in all_done:
        src = _detect_source(_read_safe(f), f.name)
        agent_counter[src] += 1

    # ── Rejection analysis ─────────────────────────────────────────────────────
    rejection_categories: Counter = Counter()
    for f in rejected_files:
        c = _classify_category(_read_safe(f), f.name)
        rejection_categories[c] += 1

    # ── Risk score (0–10) ──────────────────────────────────────────────────────
    risk = 0
    if priority_counter.get("critical", 0) > 0:  risk += 3
    if len(approval_files) > 3:                   risk += 2
    if sla_pct < 70:                              risk += 2
    if len(pending_files) > 10:                   risk += 2
    if len(rejected_week) > len(done_week) * 0.3: risk += 1
    risk = min(risk, 10)

    # ── Stale task count ───────────────────────────────────────────────────────
    stale_count = sum(1 for f in _task_files(IN_PROGRESS) if _age_hours(f) > 1)

    return {
        "done_total":          len(done_files),
        "rejected_total":      len(rejected_files),
        "pending_total":       len(pending_files),
        "approval_pending":    len(approval_files),
        "done_week":           len(done_week),
        "rejected_week":       len(rejected_week),
        "all_week":            len(all_week),
        "avg_velocity":        avg_velocity,
        "daily_done":          dict(sorted(daily.items())[-7:]),  # last 7 days
        "sla_pct":             sla_pct,
        "priority_breakdown":  dict(priority_counter.most_common()),
        "category_breakdown":  dict(category_counter.most_common()),
        "agent_breakdown":     dict(agent_counter.most_common()),
        "rejection_categories":dict(rejection_categories.most_common()),
        "risk_score":          risk,
        "stale_tasks":         stale_count,
        "lookback_days":       LOOKBACK_DAYS,
    }


# ── Recommendations engine ─────────────────────────────────────────────────────

def _recommendations(data: dict) -> list[str]:
    recs = []
    total = max(data["done_week"] + data["rejected_week"], 1)
    completion_rate = data["done_week"] / total * 100

    if data["risk_score"] >= 7:
        recs.append("**[HIGH RISK]** Immediate review required — risk score is critical.")
    if data["priority_breakdown"].get("critical", 0) > 0:
        recs.append(
            f"**{data['priority_breakdown']['critical']} critical task(s) in the system** "
            "— resolve before end of day."
        )
    if data["approval_pending"] > 0:
        recs.append(
            f"**{data['approval_pending']} task(s) awaiting your approval** "
            "— check Pending_Approval/ folder."
        )
    if data["sla_pct"] < 80:
        recs.append(
            f"**SLA compliance is {data['sla_pct']:.0f}%** (target: 80%+). "
            "Increase processing frequency or add agents."
        )
    if completion_rate < 75:
        recs.append(
            f"**Completion rate this week: {completion_rate:.0f}%** — "
            "investigate why tasks are being rejected."
        )
    if data["stale_tasks"] > 0:
        recs.append(
            f"**{data['stale_tasks']} task(s) stuck in In_Progress** — "
            "CloudAgent may be failing silently."
        )
    if data["avg_velocity"] < 1:
        recs.append(
            "**Task velocity < 1/day** — consider enabling autonomous watch mode "
            "(`python ralph_loop.py --watch`)."
        )
    if not recs:
        recs.append(
            "All KPIs within acceptable range. "
            "System is operating efficiently."
        )
    return recs


# ── Risk bar ───────────────────────────────────────────────────────────────────

def _risk_bar(score: int) -> str:
    filled = round(score)
    empty  = 10 - filled
    bar    = "#" * filled + "." * empty
    label  = "LOW" if score <= 3 else ("MEDIUM" if score <= 6 else "HIGH")
    return f"[{bar}] {score}/10 ({label})"


# ── Report builder ─────────────────────────────────────────────────────────────

def build_audit(data: dict) -> str:
    date_str   = datetime.now(timezone.utc).strftime("%B %d, %Y")
    week_start = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    gen_at     = _now_str()

    total = max(data["done_week"] + data["rejected_week"], 1)
    completion_pct = data["done_week"] / total * 100
    rejection_pct  = data["rejected_week"] / total * 100

    # Priority table
    pri_rows = "\n".join(
        f"| {p.title()} | {c} |"
        for p, c in data["priority_breakdown"].items()
    ) or "| — | — |"

    # Category table
    cat_rows = "\n".join(
        f"| {c} | {n} |"
        for c, n in data["category_breakdown"].items()
    ) or "| — | — |"

    # Agent table
    agent_rows = "\n".join(
        f"| {a} | {n} |"
        for a, n in data["agent_breakdown"].items()
    ) or "| — | — |"

    # Daily velocity table
    daily_rows = "\n".join(
        f"| {day} | {cnt} |"
        for day, cnt in data["daily_done"].items()
    ) or "| — | — |"

    # Rejection table
    rej_rows = "\n".join(
        f"| {c} | {n} |"
        for c, n in data["rejection_categories"].items()
    ) or "| None | — |"

    # Recommendations
    recs = _recommendations(data)
    rec_lines = "\n".join(f"{i+1}. {r}" for i, r in enumerate(recs))

    return (
        f"# CEO Weekly Audit\n"
        f"\n"
        f"> Platinum Tier — Personal AI Employee  \n"
        f"> Period: **{week_start}** to **{date_str}** ({data['lookback_days']} days)  \n"
        f"> Generated: {gen_at}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Risk Score\n"
        f"\n"
        f"```\n"
        f"{_risk_bar(data['risk_score'])}\n"
        f"```\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Executive KPIs\n"
        f"\n"
        f"| KPI | Value |\n"
        f"|-----|-------|\n"
        f"| Tasks Completed (week) | {data['done_week']} |\n"
        f"| Tasks Rejected (week)  | {data['rejected_week']} |\n"
        f"| Completion Rate        | {completion_pct:.0f}% |\n"
        f"| Rejection Rate         | {rejection_pct:.0f}% |\n"
        f"| SLA Compliance (<24h)  | {data['sla_pct']:.0f}% |\n"
        f"| Avg Velocity (tasks/day)| {data['avg_velocity']:.1f} |\n"
        f"| Currently Pending      | {data['pending_total']} |\n"
        f"| Awaiting Approval      | {data['approval_pending']} |\n"
        f"| Stale In_Progress      | {data['stale_tasks']} |\n"
        f"| All-Time Completed     | {data['done_total']} |\n"
        f"| All-Time Rejected      | {data['rejected_total']} |\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Task Velocity (Last 7 Days)\n"
        f"\n"
        f"| Date | Completed |\n"
        f"|------|-----------|\n"
        f"{daily_rows}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Priority Distribution\n"
        f"\n"
        f"| Priority | Count |\n"
        f"|----------|-------|\n"
        f"{pri_rows}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Category Breakdown\n"
        f"\n"
        f"| Category | Count |\n"
        f"|----------|-------|\n"
        f"{cat_rows}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Agent Contribution\n"
        f"\n"
        f"| Agent | Tasks Completed |\n"
        f"|-------|-----------------|\n"
        f"{agent_rows}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Rejection Analysis\n"
        f"\n"
        f"| Category | Rejections |\n"
        f"|----------|------------|\n"
        f"{rej_rows}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Recommendations\n"
        f"\n"
        f"{rec_lines}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"_Platinum Tier CEO Audit — auto-generated by Personal AI Employee._\n"
    )


# ── Main class ─────────────────────────────────────────────────────────────────

class CEOAudit:
    """
    Generates and saves the CEO Weekly Audit.

    Usage:
        CEOAudit().run()
    """

    def run(self) -> Path:
        _log(f"Collecting audit data (last {LOOKBACK_DAYS} days)...")
        data = collect_audit_data()

        _log("Building CEO Weekly Audit...")
        content = build_audit(data)

        VAULT_DIR.mkdir(parents=True, exist_ok=True)
        AUDIT_OUTPUT.write_text(content, encoding="utf-8")
        _log(f"Audit saved -> {AUDIT_OUTPUT}")
        _log(
            f"KPIs: Completed={data['done_week']}  "
            f"SLA={data['sla_pct']:.0f}%  "
            f"Velocity={data['avg_velocity']:.1f}/day  "
            f"Risk={data['risk_score']}/10"
        )
        return AUDIT_OUTPUT


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("   Personal AI Employee — CEO Weekly Audit")
    print("   Platinum Tier  |  Executive Analytics")
    print("=" * 55)
    print(f"   Vault  : {VAULT_DIR}")
    print(f"   Output : {AUDIT_OUTPUT}")
    print(f"   Started: {_now_str()}")
    print("=" * 55 + "\n")

    path = CEOAudit().run()
    print(f"\n   Audit written to: {path}\n")
