# 🤖 Personal AI Employee

> A fully autonomous AI-powered digital employee that monitors your inbox, processes tasks, generates plans, sends LinkedIn posts, briefs your CEO — and never takes a day off.

---

## 📌 Overview

**Personal AI Employee** is a multi-tier Python automation system that acts as your own AI digital worker. It watches for incoming tasks from multiple sources (files, Gmail, WhatsApp), classifies them intelligently, generates action plans, routes tasks through an approval workflow, and reports everything back to you — all automatically.

Built for the **Hackathon 0** challenge across three tiers:

| Tier | Focus |
|------|-------|
| 🥉 Bronze | Filesystem watcher + auto task routing |
| 🥈 Silver | AI classification + Gmail + LinkedIn + Scheduler + HITL approvals |
| 🥇 Gold | WhatsApp monitor + CEO briefing + Weekly reports + Audit log + Ralph Loop |

---

## 🗂️ Project Structure

```
AI_Employee_Project/
│
├── filesystem_watcher.py   # Core: watches Inbox, routes tasks, generates plans
├── ai_processor.py         # AI task classifier (OpenAI + keyword fallback)
├── base_watcher.py         # Abstract base class for all watchers
│
├── gmail_watcher.py        # Gmail API watcher (Gold Tier)
├── whatsapp_watcher.py     # WhatsApp Web monitor via Playwright (Gold Tier)
│
├── scheduler.py            # Dashboard auto-updater (runs every 60 min)
├── linkedin_poster.py      # Auto-generates LinkedIn posts from plans
├── ceo_briefing.py         # Weekly executive briefing generator
├── report_generator.py     # Detailed weekly productivity report
├── audit_logger.py         # Thread-safe audit trail for all AI actions
├── ralph_loop.py           # Autonomous task-processing loop
│
├── requirements.txt
└── AI_Employee_Vault/
    ├── Inbox/              # Drop tasks here
    ├── Needs_Action/       # Urgent tasks awaiting action
    ├── Plans/              # AI-generated task plans
    ├── Pending_Approval/   # Tasks waiting for human review
    ├── Approved/           # Human-approved tasks
    ├── Rejected/           # Rejected tasks
    ├── Done/               # Completed tasks
    ├── Reports/            # Weekly productivity reports
    ├── Dashboard.md        # Live task dashboard
    ├── CEO_Briefing.md     # Latest CEO briefing
    ├── Audit_Log.md        # Full AI action audit trail
    └── Social_Posts.md     # LinkedIn post history
```

---

## ⚙️ Installation

### 1. Clone the repository

```bash
git clone https://github.com/Hina-naureen/ai-employee-project.git
cd ai-employee-project
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Install Playwright browser (for WhatsApp watcher)

```bash
playwright install chromium
```

### 4. Set environment variables (optional)

```bash
# Enable OpenAI-powered planning and classification
export OPENAI_API_KEY=your_key_here

# Gmail watcher settings
export GMAIL_POLL_INTERVAL=60
export GMAIL_IMPORTANT_ONLY=true

# WhatsApp watcher settings
export WA_POLL_INTERVAL=15
export WA_HEADLESS=false

# Scheduler interval
export SCHEDULER_INTERVAL_MINUTES=60
```

---

## 🚀 Quick Start

### Run the main filesystem watcher

```bash
python filesystem_watcher.py
```

Drop any `.txt` or `.md` file into `AI_Employee_Vault/Inbox/` and watch it get automatically classified, planned, and routed.

### Run the autonomous Ralph Loop

```bash
python ralph_loop.py "Process all tasks in Needs_Action"

# Keep watching for new tasks continuously
python ralph_loop.py --watch
```

### Generate reports manually

```bash
python report_generator.py    # Weekly productivity report
python ceo_briefing.py        # CEO executive briefing
python linkedin_poster.py     # Post plan updates to LinkedIn (simulated)
```

### Start the Gmail watcher

```bash
python gmail_watcher.py
```
> First run opens a browser for Gmail OAuth sign-in. Token is saved for future runs.

### Start the WhatsApp watcher

```bash
python whatsapp_watcher.py
```
> First run opens Chromium — scan the QR code with your phone. Session is saved automatically.

---

## 🔄 How It Works

```
                    ┌─────────────┐
         Files ───► │    Inbox    │
        Emails ───► │  (watched)  │
      WhatsApp ───► └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ AI Processor│  ◄── OpenAI GPT-4o-mini
                    │ (classify)  │  ◄── Keyword fallback
                    └──────┬──────┘
                           │
           ┌───────────────┼────────────────┐
           │               │                │
    ┌──────▼──────┐  ┌─────▼──────┐  ┌─────▼──────┐
    │Needs_Action │  │   Plans    │  │    Done    │
    └──────┬──────┘  └─────┬──────┘  └────────────┘
           │               │
    ┌──────▼──────┐  ┌─────▼──────┐
    │  Sensitive? │  │  LinkedIn  │
    │  → Pending  │  │   Poster   │
    │  Approval   │  └────────────┘
    └──────┬──────┘
           │
    ┌──────▼──────────────┐
    │  Human reviews file  │
    │  Moves to Approved/  │
    │  or Rejected/        │
    └──────┬──────────────┘
           │
    ┌──────▼──────┐
    │    Done     │
    └─────────────┘
           │
    ┌──────▼──────────────────────────────────────┐
    │  Audit_Log.md  │  Dashboard.md  │  Reports/  │
    └─────────────────────────────────────────────┘
