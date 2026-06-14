# Reading Club — OpenClaw Telegram Bot

A gamified daily-reading accountability system for a Persian-speaking Telegram
group, built on OpenClaw — plus a small standalone Python bot for collecting
daily check-ins.

## Project Structure

```
reading_club/
├── README.md
├── reading_db.json              # persistent club database (users, lives, streaks)
├── welcome_message.md           # Persian welcome/rules text to pin in the group
├── report_bot/                  # standalone Python bot — collects daily check-ins
│   ├── report_bot.py
│   ├── requirements.txt
│   └── .env.example
└── skills/
    ├── reading-club-enforcer/
    │   └── SKILL.md             # automated daily cron skill
    └── reading-club-reminder/
        └── SKILL.md             # automated daily reminder cron skill
```

Two runtime files are created automatically and are NOT checked in (create empty
versions on the new machine — see Setup step 4):
- `daily_logs.txt` — append-only daily check-in log (JSON Lines)
- `message_counts.json` — per-day message counters for spam limiting
- `log_archive/` — directory where the enforcer archives each day's log

## Two Bots

This project deliberately uses **two separate Telegram bots**:

- **`@clubKetab_bot`** — the OpenClaw-connected bot. It is administrator of
  the group and runs the `reading-club-enforcer` and `reading-club-reminder`
  cron skills (scoring, leaderboard, kicks, nightly nudge). It is **not**
  open to user DMs for this project.
- **The report bot** (`report_bot/report_bot.py`) — a separate bot/token,
  added to the group as a regular (non-admin) member. Its only job is to
  receive members' daily "did you read 15 minutes?" DMs and append entries
  to `daily_logs.txt`. It runs as a plain Python process, entirely outside
  OpenClaw, with no LLM in the loop — every reply is a fixed Persian
  template and every branch is deterministic code, so there is no
  prompt-injection surface.

Keeping these separate means the OpenClaw-connected bot/agent never has an
open DM surface that strangers can talk to.

## Architecture Overview

### 1. `report_bot/report_bot.py` (standalone Python bot)
- Polls Telegram for DMs to the report bot.
- On every message, first checks `getChatMember` against the reading club
  group to confirm the sender is a member; non-members get a polite refusal
  and nothing is written to any file.
- Enforces an 8-messages-per-day quota per user (Europe/Stockholm calendar
  day), tracked in `message_counts.json`.
- Conversation (via inline-button keyboards, not free-text parsing):
  - "Did you read 15 minutes today?" → یس/نه
  - If yes and not already checked in today: appends a `CHECKIN` entry to
    `daily_logs.txt`, then optionally collects a book title and one-sentence
    takeaway (`READING_NOTE` entries).
  - If no: sends an encouragement message, writes nothing.
- All user-facing replies are in Persian.

### 2. `reading-club-enforcer` (OpenClaw cron skill)
- Runs daily at **08:00 Europe/Stockholm** (processes the *previous* calendar
  day's log; both the report bot and this skill use Europe/Stockholm for all
  day-boundary calculations).
- Reads `daily_logs.txt`, updates `reading_db.json` (attendance, lives, streaks).
- On Sundays (i.e. the processed week ending Sunday): evaluates the 5-day/week
  goal, deducts/restores lives, posts a Persian leaderboard + warnings to the group.
- Kicks members who reach 0 lives via `telegram.kickChatMember`.
- Has admin Telegram tools (`telegram.sendMessage`, `telegram.kickChatMember`,
  `telegram.getChatMember`) — runs with no user interaction, so it cannot be
  hijacked via prompt injection.
- Archives each day's log to `log_archive/daily_logs_<date>.txt` and clears
  `daily_logs.txt`.

### 3. `reading-club-reminder` (OpenClaw cron skill)
- Runs daily at **21:00 Europe/Stockholm**.
- Posts a single fixed Persian reminder to the group, prompting members who
  haven't checked in yet to DM the report bot before the day ends.
- Only has `telegram.sendMessage` — no DB access, no admin actions, no user
  interaction.

### Game Rules (encoded in `reading_db.json` config + enforcer logic)
- Goal: 15 min/day, ≥5 days/week.
- Start with 3 lives (❤️❤️❤️).
- Miss the weekly 5-day goal → lose 1 life.
- 0 lives → automatic kick from the group.
- 2 consecutive perfect weeks (7/7 days) → restore 1 life (max 3).

## Setup on a New Machine

