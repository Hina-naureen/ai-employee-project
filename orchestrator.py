"""
orchestrator.py
────────────────
Platinum Tier — Personal AI Employee

Master orchestrator.  Starts every agent and watcher as a supervised
daemon thread.  Any thread that crashes is automatically restarted
after a configurable delay.  Graceful shutdown on Ctrl+C.

Services started in parallel:
    1. FilesystemWatcher  — Inbox monitor + HITL handlers + Scheduler
    2. GmailWatcher       — Gmail API poller (skipped if not configured)
    3. WhatsAppWatcher    — Playwright WhatsApp monitor (skipped if not installed)
    4. RalphLoop          — Autonomous Needs_Action processor (watch mode)
    5. CloudAgent         — AI enrichment, social drafts, reports
    6. LocalAgent         — Approval surfacing, stale-task detection

Run:
    python orchestrator.py

Stop:
    Ctrl+C  — all services shut down cleanly

Environment variables (all optional):
    ORCH_STATUS_INTERVAL   seconds between status board prints  (default: 120)
    ORCH_RESTART_DELAY     seconds before restarting a dead thread (default: 10)
    ORCH_SKIP_GMAIL        "true" to skip GmailWatcher           (default: false)
    ORCH_SKIP_WHATSAPP     "true" to skip WhatsAppWatcher        (default: false)
"""

import io
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Vault paths ────────────────────────────────────────────────────────────────
VAULT_DIR   = Path(__file__).resolve().parent / "AI_Employee_Vault"
LOGS_DIR    = VAULT_DIR / "Logs"
IN_PROGRESS = VAULT_DIR / "In_Progress"

# ── Config ─────────────────────────────────────────────────────────────────────
STATUS_INTERVAL = int(os.environ.get("ORCH_STATUS_INTERVAL",  "120"))
RESTART_DELAY   = int(os.environ.get("ORCH_RESTART_DELAY",    "10"))
SKIP_GMAIL      = os.environ.get("ORCH_SKIP_GMAIL",     "false").lower() == "true"
SKIP_WHATSAPP   = os.environ.get("ORCH_SKIP_WHATSAPP",  "false").lower() == "true"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log(msg: str) -> None:
    print(f"[{_now_str()}]  [Orchestrator]  {msg}", flush=True)


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


# ── Service thread wrapper ─────────────────────────────────────────────────────