```

---

## 🧩 Module Reference

### `filesystem_watcher.py`
Watches `Inbox/` for new files using `watchdog`. Detects sensitive keywords (payment, invoice) and routes them to `Pending_Approval/` for human sign-off. All other tasks go through the AI classifier.

### `ai_processor.py`
Classifies task files into `Needs_Action`, `Plans`, or `Done`.
- Uses **OpenAI GPT-4o-mini** if `OPENAI_API_KEY` is set
- Falls back to **keyword rules** automatically — no internet required

### `gmail_watcher.py` *(Gold Tier)*
Polls Gmail for unread IMPORTANT emails. Converts each one into a structured Markdown task file in `Needs_Action/`. Detects priority via STARRED label, finds attachments, identifies reply threads.

### `whatsapp_watcher.py` *(Gold Tier)*
Uses Playwright to monitor WhatsApp Web. Detects unread messages containing trigger keywords (`urgent`, `invoice`, `payment`, `help`) and saves them as task files.

**Trigger keywords → priority:**
| Keywords | Priority |
|----------|----------|
| urgent, emergency, asap | critical |
| invoice, payment, help, deadline | high |
| meeting, reminder, confirm | normal |

### `ralph_loop.py` *(Gold Tier)*
Autonomous self-driving loop. Picks up every file in `Needs_Action/`, classifies it, moves it to `Plans/` or `Done/`, and logs everything to `Audit_Log.md`. Runs until the queue is empty.

```bash
python ralph_loop.py                    # process and exit
python ralph_loop.py --watch            # keep watching for new tasks
```

### `scheduler.py`
Runs in a background thread. Rebuilds `Dashboard.md` every 60 minutes. Auto-generates `Weekly_Report` and `CEO_Briefing` every 7 days.

### `linkedin_poster.py`
Reads new plan files from `Plans/`, generates professional LinkedIn-style posts, and logs them to `Social_Posts.md`. Posting is simulated — no API key required. Duplicate-safe via `.posted_plans.txt` tracker.

### `ceo_briefing.py` *(Gold Tier)*
Generates a concise executive briefing saved to `CEO_Briefing.md`. Surfaces key issues, opportunities, and action items requiring CEO attention.

### `report_generator.py` *(Gold Tier)*
Produces a detailed Markdown productivity report with:
- Task completion rate
- Category breakdown (Email, Payment, Meeting, Strategy, etc.)
- Daily completion trend
- Bottleneck analysis (tasks idle >24 hrs)
- AI recommendations

### `audit_logger.py` *(Gold Tier)*
Thread-safe module that logs every AI action to `Audit_Log.md`. Import and call from any module:

```python
from audit_logger import log_action
log_action("Task analyzed", "urgent_task.txt", "Needs_Action")
```

### `base_watcher.py`
Abstract base class inherited by `GmailWatcher` and `WhatsAppWatcher`. Provides shared vault paths, `safe_write()`, `log()`, and lifecycle hooks.

---

## 🔐 Human-in-the-Loop (HITL) Approval

Sensitive tasks (containing `payment`, `invoice`, `vendor`, `wire transfer`, etc.) are never processed automatically. Instead:

1. Task is moved to `Pending_Approval/` as `PENDING_<name>.md`
2. Human reviews the file
3. Human moves it to `Approved/` or `Rejected/`
4. Watcher detects the decision and finalises the task

---

## 📊 Dashboard

`AI_Employee_Vault/Dashboard.md` is rebuilt every 60 minutes and shows:

```markdown
## Summary
| Pending Tasks       | 3 |
| Plans Created       | 5 |
| Completed Tasks     | 12 |
| Awaiting Approval   | 1 |
| System Status       | ACTIVE |
```

---

## 📋 CEO Briefing Sample

```markdown
# CEO Weekly Briefing
> Week ending: March 05, 2026

## Operational Summary
| Tasks Completed     | 13 |
| Pending Tasks       | 2  |
| Plans Created       | 3  |
| Awaiting Approval   | 1  |

## Key Issues
- delayed client response
- pending vendor payment

## Opportunities
- new project planning underway
- client onboarding proposal

## Action Items for CEO
- [APPROVAL NEEDED] vendor invoice
```

---

## 🛠️ Tech Stack

| Technology | Purpose |
|-----------|---------|
| Python 3.11+ | Core language |
| watchdog | Filesystem event monitoring |
| OpenAI GPT-4o-mini | AI task classification + planning |
| Google Gmail API | Email monitoring |
| Playwright / Chromium | WhatsApp Web automation |
| google-auth-oauthlib | Gmail OAuth2 authentication |

---

## 🌐 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | Enables OpenAI features (optional) |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model to use |
| `GMAIL_POLL_INTERVAL` | `60` | Seconds between Gmail checks |
| `GMAIL_MAX_RESULTS` | `10` | Max emails fetched per cycle |
| `GMAIL_IMPORTANT_ONLY` | `true` | Only fetch IMPORTANT emails |
| `WA_POLL_INTERVAL` | `15` | Seconds between WhatsApp checks |
| `WA_HEADLESS` | `false` | Run browser without UI |
| `WA_SESSION_DIR` | `./whatsapp_session` | WhatsApp session storage path |
| `SCHEDULER_INTERVAL_MINUTES` | `60` | Dashboard refresh interval |
| `REPORT_LOOKBACK_DAYS` | `7` | Days of history for reports |
| `RALPH_POLL_INTERVAL` | `10` | Ralph loop watch-mode interval |
| `RALPH_WATCH` | `false` | Keep Ralph running after queue empties |

---

## 👩‍💻 Author

**Hina Naureen**
- GitHub: [@Hina-naureen](https://github.com/Hina-naureen)
- Project: [ai-employee-project](https://github.com/Hina-naureen/ai-employee-project)

---

*Built for Hackathon 0 — Personal AI Employee Challenge*
