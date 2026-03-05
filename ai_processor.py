"""
ai_processor.py
───────────────
Silver Tier — Personal AI Employee

Reads a task file and classifies it into one of three destination folders:

    Needs_Action  ->  urgent / time-sensitive tasks
    Plans         ->  project / strategy / planning tasks
    Done          ->  everything else (no immediate action needed)

Classification strategy:
  1. OpenAI API  (if OPENAI_API_KEY env var is set and openai is installed)
  2. Rule-based keyword classifier  (zero-dependency fallback)
"""

import os
from pathlib import Path

# ── Optional OpenAI import ────────────────────────────────────────────────────
try:
    import openai as _openai_lib
    _OPENAI_OK = True
except ImportError:
    _OPENAI_OK = False

# ── Config ────────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL   = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# Valid destination folder names (must match AI_Employee_Vault sub-folders)
VALID_FOLDERS = {"Needs_Action", "Plans", "Done"}

# ── Keyword rules (fallback classifier) ──────────────────────────────────────
_NEEDS_ACTION_KW = {
    "urgent", "asap", "immediately", "critical", "reply", "respond",
    "meeting", "call", "deadline", "overdue", "today", "now", "emergency",
    "action required", "follow up", "followup", "reminder",
}

_PLANS_KW = {
    "plan", "planning", "strategy", "roadmap", "project", "proposal",
    "schedule", "timeline", "milestone", "objective", "goal", "initiative",
    "design", "architecture", "blueprint", "brainstorm", "outline",
}

# ── OpenAI classifier ─────────────────────────────────────────────────────────
_CLASSIFY_PROMPT = """\
You are a task classifier for a business AI Employee system.
Read the task below and reply with EXACTLY ONE of these three words:

    Needs_Action
    Plans
    Done

Rules:
- Needs_Action -> urgent, time-sensitive, requires immediate reply or action
- Plans        -> project planning, strategy, roadmaps, proposals, scheduling
- Done         -> routine info, completed work, low-priority, no immediate action

Reply with only the folder name — no punctuation, no explanation.

Task:
{task_text}
"""


def _openai_classify(task_text: str) -> str:
    """Call OpenAI to classify the task. Returns a folder name string."""
    client   = _openai_lib.OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": "You are a precise task classifier. Reply with only the folder name.",
            },
            {
                "role": "user",
                "content": _CLASSIFY_PROMPT.format(task_text=task_text[:1500]),
            },
        ],
        temperature=0.0,
        max_tokens=10,
    )
    answer = response.choices[0].message.content.strip()
    # Validate — default to Done if the model returns something unexpected
    return answer if answer in VALID_FOLDERS else "Done"


def _rule_classify(task_text: str, filename: str) -> str:
    """
    Keyword-based classifier.  Fast, offline, zero dependencies.
    Checks both the file content and the filename.
    """
    corpus = (task_text + " " + filename).lower()

    # Needs_Action takes highest priority
    if any(kw in corpus for kw in _NEEDS_ACTION_KW):
        return "Needs_Action"

    if any(kw in corpus for kw in _PLANS_KW):
        return "Plans"

    return "Done"


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_file(filepath: Path) -> str:
    """
    Analyze *filepath* and return the name of the destination folder.

    Returns one of: "Needs_Action" | "Plans" | "Done"

    Strategy:
      1. Try OpenAI if OPENAI_API_KEY is set and openai package is installed.
      2. Fall back to the rule-based keyword classifier automatically.
    """
    filepath = Path(filepath).resolve()

    # Read task content
    try:
        task_text = filepath.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        task_text = ""

    # ── Try OpenAI first ──────────────────────────────────────────────────────
    if _OPENAI_OK and OPENAI_API_KEY:
        try:
            folder = _openai_classify(task_text)
            print(f"  [AI-CLASS] OpenAI classified '{filepath.name}' -> {folder}")
            return folder
        except Exception as exc:
            print(f"  [AI-CLASS] OpenAI error ({exc}). Using rule-based classifier.")

    # ── Rule-based fallback ───────────────────────────────────────────────────
    folder = _rule_classify(task_text, filepath.name)
    print(f"  [AI-CLASS] Rule-based classified '{filepath.name}' -> {folder}")
    return folder
