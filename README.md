# Reading Club — OpenClaw Telegram Bot

A gamified daily-reading accountability system for a Persian-speaking Telegram
group, built on OpenClaw.

## Project Structure

```
reading_club/
├── README.md
├── reading_db.json              # persistent club database (users, lives, streaks)
├── welcome_message.md           # Persian welcome/rules text to pin in the group
└── skills/
    ├── reading-bot-chat/
    │   └── SKILL.md             # conversational DM skill (data-isolation sandbox)
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

## Architecture Overview

### 1. `reading-bot-chat` (conversational skill)
- Triggered by direct messages to the bot.
- Asks the user: "Did you read for 15 minutes today?"
- If yes: writes a `CHECKIN` entry to `daily_logs.txt`, then optionally collects
  a book title and one-sentence takeaway (`READING_NOTE` entries).
- **Security sandbox**: only has `fs.readFile` and `fs.appendFile`. No Telegram
  admin tools, no DB access, no execution. All user input is treated as raw data —
  prompt-injection attempts are explicitly ignored.
- Enforces an 8-messages-per-day quota per user (Asia/Tehran calendar day).
- All user-facing replies are in Persian.

### 2. `reading-club-enforcer` (cron skill)
- Runs daily at **08:00 Europe/Stockholm** (processes the *previous* calendar
  day's log, since users operate in Asia/Tehran time).
- Reads `daily_logs.txt`, updates `reading_db.json` (attendance, lives, streaks).
- On Sundays (i.e. the processed week ending Sunday): evaluates the 5-day/week
  goal, deducts/restores lives, posts a Persian leaderboard + warnings to the group.
- Kicks members who reach 0 lives via `telegram.kickChatMember`.
- Has admin Telegram tools (`telegram.sendMessage`, `telegram.kickChatMember`,
  `telegram.getChatMember`) — completely separate from the chat skill, runs with
  no user interaction, so it cannot be hijacked via prompt injection.
- Archives each day's log to `log_archive/daily_logs_<date>.txt` and clears
  `daily_logs.txt`.

### 3. `reading-club-reminder` (cron skill)
- Runs daily at **21:00 Europe/Stockholm**.
- Posts a single fixed Persian reminder to the group, prompting members who
  haven't checked in yet to DM the bot before the day ends.
- Only has `telegram.sendMessage` — no DB access, no admin actions, no user
  interaction.

### Game Rules (encoded in `reading_db.json` config + enforcer logic)
- Goal: 15 min/day, ≥5 days/week.
- Start with 3 lives (❤️❤️❤️).
- Miss the weekly 5-day goal → lose 1 life.
- 0 lives → automatic kick from the group.
- 2 consecutive perfect weeks (7/7 days) → restore 1 life (max 3).

## Setup on a New Machine

### 1. Telegram Bot
1. Create a bot via `@BotFather` → `/newbot` → save the bot token.
2. Disable Group Privacy mode (`/mybots` → Bot Settings → Group Privacy → off).
3. Set description/commands as desired.

### 2. Telegram Group
1. Create the group, add the bot as administrator with **Ban users** + **Send
   messages** + **Delete messages** permissions.
2. Get the group's chat ID (forward a message to `@userinfobot`) — looks like
   `-1001234567890`.
3. Pin `welcome_message.md` (replace `@[YOUR_BOT_USERNAME]` with your bot's
   actual username first).

### 3. OpenClaw Channel
```bash
openclaw channels add --type telegram --token YOUR_BOT_TOKEN --name telegram
openclaw channels list      # confirm it appears
openclaw channels info telegram
```
Complete any pairing step OpenClaw prompts for, linking the channel to the
agent/workspace where the skills below will be installed.

### 4. Project Files
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
TELEGRAM_BOT_TOKEN=<your bot token>
TELEGRAM_GROUP_CHAT_ID=-1001234567890
```

### 5. Install Skills
```bash
openclaw skills install /path/to/reading_club/skills/reading-bot-chat
openclaw skills install /path/to/reading_club/skills/reading-club-enforcer
openclaw skills install /path/to/reading_club/skills/reading-club-reminder
openclaw skills list      # confirm both are active
```

### 6. Go Live
- DM the bot to test the check-in flow.
- The enforcer runs automatically at 08:00 Europe/Stockholm daily.
- Announce the group and share the pinned welcome message with members.
