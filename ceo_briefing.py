"""
ceo_briefing.py
────────────────
Gold Tier — Personal AI Employee

Reads task data from the vault, then writes a concise executive-level
weekly briefing to AI_Employee_Vault/CEO_Briefing.md.

The briefing covers:
  - Tasks completed / pending / plans created
  - Key issues detected (urgent, overdue, blocked tasks)
  - Opportunities detected (new projects, strategy items)
  - Pending human approvals
  - Quick action items the CEO should be aware of

Run standalone:
    python ceo_briefing.py

Or call from scheduler / other modules:
    from ceo_briefing import CEOBriefing
    CEOBriefing().run()
"""

import io
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Vault paths ────────────────────────────────────────────────────────────────
VAULT_DIR        = Path(__file__).resolve().parent / "AI_Employee_Vault"
NEEDS_ACTION     = VAULT_DIR / "Needs_Action"
PLANS            = VAULT_DIR / "Plans"
DONE             = VAULT_DIR / "Done"
PENDING_APPROVAL = VAULT_DIR / "Pending_Approval"
REJECTED         = VAULT_DIR / "Rejected"
BRIEFING_FILE    = VAULT_DIR / "CEO_Briefing.md"

# ── Keyword rules ──────────────────────────────────────────────────────────────

# Issues: things that need immediate CEO awareness
_ISSUE_KW = {
    "urgent", "critical", "overdue", "blocked", "delayed", "failed",
    "escalate", "breach", "complaint", "unpaid", "invoice", "payment",
    "rejected", "error", "problem", "issue", "help", "stuck", "asap",
    "emergency", "deadline missed", "not received", "no response",
}