### 1. `@clubKetab_bot` (main, OpenClaw-connected)
1. Create a bot via `@BotFather` → `/newbot` → save the bot token.
2. Disable Group Privacy mode (`/mybots` → Bot Settings → Group Privacy → off).
3. Set description/commands as desired.

### 2. Report Bot (standalone)
1. Create a **second**, separate bot via `@BotFather` → `/newbot` → save its
   token (this is `REPORT_BOT_TOKEN`, distinct from `TELEGRAM_BOT_TOKEN`).
2. No special privacy/admin settings needed — it will be a regular group
   member, just enough to call `getChatMember`.

### 3. Telegram Group
1. Create the group, add `@clubKetab_bot` as administrator with **Ban users**
   + **Send messages** + **Delete messages** permissions.
2. Add the report bot to the group as a regular member (no admin rights).
3. Get the group's chat ID (forward a message to `@userinfobot`) — looks like
   `-1001234567890`.
4. Pin `welcome_message.md` — already references the report bot,
   `@ketabyaar_bot` (the reminder skill template also references it).

### 4. OpenClaw Channel (for `@clubKetab_bot` only)
```bash
openclaw channels add --type telegram --token YOUR_BOT_TOKEN --name telegram
openclaw channels list      # confirm it appears
openclaw channels info telegram
```
Complete any pairing step OpenClaw prompts for, linking the channel to the
agent/workspace where the skills below will be installed.

### 5. Project Files
Copy the whole `reading_club/` directory to the new machine, then create the
runtime files that aren't checked in:
```bash
cd /path/to/reading_club
touch daily_logs.txt
echo "{}" > message_counts.json
mkdir -p log_archive
```

Fill in `reading_db.json`:
```json
"group_chat_id": "-1001234567890"
```

Set environment variables (e.g. in `.env`):
```bash
TELEGRAM_BOT_TOKEN=<your @clubKetab_bot token>
TELEGRAM_GROUP_CHAT_ID=-1001234567890
```

### 6. Install OpenClaw Skills
```bash
openclaw skills install /path/to/reading_club/skills/reading-club-enforcer
openclaw skills install /path/to/reading_club/skills/reading-club-reminder
openclaw skills list      # confirm both are active
```

### 7. Run the Report Bot
```bash
cd /path/to/reading_club/report_bot
python3 -m venv --without-pip venv && source venv/bin/activate
python -m ensurepip --upgrade || curl -sS https://bootstrap.pypa.io/get-pip.py | python  # if ensurepip is unavailable
pip install -r requirements.txt
cp .env.example .env   # fill in REPORT_BOT_TOKEN and TELEGRAM_GROUP_CHAT_ID
```

It only needs `daily_logs.txt` and `message_counts.json` — both live in the
`reading_club/` project root by default.

**Run it as a systemd user service** so it survives reboots/power
outages and restarts automatically on crash. Create
`~/.config/systemd/user/reading-club-report-bot.service`:

```ini
[Unit]
Description=Reading Club Report Bot (@ketabyaar_bot)
After=network-online.target
Wants=network-online.target
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
WorkingDirectory=/path/to/reading_club/report_bot
ExecStart=/path/to/reading_club/report_bot/venv/bin/python /path/to/reading_club/report_bot/report_bot.py
EnvironmentFile=/path/to/reading_club/report_bot/.env
Restart=always
RestartSec=5
KillMode=control-group

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now reading-club-report-bot.service
loginctl enable-linger "$USER"   # so the service starts at boot, even logged out
```

Check status/logs with:
```bash
systemctl --user status reading-club-report-bot.service
journalctl --user -u reading-club-report-bot.service -f
```

### 8. Admin Reports
Both `reading-club-enforcer` and `reading-club-reminder` send a daily status
report (and any error notices) to `ADMIN_USER_ID` via `@clubKetab_bot`.

Set in `.env`:
```bash
TELEGRAM_ADMIN_USER_ID=<your Telegram user id>
```

**Important:** Telegram bots can only message a user who has previously
started a conversation with that bot. The admin (`@r.mahfoozi`) must send
`/start` to `@clubKetab_bot` once — otherwise these reports will fail to
deliver (the failure is recorded in the enforcer's `errors` list but does
not stop the run).

### 9. Go Live
- DM the report bot to test the check-in flow.
- The enforcer runs automatically at 08:00 Europe/Stockholm daily.
- Announce the group and share the pinned welcome message with members.
