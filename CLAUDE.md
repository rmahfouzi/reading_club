# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This project runs a gamified daily-reading accountability club for a
Persian-speaking Telegram group, using **two separate Telegram bots**:

- `@clubKetab_bot` — OpenClaw-connected, group admin, runs the two
  `skills/*/SKILL.md` cron skills (scoring/moderation + nightly reminder).
  It has no open DM surface.
- The **report bot** (`report_bot/report_bot.py`) — a standalone Python
  process, separate bot token, regular (non-admin) group member. Its only
  job is collecting members' daily check-ins via DM and appending to
  `daily_logs.txt`. Deliberately has no LLM in the loop.

The OpenClaw "code" is almost entirely Markdown system prompts
(`skills/*/SKILL.md`); the report bot is a normal Python script. There is no
build/lint pipeline for either. "Testing" a change means: run/restart
`report_bot.py` and DM it, or wait for / manually trigger the relevant cron
skill and inspect the resulting JSON/log files.

## Repo layout and runtime data flow

```
reading_club/
├── reading_db.json         # persistent: config + per-user lives/streaks/history
├── daily_logs.txt          # transient: today's CHECKIN/READING_NOTE entries (JSON Lines, gitignored)
├── message_counts.json     # transient: per-day per-user DM quota counters (gitignored)
├── log_archive/             # one archived daily_logs file per processed day (gitignored)
├── welcome_message.md       # Persian text pinned in the Telegram group (manual copy/paste)
├── report_bot/
│   ├── report_bot.py        # standalone DM check-in bot (separate Telegram bot/token)
│   ├── requirements.txt
│   └── .env.example
└── skills/
    ├── reading-club-enforcer/SKILL.md  # 08:00 Europe/Stockholm cron — scoring & moderation
    └── reading-club-reminder/SKILL.md  # 21:00 Europe/Stockholm cron — group nudge
```

Data flows in one direction through the day:

1. A member DMs the report bot → `report_bot.py` appends `CHECKIN`/
   `READING_NOTE` JSON lines to `daily_logs.txt`.
2. The next morning, `reading-club-enforcer` reads `daily_logs.txt`, folds it
   into `reading_db.json` (per-user `days_read`, `lives`, streaks), then
   archives the raw log to `log_archive/daily_logs_<date>.txt` and truncates
   `daily_logs.txt`.
3. On Sundays (processing the week that just ended), the enforcer also runs
   weekly scoring: deducts/restores lives, posts a Persian leaderboard, and
   kicks members at 0 lives.
4. Each evening, `reading-club-reminder` posts one fixed nudge message — pure
   broadcast, no state.

**Timezone split is intentional and load-bearing**: user-facing day
boundaries (quota resets, check-in dedup, weekly scoring) are computed in
**Asia/Tehran**, while all OpenClaw crons run on **Europe/Stockholm**
wall-clock time. The enforcer's "today" run always processes *yesterday's*
(Tehran) log. `report_bot.py` uses `Asia/Tehran` directly for the same
boundaries (see `today_tehran()`).

## The report bot (`report_bot/report_bot.py`)

A `python-telegram-bot` (v21, async) `ConversationHandler` — deterministic
Python, no LLM, so there's no prompt-injection surface. Key points:

- Every reply is a fixed Persian template; conversation flow uses inline
  keyboard buttons (یس/نه, رد کردن) rather than free-text parsing.
- First step on every `/start`/message is a `getChatMember` check against
  `TELEGRAM_GROUP_CHAT_ID` — the report bot must be a member of that group.
- 8-messages/day quota per user (Asia/Tehran calendar day) in
  `message_counts.json`, plus a same-day duplicate-CHECKIN guard read
  straight from `daily_logs.txt`.
- Writes the exact same `CHECKIN` / `READING_NOTE` JSON-line schema the
  enforcer expects (see below) — if you change this schema, update both
  `report_bot.py`'s `append_log_entry()` and the enforcer's parsing logic
  and schema docs together.
- Config is via env vars (`REPORT_BOT_TOKEN`, `TELEGRAM_GROUP_CHAT_ID`,
  optional `LOG_FILE`/`MSG_COUNT_FILE`/`MAX_DAILY_MESSAGES`) — see
  `report_bot/.env.example`.

## The OpenClaw cron skills

Each `SKILL.md` is a frontmatter block (name, schedule, tool allowlist, env)
followed by a `<system>` prompt and `<tools>` reference.

- **`reading-club-enforcer`** (cron, `0 8 * * *` Europe/Stockholm)
  - Has the admin tools: `fs.writeFile`, `telegram.sendMessage`,
    `telegram.kickChatMember`, plus `fs.readFile`/`telegram.getChatMember`.
  - Runs unattended, no user input — un-hijackable by design, since the
    report bot (which does talk to users) only ever appends to
    `daily_logs.txt` and never reaches OpenClaw or these tools.
  - Six ordered tasks (load state → process log → Sunday weekly enforcement →
    Sunday leaderboard messages → kick 0-life users → archive + persist).
    The `reading_db.json` user-record schema is fully specified in the
    prompt; if you change it, update both the schema block and any code that
    reads existing `reading_db.json` files (migration is manual — there's no
    migration tooling).
  - All group-facing messages are fixed Persian templates — the prompt
    explicitly forbids improvising wording.

- **`reading-club-reminder`** (cron, `0 21 * * *` Europe/Stockholm)
  - Only tool: `telegram.sendMessage`. Sends exactly one fixed Persian
    message, no state read/write. References the report bot's username,
    `@ketabyaar_bot`.

When editing a `SKILL.md`, preserve the `tools:` allowlist as the actual
security boundary — never give a cron skill a `trigger: dm`, and never add
`telegram.sendMessage`/admin tools to anything reachable by user input.

## Game rules (source of truth: `reading_db.json` `config` block + enforcer logic)

- Goal: 15 min/day, ≥5 days/week (`min_weekly_days`).
- Start with 3 lives (`starting_lives`/`max_lives`).
- Weekly count < 5 → lose 1 life; weekly count == 7 (perfect week) builds
  `consecutive_perfect_weeks`; 2 perfect weeks restore 1 life (capped at
  `max_lives`).
- 0 lives → automatic kick (`telegram.kickChatMember`), `is_active` set to
  `false`.

If you change any of these numbers, change them in `reading_db.json`'s
`config` block — the enforcer reads `MIN_WEEKLY_DAYS`, `MAX_LIVES`,
`PERFECT_WEEKS_FOR_LIFE_RESTORE` etc. from its own `env:` frontmatter, so both
places need to stay in sync, and `welcome_message.md` documents these numbers
to members in Persian — update it too if the rules change.

## Language

All user-facing bot output (DM replies from `report_bot.py`, group messages,
leaderboards, `welcome_message.md`) is **Persian (Farsi)**. Keep new strings
in Persian and consistent in tone with existing templates.

## Deploying changes

There's no CI.
- OpenClaw skill changes: `openclaw skills install /path/to/reading_club/skills/<skill-name>`
- Report bot changes: restart the `report_bot.py` process (it's a plain
  long-running script — see `README.md` for how it's run).

See `README.md` for full from-scratch setup (both bot tokens, group chat ID,
env vars, runtime file bootstrap).