class ServiceThread:
    """
    Wraps a callable in a supervised daemon thread.

    If the callable raises an exception or returns, the thread is
    automatically restarted after RESTART_DELAY seconds (as long as
    the orchestrator is still running).
    """

    def __init__(self, name: str, target, restart_delay: int = RESTART_DELAY):
        self.name          = name
        self._target       = target
        self._restart_delay= restart_delay
        self._thread: threading.Thread | None = None
        self._running      = False
        self._start_count  = 0
        self._last_error: str = ""

    def _run_supervised(self) -> None:
        while self._running:
            self._start_count += 1
            _log(f"Starting service: {self.name} (attempt #{self._start_count})")
            _write_platinum_log(f"[Orchestrator] Service START: {self.name} (#{self._start_count})")
            try:
                self._target()
                # Target returned cleanly — check if we should restart
                if self._running:
                    _log(f"{self.name} exited cleanly. Restarting in {self._restart_delay}s...")
                    time.sleep(self._restart_delay)
            except Exception as exc:
                self._last_error = str(exc)
                if self._running:
                    _log(f"[ERROR] {self.name} crashed: {exc}")
                    _write_platinum_log(f"[Orchestrator] Service CRASH: {self.name} — {exc}")
                    _log(f"Restarting {self.name} in {self._restart_delay}s...")
                    time.sleep(self._restart_delay)

    def start(self) -> None:
        self._running = True
        self._thread  = threading.Thread(
            target=self._run_supervised,
            name=self.name,
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> str:
        if self.is_alive():
            return "RUNNING"
        return "STOPPED"


# ── Service factory functions ──────────────────────────────────────────────────

def _start_filesystem_watcher():
    """Start the core filesystem watcher (includes Scheduler automatically)."""
    from filesystem_watcher import main
    main()


def _start_gmail_watcher():
    from gmail_watcher import GmailWatcher
    GmailWatcher().start()


def _start_whatsapp_watcher():
    from whatsapp_watcher import WhatsAppWatcher
    WhatsAppWatcher().start()


def _start_ralph_loop():
    from ralph_loop import RalphLoop
    RalphLoop(instruction="Orchestrator: continuous watch mode", watch=True).run()


def _start_cloud_agent():
    from cloud_agent import CloudAgent
    CloudAgent().start()


def _start_local_agent():
    from local_agent import LocalAgent
    LocalAgent().start()


# ── Status board ──────────────────────────────────────────────────────────────

def _print_status(services: list[ServiceThread]) -> None:
    w = 55
    print()
    print("=" * w)
    print("   Platinum AI Employee — Service Status Board")
    print(f"   {_now_str()}")
    print("=" * w)
    for svc in services:
        state = svc.status()
        indicator = "OK" if state == "RUNNING" else "!!"
        error_hint = f"  (last error: {svc._last_error[:40]}...)" if svc._last_error and state != "RUNNING" else ""
        print(f"   [{indicator}] {svc.name:<22} {state}{error_hint}")

    # Vault snapshot
    print()
    vault_counts = []
    for name in ("Inbox", "Needs_Action", "In_Progress", "Pending_Approval", "Plans", "Done", "Rejected"):
        folder = VAULT_DIR / name
        count  = len(list(folder.iterdir())) if folder.exists() else 0
        vault_counts.append(f"{name}: {count}")
    print("   Vault: " + "  |  ".join(vault_counts))
    print("=" * w)
    print()


# ── Vault setup ────────────────────────────────────────────────────────────────

def _ensure_vault() -> None:
    """Create all required vault directories including Platinum-tier folders."""
    folders = [
        "Inbox", "Needs_Action", "In_Progress", "Plans",
        "Pending_Approval", "Approved", "Rejected", "Done",
        "Reports", "Logs",
    ]
    for name in folders:
        (VAULT_DIR / name).mkdir(parents=True, exist_ok=True)


# ── Main orchestrator ──────────────────────────────────────────────────────────

class Orchestrator:
    """
    Platinum Tier master orchestrator.

    Starts all agents and watchers as supervised daemon threads.
    Prints a live status board every STATUS_INTERVAL seconds.
    """

    def __init__(self):
        self._services: list[ServiceThread] = []
        self._running = False

    def _build_services(self) -> list[ServiceThread]:
        services = [
            ServiceThread("FilesystemWatcher", _start_filesystem_watcher),
            ServiceThread("RalphLoop",         _start_ralph_loop),
            ServiceThread("CloudAgent",        _start_cloud_agent),
            ServiceThread("LocalAgent",        _start_local_agent),
        ]

        if not SKIP_GMAIL:
            services.append(ServiceThread("GmailWatcher", _start_gmail_watcher))
        else:
            _log("GmailWatcher skipped (ORCH_SKIP_GMAIL=true).")

        if not SKIP_WHATSAPP:
            services.append(ServiceThread("WhatsAppWatcher", _start_whatsapp_watcher))
        else:
            _log("WhatsAppWatcher skipped (ORCH_SKIP_WHATSAPP=true).")

        return services

    def run(self) -> None:
        _ensure_vault()

        print("=" * 55)
        print("   Personal AI Employee — Platinum Orchestrator")
        print("   Always-On Cloud AI Employee")
        print("=" * 55)
        print(f"   Vault         : {VAULT_DIR}")
        print(f"   Logs          : {LOGS_DIR / 'Audit_Log.md'}")
        print(f"   Status board  : every {STATUS_INTERVAL}s")
        print(f"   Auto-restart  : after {RESTART_DELAY}s on crash")
        print(f"   Gmail         : {'SKIP' if SKIP_GMAIL else 'ENABLED'}")
        print(f"   WhatsApp      : {'SKIP' if SKIP_WHATSAPP else 'ENABLED'}")
        print(f"   Started       : {_now_str()}")
        print("=" * 55)
        print("   Press Ctrl+C to stop all services.\n")

        _write_platinum_log("[Orchestrator] System START")

        self._services = self._build_services()
        self._running  = True

        # Start all services
        for svc in self._services:
            svc.start()
            time.sleep(1)   # stagger startup to avoid resource contention

        _log(f"All {len(self._services)} service(s) started.")

        last_status_time = time.time()

        try:
            while self._running:
                time.sleep(5)

                # Periodic status board
                if time.time() - last_status_time >= STATUS_INTERVAL:
                    _print_status(self._services)
                    last_status_time = time.time()

        except KeyboardInterrupt:
            _log("Shutdown signal received (Ctrl+C). Stopping all services...")

        finally:
            self._running = False
            for svc in self._services:
                svc.stop()
            _write_platinum_log("[Orchestrator] System STOP")
            _log("All services signalled to stop. Goodbye.")
            print()
            _print_status(self._services)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    Orchestrator().run()
