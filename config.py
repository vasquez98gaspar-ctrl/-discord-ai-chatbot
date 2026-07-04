"""Kirkbot configuration. Fill in your tokens and tune settings here."""
import os

# ─────────────────────────────────────────────
#  🔑  KEYS — prefer environment variables, fall back to the literals below
# ─────────────────────────────────────────────
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "enter here")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "enter api key here")

# ─────────────────────────────────────────────
#  ⚙️  SETTINGS
# ─────────────────────────────────────────────
BOT_NAME = "Kirkbot"
BOT_PERSONALITY = (
    "You are Kirkbot, a helpful, friendly, and capable Discord assistant. "
    "Keep answers concise and conversational. "
    "Use Discord markdown when helpful (bold, code blocks, etc.). "
    "Never be rude or produce harmful content."
)
MOD_ROLE_NAME = "Moderator"   # Role name that can use mod commands/tools
MAX_WARNINGS = 3              # Warnings before auto-kick suggestion
SUMMARIZE_LIMIT = 30           # Messages to pull for /summarize

# Model used for the agentic loop (tool use, chat, vision) and for one-off generations (jokes)
MODEL = "claude-sonnet-4-5"

# ─────────────────────────────────────────────
#  😂  JOKE OF THE DAY SETTINGS
# ─────────────────────────────────────────────
JOKE_TIME = (15, 0, 0)   # 3 PM UTC = 8 AM PDT (Oregon)
JOKE_STYLE = (
    "dark humor — edgy, morbid, and unexpected twists. "
    "Make it genuinely funny, not just offensive. "
    "Keep it to 2-4 sentences max."
)

# ─────────────────────────────────────────────
#  🚨  MODERATION — bad word list
#      Empty by design — vulgarity is allowed!
#      Add specific slurs or truly harmful words here if you want to filter anything.
# ─────────────────────────────────────────────
BAD_WORDS: list[str] = []

# ─────────────────────────────────────────────
#  🧠  PERSISTENCE
# ─────────────────────────────────────────────
DB_PATH = os.environ.get("KIRKBOT_DB_PATH", "kirkbot.db")

# ─────────────────────────────────────────────
#  🕵️  AGENT LOOP
# ─────────────────────────────────────────────
MAX_AGENT_STEPS = 6     # cap tool-use round trips per request, avoids runaway loops
HISTORY_TURNS = 20       # rolling conversation window kept per channel
