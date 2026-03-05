"""
linkedin_poster.py
──────────────────
Silver Tier — Personal AI Employee

Reads new AI-generated plans from AI_Employee_Vault/Plans,
turns each one into a professional LinkedIn-style business update,
simulates posting it, and logs everything to AI_Employee_Vault/Social_Posts.md.

No API key required — posting is fully simulated.

Run standalone:
    python linkedin_poster.py

Or call from scheduler / other modules:
    from linkedin_poster import LinkedInPoster
    LinkedInPoster().run()
"""

import io
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Vault paths ───────────────────────────────────────────────────────────────
VAULT_DIR   = Path(__file__).resolve().parent / "AI_Employee_Vault"
PLANS_DIR   = VAULT_DIR / "Plans"
SOCIAL_LOG  = VAULT_DIR / "Social_Posts.md"
POSTED_LOG  = VAULT_DIR / ".posted_plans.txt"   # hidden tracker — avoids duplicate posts

# ── Post templates by task category ──────────────────────────────────────────
# Each entry: (keyword_set, list_of_post_templates)
# {task} is replaced by the extracted task summary.

_TEMPLATES: list[tuple[set, list[str]]] = [
    (
        {"payment", "invoice", "vendor", "pay", "billing"},
        [
            "Streamlining our financial operations today. {task} — keeping the business running smoothly.",
            "Efficient vendor management is key to business success. Currently working on: {task}.",
            "Finance operations update: {task}. Staying on top of our commitments.",
        ],
    ),
    (
        {"email", "reply", "message", "respond", "communication"},
        [
            "Clear communication drives results. Today's focus: {task}.",
            "Staying responsive and professional. Working on: {task}.",
            "Building stronger client relationships one message at a time. Current task: {task}.",
        ],
    ),
    (
        {"meeting", "schedule", "calendar", "agenda", "sync", "standup"},
        [
            "Collaboration is the engine of progress. Planning: {task}.",
            "Bringing teams together to drive results. Today: {task}.",
            "Great outcomes start with great meetings. Organising: {task}.",
        ],
    ),
    (
        {"strategy", "roadmap", "plan", "initiative", "objective", "goal"},
        [
            "Working on new strategy for project automation. {task}.",
            "Strategic planning is underway. Focused on: {task}.",
            "Building the roadmap for tomorrow's success. Current initiative: {task}.",
        ],
    ),
    (
        {"report", "analysis", "data", "metrics", "kpi", "dashboard"},
        [
            "Data-driven decisions lead to better outcomes. Preparing: {task}.",
            "Turning numbers into insights. Working on: {task}.",
            "Analysis in progress. Committed to evidence-based decision making. Task: {task}.",
        ],
    ),
    (
        {"code", "build", "deploy", "feature", "fix", "bug", "develop"},
        [
            "Shipping quality software, one task at a time. Currently: {task}.",
            "Engineering excellence is a daily practice. Working on: {task}.",
            "Continuous improvement in action. Latest task: {task}.",
        ],
    ),
    (
        {"design", "ui", "ux", "prototype", "mockup", "branding"},
        [
            "Great design creates great experiences. Focused on: {task}.",
            "Design thinking drives innovation. Working on: {task}.",
            "Form and function, hand in hand. Current project: {task}.",
        ],
    ),
    (
        {"research", "investigate", "explore", "study", "gather"},
        [
            "Knowledge is the foundation of great decisions. Researching: {task}.",
            "Deep-dive research underway. Topic: {task}.",
            "Staying ahead through continuous learning. Exploring: {task}.",
        ],
    ),
    (
        {"document", "write", "policy", "procedure", "guide", "sop"},
        [
            "Documentation is the backbone of scalable operations. Working on: {task}.",
            "Clear processes enable consistent results. Documenting: {task}.",
            "Building the knowledge base step by step. Task: {task}.",
        ],
    ),
]

