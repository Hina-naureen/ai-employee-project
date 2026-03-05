"""
cloud_agent.py
───────────────
Platinum Tier — Personal AI Employee

Cloud-side agent.  Runs continuously (24/7) and handles tasks that do
NOT require physical access or human approval:

    • Email tasks   → draft reply suggestion, enrich content
    • Strategy tasks → generate plan outline, queue LinkedIn draft
    • Report tasks   → summarise and tag
    • Research tasks → extract key points
    • All others    → pass-through enrichment

Workflow:
    Needs_Action/  →  In_Progress/ (claimed)  →  Done/

The In_Progress/ folder acts as a distributed lock so that ralph_loop
and cloud_agent never process the same file simultaneously.

Run standalone:
    python cloud_agent.py

Or start via orchestrator (recommended):
    from cloud_agent import CloudAgent
    CloudAgent().start()

Environment variables (all optional):
    CLOUD_POLL_INTERVAL    seconds between scans    (default: 20)
    OPENAI_API_KEY         enables AI enrichment    (optional)
"""

import os
import re
import time
import shutil
from datetime import datetime, timezone
from pathlib import Path

# ── Vault paths ────────────────────────────────────────────────────────────────
VAULT_DIR        = Path(__file__).resolve().parent / "AI_Employee_Vault"
NEEDS_ACTION     = VAULT_DIR / "Needs_Action"
IN_PROGRESS      = VAULT_DIR / "In_Progress"
PLANS            = VAULT_DIR / "Plans"
DONE             = VAULT_DIR / "Done"
PENDING_APPROVAL = VAULT_DIR / "Pending_Approval"
LOGS_DIR         = VAULT_DIR / "Logs"

# ── Config ─────────────────────────────────────────────────────────────────────
POLL_INTERVAL = int(os.environ.get("CLOUD_POLL_INTERVAL", "20"))
OPENAI_KEY    = os.environ.get("OPENAI_API_KEY", "")

try:
    import openai as _oai
    _OAI_OK = True
except ImportError:
    _OAI_OK = False

from audit_logger import log_action, log_system
from base_watcher import BaseWatcher

# ── Category keyword map ───────────────────────────────────────────────────────
_CATEGORY_RULES: list[tuple[str, set]] = [
    ("Email",    {"email", "reply", "message", "gmail_id", "sender", "subject", "inbox"}),
    ("Strategy", {"strategy", "roadmap", "plan", "initiative", "goal", "objective", "proposal"}),
    ("Report",   {"report", "analysis", "data", "metrics", "kpi", "dashboard", "analytics"}),
    ("Research", {"research", "investigate", "explore", "study", "gather", "survey"}),
    ("Document", {"document", "write", "policy", "procedure", "guide", "sop", "wiki"}),
    ("Payment",  {"payment", "invoice", "billing", "vendor", "pay", "receipt"}),
    ("Meeting",  {"meeting", "schedule", "calendar", "agenda", "sync", "standup"}),
]

# Cloud agent ONLY handles these categories (others left for ralph_loop / local_agent)
_CLOUD_CATEGORIES = {"Email", "Strategy", "Report", "Research", "Document"}


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


def _safe_move(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        stem, suffix = src.stem, src.suffix
        c = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{c}{suffix}"
            c += 1
    for attempt in range(1, 6):
        try:
            shutil.move(str(src), str(dest))
            return dest
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.3 * attempt)
    return dest


def _classify(content: str, filename: str) -> str:
    corpus = (content + " " + filename).lower()
    for category, keywords in _CATEGORY_RULES:
        if any(kw in corpus for kw in keywords):
            return category
    return "General"


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


# ── AI Enrichment ──────────────────────────────────────────────────────────────

def _openai_enrich(content: str, category: str) -> str:
    """Call OpenAI to produce a concise enrichment block."""
    prompt = (
        f"You are an AI Employee assistant. Analyse this {category} task and reply with "
        f"EXACTLY this format (no extra text):\n\n"
        f"SUMMARY: <one sentence summary>\n"
        f"ACTION_1: <first action step>\n"
        f"ACTION_2: <second action step>\n"
        f"ACTION_3: <third action step>\n"
        f"PRIORITY: <critical|high|normal>\n\n"
        f"Task content:\n{content[:1200]}"
    )
    client   = _oai.OpenAI(api_key=OPENAI_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=200,
    )
    return response.choices[0].message.content.strip()


def _local_enrich(content: str, category: str) -> str:
    """Keyword-based enrichment — no API required."""
    corpus = content.lower()

    # Derive priority
    if any(kw in corpus for kw in {"urgent", "asap", "critical", "immediately", "emergency"}):
        priority = "critical"
    elif any(kw in corpus for kw in {"important", "deadline", "overdue", "today"}):
        priority = "high"
    else:
        priority = "normal"

    # Category-specific action steps
    action_map = {
        "Email":    ["Reply to sender", "File or archive the email", "Follow up if no response in 24h"],
        "Strategy": ["Review current objectives", "Identify key milestones", "Draft action plan"],
        "Report":   ["Collect data from all sources", "Analyse trends and anomalies", "Prepare executive summary"],
        "Research": ["Define research scope", "Gather information from reliable sources", "Synthesise findings"],
        "Document": ["Outline document structure", "Draft initial content", "Review and finalise"],
    }
    steps = action_map.get(category, ["Clarify objective", "Execute task", "Review and close"])

    summary_line = content.strip().splitlines()[0][:100] if content.strip() else f"{category} task"

    return (
        f"SUMMARY: {summary_line}\n"
        f"ACTION_1: {steps[0]}\n"
        f"ACTION_2: {steps[1]}\n"
        f"ACTION_3: {steps[2]}\n"
        f"PRIORITY: {priority}"
    )


