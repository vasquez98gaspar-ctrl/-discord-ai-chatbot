# Discord AI Chatbot

A Discord bot built with Python, discord.py, and the Anthropic API — powered by
Claude for conversation, moderation help, and agentic tool use.

## Features
- **Agentic tool use** — @mention the bot or use `/ask` and Claude decides on its
  own when to check a user's warnings, recall what it remembers about someone
  else, pull in recent channel history, or issue a moderation warning, instead
  of you needing to type an exact command. Moderation actions are always
  permission-checked against the real Discord caller, never the model's say-so.
- **Conversation memory** — rolling per-channel chat history plus optional
  long-term per-user facts (Postgres-backed, persists across restarts).
- **Image understanding** — attach an image when you @mention the bot and it
  can see and respond to it.
- **Moderation** — `/warn`, `/warnings`, `/clearwarnings`, auto-mod word filter.
- **Joke of the day** — scheduled daily post plus `/jokeme` on demand.

## Setup
1. Clone this repo
2. Install dependencies: `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN` and
   `ANTHROPIC_API_KEY` (add `DATABASE_URL` too if you want persistent memory —
   e.g. Railway's Postgres add-on provides this automatically)
4. Run: `python discord_bot.py`

## Commands
- `/ask [question]` — Ask the AI anything
- `@mention the bot` — Have a full conversation; it remembers you and can use
  tools on its own (checking warnings, recalling facts about others, pulling
  in recent chat)
- `/summarize` — Summarize the last 30 messages
- `/whatdoyouknow` / `/forgetme` / `/clearmemory` — Manage what the bot remembers
- `/warn @user [reason]` / `/warnings @user` / `/clearwarnings @user` — Moderation (mods only)
- `/setjokechannel` / `/jokeme` — Daily joke setup and on-demand jokes
- `/help` — Show all commands