_DEFAULT_TEMPLATES = [
    "Driving operational excellence today. Working on: {task}.",
    "Every task completed is a step forward. Current focus: {task}.",
    "Committed to delivering results. Today's priority: {task}.",
    "Productivity in action. Working on: {task}.",
    "Focused on what matters. Current task: {task}.",
]

_HASHTAG_MAP: dict[str, list[str]] = {
    "payment":  ["#Finance", "#BusinessOperations", "#Efficiency"],
    "strategy": ["#Strategy", "#BusinessGrowth", "#Planning"],
    "email":    ["#Communication", "#Productivity", "#ProfessionalDevelopment"],
    "meeting":  ["#Teamwork", "#Collaboration", "#Leadership"],
    "report":   ["#DataDriven", "#Analytics", "#BusinessIntelligence"],
    "code":     ["#Tech", "#SoftwareDevelopment", "#Innovation"],
    "design":   ["#Design", "#UX", "#Creativity"],
    "research": ["#Research", "#Learning", "#Innovation"],
    "document": ["#Documentation", "#ProcessImprovement", "#KnowledgeManagement"],
}

_DEFAULT_HASHTAGS = ["#Productivity", "#AIAutomation", "#PersonalAIEmployee", "#DigitalFTE"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def log(msg: str) -> None:
    print(f"[{now_str()}]  [LinkedIn]  {msg}", flush=True)


def _extract_task_summary(plan_text: str, plan_stem: str) -> str:
    """
    Pull the task description from the plan file.
    Looks for the '**Task:**' field; falls back to the first heading or filename.
    """
    # Try: **Task:**\n<text>
    match = re.search(r"\*\*Task:\*\*\s*\n([^\n]+)", plan_text)
    if match:
        return match.group(1).strip()

    # Try: first H1 heading
    match = re.search(r"^#\s+(.+)$", plan_text, re.MULTILINE)
    if match and match.group(1).lower() != "ai task plan":
        return match.group(1).strip()

    # Fallback: humanise the filename
    name = plan_stem.replace("PLAN_", "").replace("_", " ").strip()
    return name.capitalize()


def _pick_template(corpus: str) -> str:
    """Select the most relevant post template for the given task corpus."""
    import random
    for keywords, templates in _TEMPLATES:
        if any(kw in corpus for kw in keywords):
            return random.choice(templates)
    return random.choice(_DEFAULT_TEMPLATES)


def _pick_hashtags(corpus: str) -> str:
    """Select 3-4 relevant hashtags for the post."""
    chosen: list[str] = []
    for keyword, tags in _HASHTAG_MAP.items():
        if keyword in corpus:
            chosen.extend(tags)
            if len(chosen) >= 3:
                break
    if not chosen:
        chosen = _DEFAULT_HASHTAGS[:3]
    # Always append the AI Employee brand tag
    if "#PersonalAIEmployee" not in chosen:
        chosen.append("#PersonalAIEmployee")
    return " ".join(chosen[:4])


def generate_post(plan_text: str, plan_name: str) -> str:
    """
    Generate a professional LinkedIn-style post from a plan file.
    Returns the full post string (body + hashtags).
    """
    task_summary = _extract_task_summary(plan_text, plan_name)

    # Capitalise and clean up
    task_summary = task_summary.rstrip(".")
    if task_summary and task_summary[0].islower():
        task_summary = task_summary[0].upper() + task_summary[1:]

    corpus = plan_text.lower() + " " + plan_name.lower()
    template = _pick_template(corpus)
    hashtags = _pick_hashtags(corpus)

    body = template.format(task=task_summary)

    return f"{body}\n\n{hashtags}"


# ── Social_Posts.md log ───────────────────────────────────────────────────────

def _init_social_log() -> None:
    """Create Social_Posts.md with a header if it does not already exist."""
    if not SOCIAL_LOG.exists():
        VAULT_DIR.mkdir(parents=True, exist_ok=True)
        SOCIAL_LOG.write_text(
            "# Social Posts Log\n"
            "> Auto-generated by the LinkedIn Poster. Do not edit manually.\n"
            "\n"
            "---\n"
            "\n",
            encoding="utf-8",
        )


def log_post(plan_name: str, post_text: str) -> None:
    """Append a posted entry to Social_Posts.md."""
    _init_social_log()
    entry = (
        f"## {now_str()}\n"
        f"\n"
        f"**Source plan:** `{plan_name}`\n"
        f"\n"
        f"**Post:**\n"
        f"\n"
        f"> {post_text.replace(chr(10), chr(10) + '> ')}\n"
        f"\n"
        f"**Status:** Simulated (posted successfully)\n"
        f"\n"
        f"---\n"
        f"\n"
    )
    with open(SOCIAL_LOG, "a", encoding="utf-8") as f:
        f.write(entry)


# ── Duplicate tracking ────────────────────────────────────────────────────────

def _load_posted() -> set[str]:
    """Return the set of plan filenames that have already been posted."""
    if not POSTED_LOG.exists():
        return set()
    return set(POSTED_LOG.read_text(encoding="utf-8").splitlines())


def _mark_posted(plan_name: str) -> None:
    """Record a plan filename as posted so it is not posted again."""
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    with open(POSTED_LOG, "a", encoding="utf-8") as f:
        f.write(plan_name + "\n")


# ── Simulated post ────────────────────────────────────────────────────────────

def simulate_post(post_text: str) -> None:
    """
    Simulate posting to LinkedIn.
    Prints the post to the terminal and pauses briefly for realism.
    Replace this function with a real LinkedIn API call when ready.
    """
    print()
    print("=" * 55)
    print("   [SIMULATED LINKEDIN POST]")
    print("=" * 55)
    for line in post_text.splitlines():
        print(f"   {line}")
    print("=" * 55)
    print()
    time.sleep(0.3)   # brief pause — simulates network round-trip


# ── Main poster class ─────────────────────────────────────────────────────────

class LinkedInPoster:
    """
    Scans AI_Employee_Vault/Plans for new plan files,
    generates a LinkedIn post for each one, simulates posting,
    and logs the result to AI_Employee_Vault/Social_Posts.md.
    """

    def run(self) -> int:
        """
        Process all unposted plans.
        Returns the number of posts made in this run.
        """
        if not PLANS_DIR.exists():
            log(f"Plans folder not found: {PLANS_DIR}")
            return 0

        plan_files = sorted(
            f for f in PLANS_DIR.iterdir()
            if f.is_file() and f.suffix.lower() == ".md"
        )

        if not plan_files:
            log("No plan files found in Plans/.")
            return 0

        already_posted = _load_posted()
        new_files = [f for f in plan_files if f.name not in already_posted]

        if not new_files:
            log(f"All {len(plan_files)} plan(s) already posted. Nothing new to share.")
            return 0

        log(f"Found {len(new_files)} new plan(s) to post.")
        posted_count = 0

        for plan_file in new_files:
            log(f"Processing: {plan_file.name}")
            try:
                plan_text = plan_file.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                log(f"  Could not read {plan_file.name}: {exc}")
                continue

            post_text = generate_post(plan_text, plan_file.stem)

            simulate_post(post_text)
            log_post(plan_file.name, post_text)
            _mark_posted(plan_file.name)

            log(f"  Posted and logged: {plan_file.name}")
            posted_count += 1

        log(f"Done. {posted_count} post(s) made this run.")
        log(f"Log saved to: {SOCIAL_LOG}")
        return posted_count


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("   Personal AI Employee — LinkedIn Poster")
    print("   Silver Tier  |  Simulated Posting")
    print("=" * 55)
    print(f"   Plans dir : {PLANS_DIR}")
    print(f"   Post log  : {SOCIAL_LOG}")
    print(f"   Started   : {now_str()}")
    print("=" * 55 + "\n")

    LinkedInPoster().run()
