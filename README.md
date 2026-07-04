# Kirkbot — Agentic Discord AI Bot

A Discord bot built with Python, discord.py, and the Claude API. Instead of every
action being a fixed slash command, Kirkbot's chat interface is a genuine tool-use
agent: it decides for itself when to warn a user, look up warnings, summarize a
channel, remember something about you, or search the web, based on the
conversation — not a hard-coded `if` statement.

## Features
- **Agentic chat** — @mention the bot (or DM it, or use `/ask`) and it can autonomously
  call tools: moderation (warn/check/clear warnings, permission-gated to moderators),
  channel summarization, daily-joke channel management, long-term memory, and live
  web search.
- **Vision** — attach an image to `/ask` or a mention and the bot can see it.
- **Persistent memory** — warnings, joke channels, and per-user remembered facts are
  stored in a local SQLite database (`kirkbot.db`), so they survive a restart.
- **Per-channel conversation history** — the agent keeps a rolling window of recent
  turns per channel for multi-turn context.
- **Daily joke** — a scheduled dark-humor joke posted to a configured channel.
- **Auto-moderation** — an optional banned-word filter.

## Architecture
- `discord_bot.py` — Discord client, event handlers, and slash commands.
- `agent.py` — the agentic tool-use loop: tool schemas, permission checks, and the
  loop that lets Claude call tools until it has a final answer.
- `storage.py` — SQLite persistence for warnings, joke channels, and memory notes.
- `config.py` — tokens and tunable settings.

## How to Run
1. Clone this repo.
2. Install dependencies: `pip install -r requirements.txt`
3. Set your tokens as environment variables (recommended) or edit `config.py` directly:
   ```
   export DISCORD_TOKEN="your-discord-bot-token"
   export ANTHROPIC_API_KEY="your-anthropic-api-key"
   ```
4. Run: `python discord_bot.py`

## Commands
- `/ask [question] [image]` — Ask the AI anything, optionally with an image attached.
- `/summarize` — Summarize the last 30 messages in the channel.
- `/warn @user [reason]` — Warn a user (mods only).
- `/warnings @user` — Check a user's warnings.
- `/clearwarnings @user` — Clear a user's warnings (mods only).
- `/setjokechannel #channel` — Set the channel for daily jokes (mods only).
- `/jokeme` — Get a joke right now.
- `/help` — Show all commands.
- `@mention the bot` (or DM it) — Chat with the agent directly.

## Skills Used
- Python, discord.py
- Claude API — tool use / agentic loops, vision, web search
- SQLite persistence
- Automation & scheduling logic
