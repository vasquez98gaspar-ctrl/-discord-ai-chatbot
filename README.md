# Discord AI Chatbot

A Discord bot built with Python and Discord APIs, focused on
automated interactions, command handling, and agentic AI workflow logic.

## Features
- Automated command handling
- Agentic AI: Claude can call real tools (look up/issue/clear warnings, fetch
  recent channel messages, get server info, tell a joke) instead of only
  generating text, running a plan → act → observe loop until it has an answer
- Per-channel conversation memory so `@mention` chats keep context across turns
- Moderator permission checks are enforced server-side against the invoking
  Discord user, never decided by the model itself
- Workflow automation logic

## Skills Used
- Python
- Discord API
- Automation Logic
- Debugging

## How to Run
1. Clone this repo
2. Install dependencies: `pip install discord.py`
3. Add your bot token
4. Run: `python discord_bot.py`
