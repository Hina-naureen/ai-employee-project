"""
ralph_loop.py
──────────────
Gold Tier — Personal AI Employee

Autonomous task processing loop ("Ralph Loop").

Continuously checks AI_Employee_Vault/Needs_Action, classifies each
task, moves it to the correct folder (Plans / Done), and repeats until
every task has been processed.  Every action is written to Audit_Log.md.

Run:
    python ralph_loop.py
    python ralph_loop.py "Process all tasks in Needs_Action"

Or invoke via the /ralph-loop skill command.

Loop behaviour:
    1. Scan Needs_Action for unprocessed task files.
    2. For each file: classify -> move -> audit-log.
    3. After one pass, if Needs_Action is still non-empty, repeat.
    4. When empty, print a summary and exit (or keep watching if
       --watch flag is passed).

Environment variables (all optional):
    RALPH_POLL_INTERVAL    seconds between watch-mode scans  (default: 10)
    RALPH_WATCH            "true" to keep running after queue clears (default: false)
"""

import io
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import os

# ── Vault paths ────────────────────────────────────────────────────────────────
VAULT_DIR    = Path(__file__).resolve().parent / "AI_Employee_Vault"
NEEDS_ACTION = VAULT_DIR / "Needs_Action"
PLANS        = VAULT_DIR / "Plans"
DONE         = VAULT_DIR / "Done"

# ── Config ─────────────────────────────────────────────────────────────────────
POLL_INTERVAL = int(os.environ.get("RALPH_POLL_INTERVAL", "10"))
WATCH_MODE    = os.environ.get("RALPH_WATCH", "false").lower() == "true"

# ── Optional imports ───────────────────────────────────────────────────────────
try:
    from ai_processor import analyze_file as _analyze_file
    _AI_AVAILABLE = True
except ImportError:
    _AI_AVAILABLE = False

