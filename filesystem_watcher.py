"""
filesystem_watcher.py
─────────────────────
Silver Tier — Personal AI Employee (Digital FTE)

Monitors the Inbox folder, triages incoming task files, generates AI-powered
plans, enforces handbook policies, and archives completed work.

Human-in-the-Loop (HITL) approval system:
  - Sensitive tasks (payment, invoice, etc.) generate a PENDING_*.md file
    inside AI_Employee_Vault/Pending_Approval/
  - Human reviews the file and moves it to Approved/ or Rejected/
  - The watcher detects the decision and finalises the task automatically

Dependencies:
    pip install watchdog openai

AI Planning:
    Set OPENAI_API_KEY in your environment to enable OpenAI-powered plans.
    Without it the system falls back to a smart local planner automatically.

Run:
    python filesystem_watcher.py
"""

import io
import os
import re
import shutil
import sys
import textwrap
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 output on Windows so Unicode in logs prints correctly
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# OpenAI is optional — graceful fallback to local planner if not installed
try:
    import openai as _openai_lib
    _OPENAI_IMPORTABLE = True
except ImportError:
    _OPENAI_IMPORTABLE = False

# Silver Tier — AI classification layer
from ai_processor import analyze_file

# Silver Tier — Dashboard scheduler
from scheduler import DashboardScheduler

# ─────────────────────────────────────────────
#  VAULT PATHS  — always resolved to absolute paths (critical on Windows)
# ─────────────────────────────────────────────

BASE_DIR         = Path(__file__).resolve().parent / "AI_Employee_Vault"
INBOX            = BASE_DIR / "Inbox"
NEEDS_ACTION     = BASE_DIR / "Needs_Action"
PLANS            = BASE_DIR / "Plans"
PENDING_APPROVAL = BASE_DIR / "Pending_Approval"
APPROVED         = BASE_DIR / "Approved"
REJECTED         = BASE_DIR / "Rejected"
DONE             = BASE_DIR / "Done"
DASHBOARD        = BASE_DIR / "Dashboard.md"

# ─────────────────────────────────────────────
#  AI PLANNER CONFIG
# ─────────────────────────────────────────────

# Set OPENAI_API_KEY in your shell environment or a .env file.
# If absent (or openai package not installed), the built-in smart
# local planner is used automatically — no internet required.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL   = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# ─────────────────────────────────────────────
#  POLICY KEYWORDS  (from Company Handbook)
# ─────────────────────────────────────────────

# Files whose name or content contains these words require human approval
PAYMENT_KEYWORDS = {"payment", "invoice", "transfer", "pay", "purchase"}

# Files prefixed with URGENT_ are processed before standard tasks
URGENT_PREFIX = "URGENT_"

# File suffixes / prefixes that are editor temp artefacts — skip these
_TEMP_SUFFIXES  = {".tmp", ".part", ".swp", ".swo", ".bak", ".orig"}
_TEMP_PREFIXES  = {".", "~", "#"}


# ─────────────────────────────────────────────
#  UTILITY HELPERS
# ─────────────────────────────────────────────