# Opportunities: positive forward-looking signals
_OPPORTUNITY_KW = {
    "new project", "proposal", "strategy", "roadmap", "opportunity",
    "growth", "initiative", "launch", "partnership", "expansion",
    "client onboarding", "contract", "deal", "pipeline", "revenue",
    "innovation", "improvement", "plan", "upgrade", "feature",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log(msg: str) -> None:
    print(f"[{_now_str()}]  [CEO Briefing]  {msg}", flush=True)


def _task_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(
        f for f in folder.iterdir()
        if f.is_file()
        and not f.name.startswith(".")
        and f.suffix.lower() not in {".tmp", ".part", ".swp", ".bak"}
    )


def _read_safe(f: Path) -> str:
    try:
        return f.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _humanise(filename: str) -> str:
    """Turn a snake_case filename into a readable phrase."""
    name = filename
    # Strip known prefixes and extension
    for prefix in ("PLAN_", "PENDING_", "PAY_", "URGENT_", "email_", "whatsapp_"):
        name = name.removeprefix(prefix)
    name = Path(name).stem                          # drop extension
    name = re.sub(r"_+", " ", name).strip()        # underscores -> spaces
    name = re.sub(r"\b[A-Z]{2,}\b", lambda m: m.group().title(), name)  # ALLCAPS -> Title
    return name.lower()


def _extract_issues(files: list[Path]) -> list[str]:
    """
    Scan files for issue keywords.
    Returns a deduplicated list of human-readable issue phrases.
    """
    issues: list[str] = []
    seen: set[str] = set()

    for f in files:
        corpus = (_read_safe(f) + " " + f.name).lower()
        matched_kws = [kw for kw in _ISSUE_KW if kw in corpus]
        if matched_kws:
            phrase = _humanise(f.name)
            if phrase not in seen:
                seen.add(phrase)
                issues.append(phrase)

    return issues


def _extract_opportunities(files: list[Path]) -> list[str]:
    """
    Scan plan files for opportunity keywords.
    Returns a deduplicated list of human-readable opportunity phrases.
    """
    opps: list[str] = []
    seen: set[str] = set()

    for f in files:
        corpus = (_read_safe(f) + " " + f.name).lower()
        if any(kw in corpus for kw in _OPPORTUNITY_KW):
            phrase = _humanise(f.name)
            if phrase not in seen:
                seen.add(phrase)
                opps.append(phrase)

    return opps


# ── Data collection ────────────────────────────────────────────────────────────

def collect_briefing_data() -> dict:
    done_files     = _task_files(DONE)
    pending_files  = _task_files(NEEDS_ACTION)
    plan_files     = _task_files(PLANS)
    approval_files = _task_files(PENDING_APPROVAL)
    rejected_files = _task_files(REJECTED)

    # Issues come from pending (unresolved) and rejected tasks
    issues = _extract_issues(pending_files + rejected_files)

    # Opportunities come from plan files and pending strategy tasks
    opportunities = _extract_opportunities(plan_files + pending_files)

    # Action items = pending approvals + urgent pending tasks
    urgent_pending = [
        f for f in pending_files
        if any(kw in (_read_safe(f) + f.name).lower()
               for kw in {"urgent", "critical", "asap", "emergency"})
    ]

    return {
        "completed":      len(done_files),
        "pending":        len(pending_files),
        "plans_created":  len(plan_files),
        "pending_approval": len(approval_files),
        "rejected":       len(rejected_files),
        "issues":         issues,
        "opportunities":  opportunities,
        "urgent_pending": [_humanise(f.name) for f in urgent_pending],
        "approval_names": [_humanise(f.name) for f in approval_files],
    }


# ── Report builder ─────────────────────────────────────────────────────────────

def build_briefing(data: dict) -> str:
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
    generated_at = _now_str()

    # ── Issues section ─────────────────────────────────────────────────────────
    if data["issues"]:
        issue_lines = "\n".join(f"- {issue}" for issue in data["issues"])
    else:
        issue_lines = "- No critical issues detected."

    # ── Opportunities section ──────────────────────────────────────────────────
    if data["opportunities"]:
        opp_lines = "\n".join(f"- {opp}" for opp in data["opportunities"])
    else:
        opp_lines = "- No new opportunities flagged this week."

    # ── Action items section ───────────────────────────────────────────────────
    action_lines_list: list[str] = []
    if data["urgent_pending"]:
        for item in data["urgent_pending"]:
            action_lines_list.append(f"- **[URGENT]** {item}")
    if data["approval_names"]:
        for item in data["approval_names"]:
            action_lines_list.append(f"- **[APPROVAL NEEDED]** {item}")
    if not action_lines_list:
        action_lines_list.append("- No immediate actions required.")
    action_lines = "\n".join(action_lines_list)

    # ── Health status ──────────────────────────────────────────────────────────
    if data["pending"] == 0 and data["pending_approval"] == 0:
        health = "All clear — operations running smoothly."
    elif data["pending_approval"] > 0:
        health = f"{data['pending_approval']} task(s) require your approval."
    elif data["pending"] > 5:
        health = f"High workload — {data['pending']} tasks currently pending."
    else:
        health = f"{data['pending']} task(s) in progress."

    return (
        f"# CEO Weekly Briefing\n"
        f"\n"
        f"> Prepared by your Personal AI Employee  \n"
        f"> Week ending: **{date_str}**  \n"
        f"> Generated: {generated_at}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Operational Summary\n"
        f"\n"
        f"| Metric | Count |\n"
        f"|--------|-------|\n"
        f"| Tasks Completed | {data['completed']} |\n"
        f"| Pending Tasks | {data['pending']} |\n"
        f"| Plans Created | {data['plans_created']} |\n"
        f"| Awaiting Your Approval | {data['pending_approval']} |\n"
        f"| Rejected Tasks | {data['rejected']} |\n"
        f"\n"
        f"**Status:** {health}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Key Issues\n"
        f"\n"
        f"{issue_lines}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Opportunities\n"
        f"\n"
        f"{opp_lines}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Action Items for CEO\n"
        f"\n"
        f"{action_lines}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"_This briefing is auto-generated. All data sourced from AI\\_Employee\\_Vault._\n"
    )


# ── Save & display ─────────────────────────────────────────────────────────────

def save_briefing(content: str) -> Path:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    BRIEFING_FILE.write_text(content, encoding="utf-8")
    return BRIEFING_FILE


# ── Main class ─────────────────────────────────────────────────────────────────

class CEOBriefing:
    """
    Generates and saves the CEO weekly briefing.

    Usage:
        CEOBriefing().run()
    """

    def run(self) -> Path:
        _log("Collecting vault data...")
        data = collect_briefing_data()

        _log("Building briefing...")
        content = build_briefing(data)

        path = save_briefing(content)
        _log(f"Briefing saved -> {path}")
        _log(
            f"Summary: Completed={data['completed']}  "
            f"Pending={data['pending']}  "
            f"Plans={data['plans_created']}  "
            f"Issues={len(data['issues'])}  "
            f"Opportunities={len(data['opportunities'])}"
        )
        return path


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("   Personal AI Employee — CEO Weekly Briefing")
    print("   Gold Tier  |  Executive Summary")
    print("=" * 55)
    print(f"   Vault dir : {VAULT_DIR}")
    print(f"   Output    : {BRIEFING_FILE}")
    print(f"   Started   : {_now_str()}")
    print("=" * 55 + "\n")

    path = CEOBriefing().run()

    print()
    print("=" * 55)
    print(f"   Briefing written to:")
    print(f"   {path}")
    print("=" * 55)