from audit_logger import log_action, log_system


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log(msg: str) -> None:
    print(f"[{_now_str()}]  [Ralph]  {msg}", flush=True)


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
    """Move src into dest_dir, avoiding filename collisions."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        stem, suffix = src.stem, src.suffix
        counter = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    retries = 5
    for attempt in range(1, retries + 1):
        try:
            shutil.move(str(src), str(dest))
            return dest
        except PermissionError:
            if attempt == retries:
                raise
            time.sleep(0.3 * attempt)

    return dest


def _classify(task_file: Path) -> str:
    """
    Return destination folder name for *task_file*.
    Uses AI classifier if available, otherwise keyword rules.
    """
    if _AI_AVAILABLE:
        try:
            return _analyze_file(task_file)
        except Exception as exc:
            _log(f"  AI classifier error ({exc}) — falling back to rules.")

    # Built-in keyword fallback (no external dependency)
    corpus = (task_file.read_text(encoding="utf-8", errors="ignore")
              + " " + task_file.name).lower()

    needs_action_kw = {
        "urgent", "asap", "critical", "immediately", "emergency",
        "deadline", "overdue", "today", "reply", "respond", "meeting",
        "follow up", "followup", "action required",
    }
    plans_kw = {
        "plan", "strategy", "roadmap", "project", "proposal",
        "schedule", "milestone", "objective", "goal", "initiative",
        "design", "blueprint", "brainstorm",
    }

    if any(kw in corpus for kw in needs_action_kw):
        return "Needs_Action"
    if any(kw in corpus for kw in plans_kw):
        return "Plans"
    return "Done"


# ── Core processing ────────────────────────────────────────────────────────────

def process_task(task_file: Path) -> tuple[str, Path]:
    """
    Classify and move a single task file.
    Returns (destination_folder_name, new_path).
    """
    destination = _classify(task_file)

    # Files classified as Needs_Action are already in Needs_Action —
    # we treat them as Done (no further triage needed at this stage).
    if destination == "Needs_Action":
        destination = "Done"

    dest_dir = VAULT_DIR / destination
    new_path = _safe_move(task_file, dest_dir)

    return destination, new_path


def run_pass() -> dict:
    """
    Process every file currently in Needs_Action.
    Returns a stats dict: {processed, moved_to_plans, moved_to_done, errors}.
    """
    tasks = _task_files(NEEDS_ACTION)
    stats = {"processed": 0, "moved_to_plans": 0, "moved_to_done": 0, "errors": 0}

    if not tasks:
        _log("Needs_Action is empty — nothing to process.")
        return stats

    _log(f"Found {len(tasks)} task(s) in Needs_Action. Processing...")

    for task_file in tasks:
        _log(f"  Processing: {task_file.name}")
        try:
            destination, new_path = process_task(task_file)

            _log(f"    -> Moved to {destination}/")
            log_action(
                action="Task processed by Ralph Loop",
                file=task_file.name,
                result=destination,
                source="RalphLoop",
            )

            stats["processed"] += 1
            if destination == "Plans":
                stats["moved_to_plans"] += 1
            else:
                stats["moved_to_done"] += 1

        except Exception as exc:
            _log(f"    ERROR processing {task_file.name}: {exc}")
            log_action(
                action="Task processing FAILED",
                file=task_file.name,
                result=f"Error: {exc}",
                source="RalphLoop",
            )
            stats["errors"] += 1

    return stats


# ── Main loop class ────────────────────────────────────────────────────────────

class RalphLoop:
    """
    Autonomous task-processing loop.

    One-shot mode (default):
        Drains Needs_Action completely, then exits.

    Watch mode (RALPH_WATCH=true or --watch flag):
        After draining, keeps polling every POLL_INTERVAL seconds
        for new arrivals.  Stop with Ctrl+C.
    """

    def __init__(self, instruction: str = "", watch: bool = WATCH_MODE):
        self.instruction = instruction or "Process all tasks in Needs_Action"
        self.watch       = watch
        self._total      = {"processed": 0, "moved_to_plans": 0, "moved_to_done": 0, "errors": 0}

    def _print_banner(self) -> None:
        print("=" * 55)
        print("   Personal AI Employee — Ralph Autonomous Loop")
        print("   Gold Tier  |  Self-Driving Task Processor")
        print("=" * 55)
        print(f"   Instruction : {self.instruction}")
        print(f"   Watch mode  : {'yes (Ctrl+C to stop)' if self.watch else 'no (exits when queue is empty)'}")
        print(f"   Needs_Action: {NEEDS_ACTION}")
        print(f"   Audit log   : {VAULT_DIR / 'Audit_Log.md'}")
        print(f"   Started     : {_now_str()}")
        print("=" * 55 + "\n")

    def _accumulate(self, stats: dict) -> None:
        for key in self._total:
            self._total[key] += stats.get(key, 0)

    def _print_summary(self) -> None:
        print()
        print("=" * 55)
        print("   Ralph Loop — Run Complete")
        print("=" * 55)
        print(f"   Tasks processed  : {self._total['processed']}")
        print(f"   Moved to Plans   : {self._total['moved_to_plans']}")
        print(f"   Moved to Done    : {self._total['moved_to_done']}")
        print(f"   Errors           : {self._total['errors']}")
        print(f"   Finished         : {_now_str()}")
        print("=" * 55)

    def run(self) -> None:
        self._print_banner()
        log_system(
            f"Ralph Loop started — instruction: {self.instruction!r}",
            source="RalphLoop",
        )

        try:
            if self.watch:
                _log("Watch mode active. Polling for tasks...")
                while True:
                    stats = run_pass()
                    self._accumulate(stats)
                    _log(f"Pass complete. Waiting {POLL_INTERVAL}s for new tasks...")
                    time.sleep(POLL_INTERVAL)
            else:
                # One-shot: keep running passes until queue is empty
                pass_num = 1
                while True:
                    _log(f"--- Pass {pass_num} ---")
                    remaining_before = len(_task_files(NEEDS_ACTION))
                    if remaining_before == 0:
                        _log("Queue is empty. Nothing to do.")
                        break

                    stats = run_pass()
                    self._accumulate(stats)

                    remaining_after = len(_task_files(NEEDS_ACTION))
                    if remaining_after == 0:
                        _log("All tasks processed. Queue is now empty.")
                        break
                    if stats["processed"] == 0:
                        _log("No progress made — stopping to avoid infinite loop.")
                        break
                    pass_num += 1

        except KeyboardInterrupt:
            _log("Stopped by user (Ctrl+C).")

        log_system(
            f"Ralph Loop finished — "
            f"processed={self._total['processed']} "
            f"errors={self._total['errors']}",
            source="RalphLoop",
        )
        self._print_summary()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Accept optional instruction as a CLI argument
    # e.g.  python ralph_loop.py "Process all tasks in Needs_Action"
    import argparse

    parser = argparse.ArgumentParser(
        description="Ralph — Autonomous task-processing loop for the AI Employee."
    )
    parser.add_argument(
        "instruction",
        nargs="?",
        default="Process all tasks in Needs_Action",
        help="Human-readable instruction (logged to Audit_Log.md).",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        default=WATCH_MODE,
        help="Keep running after queue empties (poll for new tasks).",
    )
    args = parser.parse_args()

    RalphLoop(instruction=args.instruction, watch=args.watch).run()