def now_str() -> str:
    """Return a human-readable UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def log(message: str) -> None:
    """Print a timestamped message to the terminal."""
    print(f"[{now_str()}]  {message}", flush=True)


def is_payment_task(filepath: Path) -> bool:
    """
    Return True if the task filename OR its text content contains any
    payment-related keyword defined in the Company Handbook.
    """
    name_lower = filepath.name.lower()
    if any(kw in name_lower for kw in PAYMENT_KEYWORDS):
        return True
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore").lower()
        return any(kw in content for kw in PAYMENT_KEYWORDS)
    except OSError:
        return False


def is_urgent(filepath: Path) -> bool:
    """Return True if the file is prefixed with URGENT_ (case-insensitive)."""
    return filepath.name.upper().startswith(URGENT_PREFIX)


def is_temp_file(filepath: Path) -> bool:
    """
    Return True for editor temp / lock / swap files that should be ignored.
    Covers: hidden dot-files, ~backups, .tmp/.swp/.bak, etc.
    """
    name = filepath.name
    if any(name.startswith(p) for p in _TEMP_PREFIXES):
        return True
    if filepath.suffix.lower() in _TEMP_SUFFIXES:
        return True
    return False


def safe_move(src: Path, dst_dir: Path) -> Path:
    """
    Robustly move *src* into *dst_dir* on Windows.

    - Resolves both paths to absolute before moving.
    - Avoids collisions by appending a counter when the destination exists.
    - Retries up to 5 times with a short sleep to handle Windows file locks.

    Returns the destination Path.
    """
    src     = src.resolve()
    dst_dir = dst_dir.resolve()
    dst_dir.mkdir(parents=True, exist_ok=True)

    dst = dst_dir / src.name
    counter = 1
    while dst.exists():
        dst = dst_dir / f"{src.stem}_{counter}{src.suffix}"
        counter += 1

    # Retry loop — handles transient Windows file-lock errors (e.g. antivirus)
    last_exc: Exception | None = None
    for attempt in range(1, 6):
        try:
            shutil.move(str(src), str(dst))
            return dst
        except PermissionError as exc:
            last_exc = exc
            log(f"  [WARN]    Move attempt {attempt}/5 failed (lock). Retrying...")
            time.sleep(0.3 * attempt)
        except FileNotFoundError:
            # File disappeared between detection and move — nothing to do
            raise

    raise RuntimeError(f"Could not move {src.name} after 5 attempts: {last_exc}")


# ─────────────────────────────────────────────
#  DASHBOARD  (Dashboard.md writer)
# ─────────────────────────────────────────────

# Simple in-memory stats updated throughout the session
_stats = {
    "Total Tasks":     0,
    "Completed Tasks": 0,
    "Pending Tasks":   0,
    "Rejected Tasks":  0,
}

_dashboard_lock = threading.Lock()


def _read_dashboard() -> str:
    """Read the current dashboard content, or return an empty string."""
    if DASHBOARD.exists():
        return DASHBOARD.read_text(encoding="utf-8")
    return ""


def _write_dashboard(content: str) -> None:
    DASHBOARD.write_text(content, encoding="utf-8")


def dashboard_log_event(event: str, filename: str) -> None:
    """Append one row to the Recent Activity Log table in Dashboard.md."""
    with _dashboard_lock:
        content = _read_dashboard()
        new_row = f"| {now_str()} | {event} | `{filename}` |"

        # Insert new row directly after the table header separator
        content = content.replace(
            "| Timestamp | Event | File |\n|-----------|-------|------|",
            f"| Timestamp | Event | File |\n|-----------|-------|------|\n{new_row}",
        )
        _write_dashboard(content)


def dashboard_update_stats() -> None:
    """Rewrite the Summary table with current in-memory counters."""
    with _dashboard_lock:
        content = _read_dashboard()

        # Build a fresh stats table block
        table_lines = [
            "| Metric | Value |",
            "|--------|-------|",
        ]
        for metric, value in _stats.items():
            table_lines.append(f"| {metric} | {value} |")
        table_lines.append(f"| Last Updated | {now_str()} |")
        new_table = "\n".join(table_lines)

        # Replace the entire Summary section content (preserve the trailing ---)
        content = re.sub(
            r"(## Summary\n).*?(\n---|\Z)",
            lambda m: m.group(1) + "\n" + new_table + "\n" + m.group(2),
            content,
            flags=re.DOTALL,
        )
        _write_dashboard(content)


# ─────────────────────────────────────────────
#  AI PLANNER  (OpenAI → local smart fallback)
# ─────────────────────────────────────────────

_PLAN_PROMPT = """\
You are an AI task planner for a business AI Employee system.
Given the task description below, produce a structured plan.

Reply with EXACTLY this markdown (no extra text):

# AI Task Plan