def _enrich(content: str, category: str) -> str:
    """Return an enrichment block string."""
    if _OAI_OK and OPENAI_KEY:
        try:
            return _openai_enrich(content, category)
        except Exception:
            pass
    return _local_enrich(content, category)


def _append_enrichment(task_file: Path, enrichment: str) -> None:
    """Append the AI enrichment block to the task file."""
    existing = task_file.read_text(encoding="utf-8", errors="ignore")
    if "## AI Enrichment" in existing:
        return  # already enriched
    block = (
        f"\n\n---\n\n"
        f"## AI Enrichment\n\n"
        f"> Added by CloudAgent — {_now_str()}\n\n"
        f"```\n{enrichment}\n```\n"
    )
    task_file.write_text(existing + block, encoding="utf-8")


# ── LinkedIn draft helper ──────────────────────────────────────────────────────

def _queue_linkedin_draft(task_file: Path, enrichment: str) -> None:
    """
    Write a LinkedIn post draft to AI_Employee_Vault/Plans/ so that
    linkedin_poster.py picks it up on its next run.
    """
    plan_dir = PLANS
    plan_dir.mkdir(parents=True, exist_ok=True)
    draft_path = plan_dir / f"PLAN_{task_file.stem}.md"
    if draft_path.exists():
        return   # already exists

    summary = ""
    for line in enrichment.splitlines():
        if line.startswith("SUMMARY:"):
            summary = line.replace("SUMMARY:", "").strip()
            break
    if not summary:
        summary = task_file.stem.replace("_", " ").title()

    draft = (
        f"# AI Task Plan\n\n"
        f"**Task:**\n{summary}\n\n"
        f"## Steps\n\n"
        f"1. Identify key stakeholders and objectives\n"
        f"2. Draft the strategic approach\n"
        f"3. Execute phase one and measure results\n\n"
        f"**Priority:** High\n"
        f"**Estimated Time:** 2-4 hours\n"
        f"**Suggested Tools:** Project management tool, Communication platform\n"
    )
    draft_path.write_text(draft, encoding="utf-8")


# ── Main agent class ───────────────────────────────────────────────────────────

class CloudAgent(BaseWatcher):
    """
    Platinum Tier cloud-side task agent.

    Polls Needs_Action/ for email, strategy, report, research, and document
    tasks.  Claims each task by moving it to In_Progress/, enriches it with
    AI analysis, and moves it to Done/.

    Payment / sensitive tasks are left untouched for local_agent.
    """

    def __init__(self):
        super().__init__(name="CloudAgent")
        self._running   = False
        self._claimed: set[str] = set()   # filenames already processed this session

    def process(self, task_file: Path) -> None:
        """Enrich and complete one task file."""
        content  = task_file.read_text(encoding="utf-8", errors="ignore")
        category = _classify(content, task_file.name)

        self.log(f"  Category: {category} | File: {task_file.name}")

        if category not in _CLOUD_CATEGORIES:
            # Not cloud agent's domain — put back to Needs_Action
            _safe_move(task_file, NEEDS_ACTION)
            self.log(f"  Returned to Needs_Action (not cloud domain): {task_file.name}")
            return

        # AI enrichment
        enrichment = _enrich(content, category)
        _append_enrichment(task_file, enrichment)

        # Queue LinkedIn draft for strategy/document tasks
        if category in {"Strategy", "Document"}:
            _queue_linkedin_draft(task_file, enrichment)
            self.log(f"  LinkedIn draft queued for: {task_file.name}")

        # Move to Done
        done_path = _safe_move(task_file, DONE)
        self.log(f"  Completed -> Done/{done_path.name}")

        log_action(
            action=f"Cloud Agent processed ({category})",
            file=task_file.name,
            result="Done",
            extra=enrichment.splitlines()[0] if enrichment else None,
            source="CloudAgent",
        )
        _write_platinum_log(f"[CloudAgent] Processed {task_file.name} -> Done ({category})")

    def _poll_once(self) -> None:
        tasks = _task_files(NEEDS_ACTION)
        new   = [t for t in tasks if t.name not in self._claimed]
        if not new:
            return

        self.log(f"Found {len(new)} unclaimed task(s) in Needs_Action.")
        for task in new:
            self._claimed.add(task.name)
            try:
                # Claim by moving to In_Progress
                in_progress = _safe_move(task, IN_PROGRESS)
                self.process(in_progress)
            except FileNotFoundError:
                self.log(f"  File gone before claim: {task.name}")
            except Exception as exc:
                self.on_error(exc)
                _write_platinum_log(f"[CloudAgent] ERROR on {task.name}: {exc}")

    def start(self) -> None:
        # Ensure Platinum vault folders exist
        for folder in (IN_PROGRESS, LOGS_DIR):
            folder.mkdir(parents=True, exist_ok=True)

        self.print_banner([
            f"Poll interval : every {POLL_INTERVAL}s",
            f"AI enrichment : {'OpenAI (gpt-4o-mini)' if (_OAI_OK and OPENAI_KEY) else 'local keyword engine'}",
            f"Handles       : Email, Strategy, Report, Research, Document tasks",
            f"In_Progress   : {IN_PROGRESS}",
            "Press Ctrl+C to stop.",
        ])
        log_system("CloudAgent started", source="CloudAgent")
        _write_platinum_log("[CloudAgent] Started")
        self._running = True

        try:
            while self._running:
                self._poll_once()
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            self.log("Shutdown signal received.")
        finally:
            self.stop()

    def stop(self) -> None:
        self._running = False
        log_system("CloudAgent stopped", source="CloudAgent")
        _write_platinum_log("[CloudAgent] Stopped")
        self.on_stop()
        self.log("CloudAgent stopped.")


if __name__ == "__main__":
    CloudAgent().start()