**Task:**
<one-sentence summary of the task>

## Steps

1. <step>
2. <step>
3. <step>
4. <step>
5. <step>

**Estimated Time:** <realistic estimate>
**Priority:** <URGENT or NORMAL>
**Suggested Tools:** <comma-separated list of relevant tools>

Task description:
{task_text}
"""


def _local_ai_plan(
    task_text: str,
    task_name: str,
    urgent: bool,
    payment: bool,
) -> str:
    """
    Smart local planner.  Analyses keywords in the task text to generate
    context-aware steps, time estimates, priorities, and tool suggestions.
    Returns a fully-formatted plan string.
    """
    corpus = (task_text + " " + task_name).lower()

    def hit(keywords: set) -> bool:
        return any(kw in corpus for kw in keywords)

    EMAIL_KW    = {"email", "reply", "respond", "message", "send", "draft",
                   "write to", "inbox", "outbox"}
    MEETING_KW  = {"meeting", "schedule", "calendar", "call", "zoom", "teams",
                   "invite", "agenda", "standup", "sync"}
    CODE_KW     = {"code", "fix", "bug", "error", "deploy", "build", "test",
                   "debug", "develop", "feature", "pull request", "pr", "commit"}
    REPORT_KW   = {"report", "analysis", "analyse", "analyze", "summary",
                   "review", "audit", "metrics", "kpi", "dashboard"}
    RESEARCH_KW = {"research", "investigate", "find", "look up", "search",
                   "gather", "collect", "study", "explore"}
    DESIGN_KW   = {"design", "mockup", "wireframe", "ui", "ux", "figma",
                   "prototype", "logo", "branding", "layout"}
    DOCUMENT_KW = {"document", "write", "create", "prepare", "update",
                   "edit", "policy", "procedure", "sop", "guide"}
    PAYMENT_KW  = {"payment", "invoice", "pay", "transfer", "purchase",
                   "vendor", "bill", "expense", "refund", "reimbursement"}

    if payment or hit(PAYMENT_KW):
        steps = [
            "Retrieve and review the invoice or payment request in full",
            "Verify vendor details, amounts, and tax information",
            "Cross-reference against approved budget and purchase order",
            "Obtain required sign-offs from authorised approvers",
            "Process payment and file the confirmation receipt",
        ]
        est_time = "24–48 hours (pending approval)"
        tools    = "Accounting software, Email, Approval workflow system"

    elif hit(EMAIL_KW):
        steps = [
            "Read the original email thread and identify all open points",
            "Research any facts or context needed for an accurate reply",
            "Draft a clear, professional response addressing every point",
            "Proofread for tone, accuracy, and completeness",
            "Send the reply and archive the thread for reference",
        ]
        est_time = "30–60 minutes"
        tools    = "Email client, Grammar checker (e.g. Grammarly)"

    elif hit(MEETING_KW):
        steps = [
            "Identify all required attendees and check their availability",
            "Define meeting objectives and prepare a focused agenda",
            "Book a room or create a virtual conferencing link",
            "Send calendar invites with agenda and any pre-read materials",
            "Capture meeting notes and distribute action items afterwards",
        ]
        est_time = "1–2 hours"
        tools    = "Calendar app, Video conferencing tool, Note-taking app"

    elif hit(CODE_KW):
        steps = [
            "Reproduce the issue or clarify the feature requirements precisely",
            "Analyse the root cause or design the solution architecture",
            "Implement the fix or feature with appropriate unit tests",
            "Run the full test suite and conduct a code quality review",
            "Deploy to the target environment and verify behaviour in production",
        ]
        est_time = "2–8 hours"
        tools    = "IDE, Git, CI/CD pipeline, Testing framework"

    elif hit(REPORT_KW):
        steps = [
            "Define the report scope, audience, and required metrics",
            "Gather data from all relevant sources and validate accuracy",
            "Analyse findings and identify key insights or anomalies",
            "Structure the report with charts, tables, and clear narrative",
            "Review, finalise, and distribute to stakeholders",
        ]
        est_time = "3–6 hours"
        tools    = "Spreadsheet software, BI / charting tool, Word processor"

    elif hit(DESIGN_KW):
        steps = [
            "Gather requirements and review existing brand or design assets",
            "Sketch initial concepts and agree on design direction",
            "Create detailed mockups or interactive prototypes",
            "Collect stakeholder feedback and iterate on the design",
            "Export final assets and hand off for development or print",
        ]
        est_time = "4–8 hours"
        tools    = "Figma / Adobe XD, Image editor, Collaboration platform"

    elif hit(RESEARCH_KW):
        steps = [
            "Define the research question, scope, and success criteria",
            "Identify and access primary information sources",
            "Collect, tag, and organise relevant data or findings",
            "Synthesise findings into clear, actionable insights",
            "Document results and present recommendations to stakeholders",
        ]
        est_time = "2–5 hours"
        tools    = "Search engine, Research databases, Spreadsheet, Note-taking app"

    elif hit(DOCUMENT_KW):
        steps = [
            "Clarify the document's purpose, audience, and key messages",
            "Outline the structure and gather all required input materials",
            "Write the first draft following the agreed structure",
            "Review for clarity, accuracy, and adherence to style guidelines",
            "Finalise, version-control, and publish or distribute",
        ]
        est_time = "2–5 hours"
        tools    = "Word processor, Style guide, Version control system"

    else:
        steps = [
            "Clarify the task objectives, deliverables, and constraints",
            "Break down the work into subtasks and set priorities",
            "Execute each subtask systematically, tracking progress",
            "Review all outputs against the original requirements",
            "Archive deliverables and inform relevant stakeholders",
        ]
        est_time = "2–4 hours"
        tools    = "Task manager, Communication tool, Documentation system"

    priority = "URGENT" if urgent else "NORMAL"
    if urgent:
        est_time = est_time.split("(")[0].strip() + " (expedited — URGENT)"

    task_summary = task_text.strip().splitlines()[0][:120] if task_text.strip() else task_name

    return textwrap.dedent(f"""\
        # AI Task Plan

        **Task:**
        {task_summary}

        ## Steps

        1. {steps[0]}
        2. {steps[1]}
        3. {steps[2]}
        4. {steps[3]}
        5. {steps[4]}

        **Estimated Time:** {est_time}
        **Priority:** {priority}
        **Suggested Tools:** {tools}
    """)


def ai_generate_plan(
    task_text: str,
    task_name: str = "",
    urgent: bool = False,
    payment: bool = False,
) -> str:
    """
    Generate an AI Task Plan for *task_text*.

    1. If openai package is installed AND OPENAI_API_KEY is set → OpenAI API.
    2. Otherwise → built-in smart local planner.

    Always returns a fully-formatted markdown string.
    """
    if _OPENAI_IMPORTABLE and OPENAI_API_KEY:
        try:
            client = _openai_lib.OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a precise AI task planner. "
                            "Follow the output format exactly as instructed."
                        ),
                    },
                    {
                        "role": "user",
                        "content": _PLAN_PROMPT.format(task_text=task_text),
                    },
                ],
                temperature=0.4,
                max_tokens=600,
            )
            plan_text = response.choices[0].message.content.strip()
            log(f"  [AI]      Plan generated via OpenAI ({OPENAI_MODEL}).")
            return plan_text
        except Exception as exc:
            log(f"  [AI]      OpenAI unavailable ({exc}). Using local planner.")

    log("  [AI]      Generating plan with local smart planner.")
    return _local_ai_plan(task_text, task_name, urgent, payment)


# ─────────────────────────────────────────────
#  PLAN GENERATOR
# ─────────────────────────────────────────────

def generate_plan(task_file: Path) -> Path:
    """
    Read the task file, call ai_generate_plan(), and write the result to
    Plans/PLAN_<taskname>.md. Returns the path to the generated plan file.
    """
    task_file = task_file.resolve()
    plan_path = PLANS / f"PLAN_{task_file.stem}.md"

    try:
        task_text = task_file.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        task_text = ""

    plan_body = ai_generate_plan(
        task_text=task_text,
        task_name=task_file.stem,
        urgent=is_urgent(task_file),
        payment=is_payment_task(task_file),
    )

    PLANS.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(plan_body, encoding="utf-8")
    log(f"  [PLAN]    Saved → {plan_path.name}")
    return plan_path


# ─────────────────────────────────────────────
#  HUMAN-IN-THE-LOOP  (HITL) APPROVAL SYSTEM
# ─────────────────────────────────────────────

# Additional keywords that trigger the approval gate (beyond payment keywords)
SENSITIVE_KEYWORDS = PAYMENT_KEYWORDS | {
    "delete", "remove", "terminate", "fire", "legal", "lawsuit",
    "contract", "confidential", "private", "sensitive",
}


def _detect_action(task_file: Path, task_text: str) -> tuple[str, str]:
    """
    Infer a human-readable action label and recipient hint from the task.
    Returns (action, recipient).
    """
    corpus = (task_text + " " + task_file.name).lower()

    if any(kw in corpus for kw in {"invoice", "payment", "pay", "transfer", "purchase"}):
        action = "process_payment"
    elif any(kw in corpus for kw in {"email", "send", "reply", "message"}):
        action = "send_email"
    elif any(kw in corpus for kw in {"contract", "legal", "sign"}):
        action = "sign_or_execute_contract"
    elif any(kw in corpus for kw in {"delete", "remove", "terminate"}):
        action = "delete_or_terminate_resource"
    else:
        action = "review_and_execute"

    # Try to find a recipient (email-like token)
    import re as _re
    email_match = _re.search(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}", task_text)
    recipient = email_match.group(0) if email_match else "(see task details)"

    return action, recipient


def generate_approval_request(task_file: Path) -> Path:
    """
    Generate a PENDING_<taskname>.md approval request inside Pending_Approval/.

    The file contains:
      - YAML front-matter with action, recipient, reason, status
      - The original task content for human review
      - Clear instructions: move to Approved/ or Rejected/

    Returns the path to the written approval request file.
    """
    task_file = task_file.resolve()

    try:
        task_text = task_file.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        task_text = "(could not read task content)"

    action, recipient = _detect_action(task_file, task_text)

    # Determine the reason
    corpus = (task_text + " " + task_file.name).lower()
    if any(kw in corpus for kw in {"invoice", "payment", "pay"}):
        reason = "Payment-related task detected — requires human authorisation"
    elif any(kw in corpus for kw in {"contract", "legal"}):
        reason = "Legal / contract action detected — requires human sign-off"
    elif any(kw in corpus for kw in {"delete", "remove", "terminate"}):
        reason = "Destructive action detected — requires human confirmation"
    else:
        reason = "Sensitive task detected — requires human review"

    approval_name = f"PENDING_{task_file.stem}.md"
    approval_path = PENDING_APPROVAL / approval_name

    content = (
        f"---\n"
        f"type: approval_request\n"
        f"action: {action}\n"
        f"recipient: {recipient}\n"
        f"reason: {reason}\n"
        f"original_file: {task_file.name}\n"
        f"requested_at: {now_str()}\n"
        f"status: pending\n"
        f"---\n"
        f"\n"
        f"## Task Details\n"
        f"\n"
        f"{task_text}\n"
        f"\n"
        f"## To Approve\n"
        f"\n"
        f"Move this file to:\n"
        f"\n"
        f"    AI_Employee_Vault/Approved/\n"
        f"\n"
        f"The task will be automatically marked as **Done**.\n"
        f"\n"
        f"## To Reject\n"
        f"\n"
        f"Move this file to:\n"
        f"\n"
        f"    AI_Employee_Vault/Rejected/\n"
        f"\n"
        f"The task will be automatically marked as **Rejected** and archived.\n"
    )

    PENDING_APPROVAL.mkdir(parents=True, exist_ok=True)

    # Avoid collision
    if approval_path.exists():
        stem, suffix = approval_path.stem, approval_path.suffix
        counter = 1
        while approval_path.exists():
            approval_path = PENDING_APPROVAL / f"{stem}_{counter}{suffix}"
            counter += 1

    approval_path.write_text(content, encoding="utf-8")
    log(f"  [HITL]    Approval request created → {approval_path.name}")
    return approval_path


def _handle_approval_decision(raw_path: str, approved: bool) -> None:
    """
    Called when a human moves an approval file into Approved/ or Rejected/.

    - Reads the original_file field from the YAML front-matter.
    - Moves the original task file to Done/ (approved) or Rejected/ (rejected).
    - Moves the approval request itself alongside the decision.
    - Updates dashboard stats and activity log.
    """
    time.sleep(0.5)
    decision_file = Path(raw_path).resolve()

    if not decision_file.exists():
        return
    if is_temp_file(decision_file):
        return

    decision_label = "Approved" if approved else "Rejected"
    log(f"\n{'─' * 55}")
    log(f"  [HITL]    Decision detected: {decision_label} → {decision_file.name}")

    # ── Parse the original_file from YAML front-matter ────────────────────────
    import re as _re
    try:
        text = decision_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        log(f"  [HITL]    Could not read {decision_file.name} — skipping.")
        return

    match = _re.search(r"^original_file:\s*(.+)$", text, _re.MULTILINE)
    original_filename = match.group(1).strip() if match else None

    # ── Resolve where the original task file currently is ─────────────────────
    original_file: Path | None = None
    if original_filename:
        candidate = PENDING_APPROVAL / original_filename
        if candidate.exists():
            original_file = candidate
        else:
            # Search the whole vault in case it was moved already
            for folder in (NEEDS_ACTION, PLANS, DONE, REJECTED):
                candidate = folder / original_filename
                if candidate.exists():
                    original_file = candidate
                    break

    # ── Route based on decision ───────────────────────────────────────────────
    if approved:
        if original_file and original_file.exists():
            moved = safe_move(original_file, DONE)
            log(f"  [HITL]    Original task moved to Done/ → {moved.name}")
        safe_move(decision_file, DONE)
        _stats["Completed Tasks"] += 1
        _stats["Pending Tasks"]   = max(0, _stats["Pending Tasks"] - 1)
        event_label = "Approved → Done"
        log(f"  [HITL]    Task APPROVED and marked Done.")

    else:
        if original_file and original_file.exists():
            moved = safe_move(original_file, REJECTED)
            log(f"  [HITL]    Original task archived in Rejected/ → {moved.name}")
        safe_move(decision_file, REJECTED)
        _stats["Rejected Tasks"] += 1
        _stats["Pending Tasks"]  = max(0, _stats["Pending Tasks"] - 1)
        event_label = "Rejected → Archived"
        log(f"  [HITL]    Task REJECTED and archived.")

    dashboard_log_event(event_label, decision_file.name)
    dashboard_update_stats()
    log(f"{'─' * 55}\n")


class ApprovalHandler(FileSystemEventHandler):
    """
    Watchdog handler that monitors both Approved/ and Rejected/ folders.

    When a file is dropped into either folder (by the human reviewer),
    it reads the approval request, finds the original task, and finalises it.
    """

    def __init__(self, approved: bool):
        super().__init__()
        self.approved = approved          # True = Approved/, False = Rejected/

    def on_created(self, event):
        if event.is_directory:
            return
        _handle_approval_decision(event.src_path, self.approved)

    def on_moved(self, event):
        """Catch atomic editor saves (rename-based moves into the folder)."""
        if event.is_directory:
            return
        _handle_approval_decision(event.dest_path, self.approved)


# ─────────────────────────────────────────────
#  TASK PROCESSOR
# ─────────────────────────────────────────────

def process_task(task_file: Path) -> None:
    """
    Silver Tier processing pipeline for a single task file:
      1. Generate an AI plan.
      2. Enforce payment-approval policy (always takes precedence).
      3. AI classification → route to Needs_Action / Plans / Done.
      4. Update Dashboard.
    """
    task_file = task_file.resolve()
    log(f"  [PROCESS] Processing task: {task_file.name}")

    # Step 1 — Generate AI plan
    generate_plan(task_file)

    # Step 2 — Sensitive-task gate (runs before AI classification)
    #           Generates a PENDING_*.md approval request for human review.
    task_text_lower = ""
    try:
        task_text_lower = task_file.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        pass

    is_sensitive = (
        any(kw in task_file.name.lower() for kw in SENSITIVE_KEYWORDS)
        or any(kw in task_text_lower for kw in SENSITIVE_KEYWORDS)
    )

    if is_sensitive:
        # Keep original task in Pending_Approval and generate approval request
        safe_move(task_file, PENDING_APPROVAL)
        generate_approval_request(PENDING_APPROVAL / task_file.name)
        _stats["Pending Tasks"] += 1
        event_label = "Awaiting Human Approval (HITL)"
        log(f"  [HITL]    Sensitive task — approval request generated.")

    else:
        # Step 3 — AI classification (Silver Tier)
        target_name   = analyze_file(task_file)     # "Needs_Action" | "Plans" | "Done"
        target_folder = BASE_DIR / target_name

        time.sleep(0.3)
        dest = safe_move(task_file, target_folder)

        if target_name == "Done":
            _stats["Completed Tasks"] += 1
        else:
            _stats["Pending Tasks"] += 1

        event_label = f"AI Classified → {target_name}"
        log(f"  [ROUTED]  '{task_file.name}' → {target_name}/")

    # Step 4 — Update dashboard
    dashboard_log_event(event_label, task_file.name)
    dashboard_update_stats()


# ─────────────────────────────────────────────
#  INBOX HANDLER  (watchdog event handler)
# ─────────────────────────────────────────────

def _handle_inbox_file(raw_path: str) -> None:
    """
    Core intake logic — shared by on_created and on_moved.
    raw_path is the string path from the watchdog event.
    """
    # ── Resolve to absolute path immediately (fixes [WinError 2] on Windows) ──
    src_path = Path(raw_path).resolve()

    # Give the OS a moment to finish writing / renaming the file
    time.sleep(0.5)

    # Guard: file might have vanished (temp file already renamed/deleted)
    if not src_path.exists():
        log(f"  [SKIP]    File no longer exists (temp artefact): {src_path.name}")
        return

    # Skip editor temp / lock / hidden files
    if is_temp_file(src_path):
        return

    # Only process files inside Inbox (safety check)
    try:
        src_path.relative_to(INBOX)
    except ValueError:
        return

    sep = "-" * 55
    log(f"\n{sep}")
    log(f"  [INBOX]   New file detected: {src_path.name}")

    # Move Inbox → Needs_Action
    try:
        dest = safe_move(src_path, NEEDS_ACTION)
    except FileNotFoundError:
        log(f"  [SKIP]    File gone before move could complete: {src_path.name}")
        log(f"{sep}\n")
        return
    except Exception as exc:
        log(f"  [ERROR]   Could not move {src_path.name}: {exc}")
        log(f"{sep}\n")
        return

    _stats["Total Tasks"] += 1
    priority_tag = "[URGENT] " if is_urgent(dest) else ""
    log(f"  [QUEUE]   {priority_tag}Moved to Needs_Action → {dest.name}")

    dashboard_log_event("Received → Needs_Action", dest.name)
    dashboard_update_stats()

    # Process the task
    process_task(dest)
    log(f"{sep}\n")


class InboxHandler(FileSystemEventHandler):
    """
    Watchdog handler for the Inbox folder.

    Handles both on_created (new file dropped) and on_moved (editor atomic
    save — write-to-temp then rename-to-final), which is the common save
    pattern used by VS Code, Notepad++, and most Windows editors.
    """

    def on_created(self, event):
        if event.is_directory:
            return
        _handle_inbox_file(event.src_path)

    def on_moved(self, event):
        """
        Fires when an editor saves atomically: temp file → final filename.
        event.dest_path is the final file now sitting in Inbox.
        """
        if event.is_directory:
            return
        dest = Path(event.dest_path).resolve()
        # Only care if the destination landed inside Inbox
        try:
            dest.relative_to(INBOX)
        except ValueError:
            return
        _handle_inbox_file(event.dest_path)


# ─────────────────────────────────────────────
#  BOOT — process any files already in Inbox
# ─────────────────────────────────────────────

def process_existing_inbox() -> None:
    """
    On startup, sweep the Inbox for any files left over from before the
    watcher was running, so nothing is missed.
    """
    # Use resolve() on every path so we always have absolute paths
    existing = sorted(
        (f.resolve() for f in INBOX.iterdir() if f.is_file() and not is_temp_file(f)),
        key=lambda p: (0 if is_urgent(p) else 1, p.stat().st_mtime),
    )
    if not existing:
        return

    log(f"  [BOOT]    Found {len(existing)} pre-existing file(s) in Inbox — processing...")
    for f in existing:
        _handle_inbox_file(str(f))


# ─────────────────────────────────────────────
#  MAIN ENTRY POINT
# ─────────────────────────────────────────────

def main() -> None:
    # Ensure all vault directories exist (idempotent)
    for folder in (INBOX, NEEDS_ACTION, PLANS, PENDING_APPROVAL, APPROVED, REJECTED, DONE):
        folder.mkdir(parents=True, exist_ok=True)

    print("=" * 55)
    print("   Personal AI Employee — Digital FTE")
    print("   Silver Tier  |  AI + Human-in-the-Loop")
    print("=" * 55)
    print(f"   Vault      : {BASE_DIR}")
    print(f"   Inbox      : {INBOX}")
    print(f"   Approvals  : {PENDING_APPROVAL}")
    print(f"   AI Mode    : {'OpenAI (' + OPENAI_MODEL + ')' if (_OPENAI_IMPORTABLE and OPENAI_API_KEY) else 'Local smart planner'}")
    print(f"   Started    : {now_str()}")
    print("=" * 55)
    print("   Drop task files into the Inbox/ folder.")
    print("   Sensitive tasks → Pending_Approval/ (awaiting review).")
    print("   Move PENDING_*.md to Approved/ or Rejected/ to decide.")
    print("   Press Ctrl+C to stop.")
    print("=" * 55 + "\n")

    process_existing_inbox()

    # Start dashboard scheduler in background (updates Dashboard.md every 60 min)
    scheduler = DashboardScheduler()
    scheduler.run_once()          # immediate first run on startup
    scheduler.start_background()  # then every SCHEDULER_INTERVAL_MINUTES

    observer = Observer()
    # Watch Inbox for new tasks
    observer.schedule(InboxHandler(),                   str(INBOX),            recursive=False)
    # Watch Approved/ — human said yes
    observer.schedule(ApprovalHandler(approved=True),   str(APPROVED),         recursive=False)
    # Watch Rejected/ — human said no
    observer.schedule(ApprovalHandler(approved=False),  str(REJECTED),         recursive=False)
    observer.start()

    log("[WATCHER] Observers started.")
    log(f"[WATCHER]   Inbox     : {INBOX}")
    log(f"[WATCHER]   Approved  : {APPROVED}")
    log(f"[WATCHER]   Rejected  : {REJECTED}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("[WATCHER] Shutdown signal received. Stopping...")
        observer.stop()

    observer.join()
    log("[WATCHER] Stopped cleanly. Goodbye.")


if __name__ == "__main__":
    main()
