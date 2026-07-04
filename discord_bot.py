"""
╔══════════════════════════════════════════════╗
║         AI Discord Bot — Powered by Claude   ║
╚══════════════════════════════════════════════╝

SETUP (do this once):
  1. pip install -r requirements.txt
  2. Copy .env.example to .env and fill in your tokens
  3. Run: python discord_bot.py

COMMANDS:
  /ask [question]     — Ask the AI anything
  /summarize          — Summarize the last 30 messages
  /warn @user [reason]— Warn a user (mods only)
  /warnings @user     — Check a user's warnings
  /clearwarnings @user— Clear a user's warnings (mods only)
  /setjokechannel     — Set the channel for daily jokes (mods only)
  /jokeme             — Get a joke right now!
  /whatdoyouknow      — See what the bot remembers about you
  /forgetme           — Make the bot forget you
  /clearmemory        — Wipe the bot's memory of this channel
  /help               — Show all commands

  @mention the bot    — Chat with it directly! It can also decide on its own
                         to check warnings, recall what it knows about other
                         users, or pull in recent chat history when relevant.
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import anthropic
import asyncio
import os
import base64
from collections import defaultdict
from datetime import datetime

try:
    import psycopg2
except ImportError:
    psycopg2 = None  # bot still runs without DB (memory just won't persist)

# Load a local .env file if present (for testing on your PC).
# On Railway, real environment variables are used instead.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed — that's fine on the cloud

# ─────────────────────────────────────────────
#  🔑  API KEYS
#
#  These are read from environment variables so
#  your secrets NEVER live in the code (safe for
#  GitHub + cloud hosting like Railway).
#
#  • For LOCAL testing on your PC, you can either
#    set environment variables OR temporarily paste
#    your keys into the fallback strings below.
#  • For Railway/cloud, set them in the dashboard
#    under "Variables" — do NOT paste them here.
# ─────────────────────────────────────────────
DISCORD_TOKEN     = os.environ.get("DISCORD_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

if not DISCORD_TOKEN or not ANTHROPIC_API_KEY:
    raise SystemExit(
        "❌ Missing keys! Set DISCORD_TOKEN and ANTHROPIC_API_KEY as environment "
        "variables (Railway dashboard) or in a local .env file. See README."
    )

# Database connection string — auto-provided by Railway when you add Postgres.
# If it's missing, the bot still runs but won't remember people across restarts.
DATABASE_URL = os.environ.get("DATABASE_URL", "")
MEMORY_ENABLED = bool(DATABASE_URL and psycopg2)

# ─────────────────────────────────────────────
#  ⚙️  SETTINGS  (customize these!)
# ─────────────────────────────────────────────
BOT_NAME         = "ServerBot"          # What the bot calls itself
BOT_PERSONALITY  = (
    "You are a helpful, friendly Discord bot assistant. "
    "Keep answers concise and conversational. "
    "You remember what people said earlier in the conversation and reference it naturally. "
    "Use Discord markdown when helpful (bold, code blocks, etc.). "
    "Never be rude or produce harmful content. "
    "Each incoming message is prefixed with the speaker's name in brackets, like "
    "'[SomeUser]: hey what's up'. That prefix tells you WHO is talking — use it to "
    "tell people apart and address them by name naturally. Never copy the bracket "
    "format into your own replies; just talk normally. "
    "You have tools available to check a user's warnings, recall what you remember "
    "about someone other than the person you're talking to, look up recent chat "
    "history to summarize, and issue moderation warnings (moderators only, and it "
    "will simply be refused if the requester isn't one). Use them naturally when "
    "the conversation calls for it — don't announce that you're 'using a tool', "
    "just answer as if you already knew."
)
MEMORY_LIMIT     = 20                   # How many past messages the bot remembers per channel
MOD_ROLE_NAME    = "Moderator"          # Role name that can use mod commands
MAX_WARNINGS     = 3                    # Warnings before auto-kick suggestion
SUMMARIZE_LIMIT  = 30                   # Messages to pull for /summarize
MAX_TOOL_ROUNDS  = 4                    # Safety cap on chained tool calls per reply

# ─────────────────────────────────────────────
#  😂  JOKE OF THE DAY SETTINGS
# ─────────────────────────────────────────────
JOKE_TIME        = (15, 0, 0)           # 3 PM UTC = 8 AM PDT (Oregon) ✅
JOKE_STYLE       = (
    "dark humor — edgy, morbid, and unexpected twists. "
    "Make it genuinely funny, not just offensive. "
    "Keep it to 2-4 sentences max."
)

# ─────────────────────────────────────────────
#  🚨  MODERATION — bad word list
#      Empty by design — vulgarity is allowed!
#      Add specific slurs or truly harmful words
#      here if you ever want to filter anything.
# ─────────────────────────────────────────────
BAD_WORDS = []   # Nothing filtered — swear freely 🤘

# ─────────────────────────────────────────────
#  🖼️  Detect the REAL image type from file bytes
#      (Discord's label is sometimes wrong, and
#       Claude's API requires the correct type)
# ─────────────────────────────────────────────
def detect_image_type(raw: bytes) -> str:
    """Return the correct media_type by sniffing the file's magic bytes."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return ""  # unknown / unsupported


# ─────────────────────────────────────────────
#  BOT SETUP
# ─────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# In-memory warning storage  {guild_id: {user_id: [list of warnings]}}
warnings_db: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))

# Joke channel storage  {guild_id: channel_id}
joke_channels: dict[int, int] = {}

# Conversation memory per channel  {channel_id: [{"role": ..., "content": ...}]}
conversation_history: dict[int, list] = defaultdict(list)


# ─────────────────────────────────────────────
#  🛠️  AGENTIC TOOLS
#      Claude decides on its own when to call
#      these mid-conversation instead of the
#      user needing to know an exact command.
# ─────────────────────────────────────────────
TOOLS = [
    {
        "name": "tell_joke",
        "description": (
            "Generate a fresh joke on demand. Use this when someone asks for a joke "
            "or the conversation calls for comic relief."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "recall_user_facts",
        "description": (
            "Look up what you remember about a specific Discord user in this server "
            "by their display name. Use this when asked what you know/remember about "
            "someone other than the person currently talking to you."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "display_name": {"type": "string", "description": "The user's display name or username"}
            },
            "required": ["display_name"],
        },
    },
    {
        "name": "get_warnings",
        "description": "Check how many moderation warnings a user has and why, by display name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "display_name": {"type": "string", "description": "The user's display name or username"}
            },
            "required": ["display_name"],
        },
    },
    {
        "name": "issue_warning",
        "description": (
            "Issue an official moderation warning to a user. Only works if the person "
            "talking to you is a moderator or admin — otherwise it is refused."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "display_name": {"type": "string", "description": "The user's display name or username"},
                "reason": {"type": "string", "description": "Why they're being warned"},
            },
            "required": ["display_name", "reason"],
        },
    },
    {
        "name": "summarize_recent_messages",
        "description": "Fetch the recent conversation in this channel so you can summarize it when asked.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many recent messages to pull (default 30, max 100)"}
            },
        },
    },
]


def find_members(guild: discord.Guild, name: str) -> list:
    """Find guild members matching a display name or username (case-insensitive).

    Returns exact matches if any exist; otherwise falls back to substring matches.
    Callers must handle the multi-match case explicitly rather than guessing.
    """
    if not guild or not name:
        return []
    needle = name.strip().lstrip("@").lower()
    exact = [m for m in guild.members if m.display_name.lower() == needle or m.name.lower() == needle]
    if exact:
        return exact
    return [m for m in guild.members if needle in m.display_name.lower()]


def resolve_single_member(guild: discord.Guild, name: str):
    """Resolve a name to exactly one member, or return (None, error_message) otherwise."""
    matches = find_members(guild, name)
    if not matches:
        return None, "No matching user found in this server."
    if len(matches) > 1:
        options = ", ".join(f"{m.display_name} ({m.name})" for m in matches[:8])
        return None, f"That name matches multiple people: {options}. Ask which one they mean."
    return matches[0], None


def is_moderator(member: discord.Member) -> bool:
    """True if this member can use mod-only commands/tools."""
    if member.guild_permissions.administrator:
        return True
    mod_role = discord.utils.get(member.guild.roles, name=MOD_ROLE_NAME)
    return mod_role in member.roles if mod_role else False


async def execute_tool(name: str, tool_input: dict, ctx: dict) -> str:
    """Run one tool call and return its result as text for Claude to read.

    Permission checks happen here against the real Discord caller in `ctx`,
    never against anything the model itself claims — so a non-mod can't talk
    the bot into issuing a warning just by asking it to.
    """
    guild = ctx.get("guild")

    if name == "tell_joke":
        return await generate_joke()

    if name == "recall_user_facts":
        member, error = resolve_single_member(guild, tool_input.get("display_name", ""))
        if error:
            return error
        facts = await asyncio.to_thread(get_user_facts, member.id)
        return facts or f"Nothing specific remembered about {member.display_name} yet."

    if name == "get_warnings":
        member, error = resolve_single_member(guild, tool_input.get("display_name", ""))
        if error:
            return error
        user_warnings = warnings_db[guild.id][member.id]
        if not user_warnings:
            return f"{member.display_name} has no warnings."
        lines = [f"- {w['reason']} ({w['time'][:10]})" for w in user_warnings]
        return f"{member.display_name} has {len(user_warnings)} warning(s):\n" + "\n".join(lines)

    if name == "issue_warning":
        if not ctx.get("is_mod"):
            return "Permission denied: only moderators or admins can issue warnings."
        member, error = resolve_single_member(guild, tool_input.get("display_name", ""))
        if error:
            return error
        author = ctx.get("author")
        if author is not None and member.id == author.id:
            return "Refusing to warn the requester themselves — that's not a real moderation action."
        if member.bot:
            return "Refusing to warn a bot account."
        reason = tool_input.get("reason") or "No reason given"
        warnings_db[guild.id][member.id].append({
            "reason": reason,
            "time": datetime.utcnow().isoformat(),
            "by": str(ctx.get("author")),
        })
        count = len(warnings_db[guild.id][member.id])
        try:
            await member.send(
                f"⚠️ You received a warning in **{guild.name}**.\n"
                f"**Reason:** {reason}\nYou now have **{count}** warning(s)."
            )
        except discord.Forbidden:
            pass
        return f"Warning issued to {member.display_name} ({count}/{MAX_WARNINGS}). Reason: {reason}"

    if name == "summarize_recent_messages":
        channel = ctx.get("channel")
        limit = tool_input.get("limit") or SUMMARIZE_LIMIT
        limit = max(1, min(int(limit), 100))
        history = []
        async for msg in channel.history(limit=limit):
            if not msg.author.bot:
                history.append(f"{msg.author.display_name}: {msg.content}")
        history.reverse()
        if not history:
            return "No messages found to summarize."
        return "\n".join(history)

    return f"Unknown tool: {name}"


# ─────────────────────────────────────────────
#  HELPER — call Claude
# ─────────────────────────────────────────────
async def ask_claude(prompt: str, system: str = BOT_PERSONALITY, channel_id: int = None,
                      images: list = None, tool_ctx: dict = None) -> str:
    """Send a message to Claude with optional conversation history, images, and tools.

    `images` is a list of dicts: {"media_type": "image/png", "data": "<base64>"}
    `tool_ctx` is a dict with guild/channel/author/is_mod — pass it to let Claude
    call the TOOLS above; omit it (None) for tool-free calls like the joke generator.
    """
    try:
        if images:
            content = []
            for img in images:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img["media_type"],
                        "data": img["data"],
                    },
                })
            content.append({"type": "text", "text": prompt or "What's in this image?"})
            user_message = {"role": "user", "content": content}
        else:
            user_message = {"role": "user", "content": prompt}

        if channel_id is not None:
            conversation_history[channel_id].append(user_message)
            if len(conversation_history[channel_id]) > MEMORY_LIMIT:
                conversation_history[channel_id] = conversation_history[channel_id][-MEMORY_LIMIT:]
            # Tool exchanges stay local to this call — only the final reply is persisted.
            working_messages = list(conversation_history[channel_id])
        else:
            working_messages = [user_message]

        tools_param = TOOLS if tool_ctx is not None else None
        final_text = ""
        for _ in range(MAX_TOOL_ROUNDS):
            kwargs = dict(model="claude-sonnet-4-5", max_tokens=2000, system=system, messages=working_messages)
            if tools_param:
                kwargs["tools"] = tools_param
            response = await asyncio.to_thread(ai_client.messages.create, **kwargs)

            if response.stop_reason == "tool_use":
                working_messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result_text = await execute_tool(block.name, block.input, tool_ctx or {})
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        })
                working_messages.append({"role": "user", "content": tool_results})
                continue

            final_text = "".join(b.text for b in response.content if b.type == "text")
            break
        else:
            final_text = final_text or "I got tangled up using my tools too many times in a row — try again?"

        if channel_id is not None:
            conversation_history[channel_id].append({"role": "assistant", "content": final_text})
        return final_text
    except Exception as e:
        return f"⚠️ AI error: {e}"


# ─────────────────────────────────────────────
#  🧠  PERSISTENT MEMORY  (Postgres database)
#      Stores long-term facts about each user so
#      the bot remembers them across restarts.
# ─────────────────────────────────────────────
def init_db():
    """Create the memory table if it doesn't exist yet."""
    if not MEMORY_ENABLED:
        return
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_facts (
                user_id      BIGINT PRIMARY KEY,
                display_name TEXT,
                facts        TEXT
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("   🧠 Persistent memory: CONNECTED")
    except Exception as e:
        print(f"   ⚠️ Memory DB error on init: {e}")


def get_user_facts(user_id: int) -> str:
    """Return everything the bot knows about a user (or empty string)."""
    if not MEMORY_ENABLED:
        return ""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT facts FROM user_facts WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row and row[0] else ""
    except Exception as e:
        print(f"⚠️ Memory read error: {e}")
        return ""


def save_user_facts(user_id: int, display_name: str, facts: str):
    """Insert or update a user's stored facts."""
    if not MEMORY_ENABLED:
        return
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_facts (user_id, display_name, facts)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET facts = EXCLUDED.facts, display_name = EXCLUDED.display_name
        """, (user_id, display_name, facts))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"⚠️ Memory save error: {e}")


def forget_user(user_id: int):
    """Wipe a user's stored memory."""
    if not MEMORY_ENABLED:
        return
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("DELETE FROM user_facts WHERE user_id = %s", (user_id,))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"⚠️ Memory delete error: {e}")


def update_memory_from_message(user_id: int, display_name: str, message_text: str):
    """Ask Claude to update what we know about a user based on their latest message.

    Runs AFTER the bot has already replied, so it never slows down the response.
    """
    if not MEMORY_ENABLED or not message_text.strip():
        return
    existing = get_user_facts(user_id)
    try:
        result = ai_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
            system=(
                "You maintain a concise memory file of durable facts about a Discord user. "
                "Given the EXISTING facts and the user's NEW message, return an UPDATED fact list. "
                "Only include concrete, lasting things they revealed about themselves (preferences, "
                "hobbies, life details, recurring topics, opinions they hold). "
                "Ignore one-off small talk. Merge duplicates. Keep it under 15 short bullet points. "
                "Return ONLY the bullet list, nothing else. If there's nothing worth saving and no "
                "existing facts, return the single word NONE."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"EXISTING FACTS about {display_name}:\n{existing or '(none yet)'}\n\n"
                    f"NEW MESSAGE from {display_name}:\n{message_text}\n\n"
                    f"Return the updated fact list:"
                ),
            }],
        )
        updated = result.content[0].text.strip()
        if updated and updated.upper() != "NONE":
            save_user_facts(user_id, display_name, updated)
    except Exception as e:
        print(f"⚠️ Memory update error: {e}")


# ─────────────────────────────────────────────
#  EVENT — Bot ready
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    await tree.sync()           # Register slash commands with Discord
    print(f"✅ {BOT_NAME} is online as {bot.user}")
    print(f"   Servers: {[g.name for g in bot.guilds]}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="/help for commands"
        )
    )
    daily_joke.start()          # 🎭 Start the joke scheduler
    print(f"   Daily joke scheduled at {JOKE_TIME[0]:02d}:{JOKE_TIME[1]:02d} UTC")
    init_db()                   # 🧠 Set up persistent memory
    if not MEMORY_ENABLED:
        print("   🧠 Persistent memory: OFF (no database connected)")


# ─────────────────────────────────────────────
#  EVENT — Auto-moderation on every message
# ─────────────────────────────────────────────
@bot.event
async def on_message(message: discord.Message):
    # Ignore messages from bots (including itself)
    if message.author.bot:
        return

    content_lower = message.content.lower()

    # 🚨 Bad word detection
    for word in BAD_WORDS:
        if word in content_lower:
            await message.delete()
            await message.channel.send(
                f"⚠️ {message.author.mention} — that message was removed for violating server rules.",
                delete_after=8,
            )
            # Auto-log a warning
            warnings_db[message.guild.id][message.author.id].append({
                "reason": f"Automatic: banned word detected",
                "time": datetime.utcnow().isoformat(),
            })
            count = len(warnings_db[message.guild.id][message.author.id])
            if count >= MAX_WARNINGS:
                await message.channel.send(
                    f"🔴 **{message.author.display_name}** has reached {count} warnings. "
                    f"Mods — please review.",
                    delete_after=15,
                )
            return

    # 💬 Reply when the bot is @mentioned
    if bot.user in message.mentions:
        # Strip the mention from the message
        prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()

        # 🖼️ Grab any image attachments so the bot can SEE them
        images = []
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                try:
                    raw = await attachment.read()
                    # Detect the REAL type from the bytes (Discord's label can be wrong)
                    media_type = detect_image_type(raw)
                    if not media_type:
                        print(f"Skipping unsupported image: {attachment.filename}")
                        continue
                    images.append({
                        "media_type": media_type,
                        "data": base64.standard_b64encode(raw).decode("utf-8"),
                    })
                except Exception as e:
                    print(f"Couldn't read attachment: {e}")

        # Nothing to respond to at all
        if not prompt and not images:
            await message.reply("Hey! Ask me anything 😊 (you can send me images too!)")
            return

        # 🏷️ Tag the message with WHO is speaking so the bot knows everyone
        speaker = message.author.display_name
        tagged_prompt = f"[{speaker}]: {prompt}" if prompt else f"[{speaker}] sent an image."

        # 🧠 Load what the bot remembers about this person
        facts = get_user_facts(message.author.id)
        system_prompt = BOT_PERSONALITY
        if facts:
            system_prompt += (
                f"\n\nHere's what you remember about {speaker} from past conversations:\n"
                f"{facts}\n"
                f"Weave this in naturally when relevant — don't recite it like a list."
            )

        # 🛠️ Give the bot tool access, scoped to this real Discord caller
        tool_ctx = None
        if message.guild is not None:
            tool_ctx = {
                "guild": message.guild,
                "channel": message.channel,
                "author": message.author,
                "is_mod": is_moderator(message.author),
            }

        async with message.channel.typing():
            reply = await ask_claude(
                tagged_prompt, system=system_prompt, channel_id=message.channel.id,
                images=images, tool_ctx=tool_ctx,
            )
        await message.reply(reply)

        # 🧠 After replying, quietly update the bot's memory of this person
        update_memory_from_message(message.author.id, speaker, prompt)
        return

    await bot.process_commands(message)


# ─────────────────────────────────────────────
#  😂  DAILY JOKE TASK — runs every day at JOKE_TIME
# ─────────────────────────────────────────────
async def generate_joke() -> str:
    """Ask Claude for a fresh joke."""
    return await ask_claude(
        "Tell me one original joke. No preamble, no explanation after — just the joke.",
        system=(
            f"You are a comedian who specialises in {JOKE_STYLE} "
            "Never repeat a joke you've told before. "
            "Respond with ONLY the joke text, nothing else."
        ),
    )

@tasks.loop(time=discord.utils.utcnow().replace(
    hour=JOKE_TIME[0], minute=JOKE_TIME[1], second=JOKE_TIME[2], microsecond=0
).timetz())
async def daily_joke():
    """Post the joke of the day to every configured channel."""
    for guild in bot.guilds:
        channel_id = joke_channels.get(guild.id)
        if not channel_id:
            continue
        channel = guild.get_channel(channel_id)
        if not channel:
            continue
        joke = await generate_joke()
        embed = discord.Embed(
            title="😈 Joke of the Day",
            description=joke,
            color=discord.Color.dark_red(),
        )
        embed.set_footer(text="Use /jokeme for an on-demand joke anytime!")
        await channel.send(embed=embed)

@daily_joke.before_loop
async def before_daily_joke():
    await bot.wait_until_ready()


# ─────────────────────────────────────────────
#  SLASH COMMAND — /setjokechannel  (mods only)
# ─────────────────────────────────────────────
@tree.command(name="setjokechannel", description="Set the channel where the daily joke gets posted (mods only)")
@app_commands.describe(channel="The channel to post jokes in")
async def slash_setjokechannel(interaction: discord.Interaction, channel: discord.TextChannel):
    mod_role = discord.utils.get(interaction.guild.roles, name=MOD_ROLE_NAME)
    if mod_role not in interaction.user.roles and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need the Moderator role to use this command.", ephemeral=True)
        return

    joke_channels[interaction.guild.id] = channel.id
    await interaction.response.send_message(
        f"✅ Daily jokes will now post in {channel.mention} every day at {JOKE_TIME[0]:02d}:{JOKE_TIME[1]:02d} UTC! 😈"
    )


# ─────────────────────────────────────────────
#  SLASH COMMAND — /jokeme  (on-demand joke)
# ─────────────────────────────────────────────
@tree.command(name="jokeme", description="Get a joke right now!")
async def slash_jokeme(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    joke = await generate_joke()
    embed = discord.Embed(
        title="😈 Here's one for you...",
        description=joke,
        color=discord.Color.dark_red(),
    )
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed)


# ─────────────────────────────────────────────
#  SLASH COMMAND — /ask
# ─────────────────────────────────────────────
@tree.command(name="ask", description="Ask the AI a question")
@app_commands.describe(question="What do you want to know?")
async def slash_ask(interaction: discord.Interaction, question: str):
    await interaction.response.defer(thinking=True)

    tool_ctx = None
    if interaction.guild is not None:
        tool_ctx = {
            "guild": interaction.guild,
            "channel": interaction.channel,
            "author": interaction.user,
            "is_mod": is_moderator(interaction.user),
        }

    answer = await ask_claude(question, tool_ctx=tool_ctx)
    embed = discord.Embed(
        title="💬 AI Answer",
        description=answer,
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Asked by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed)


# ─────────────────────────────────────────────
#  SLASH COMMAND — /summarize
# ─────────────────────────────────────────────
@tree.command(name="summarize", description=f"Summarize the last {SUMMARIZE_LIMIT} messages in this channel")
async def slash_summarize(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    messages = []
    async for msg in interaction.channel.history(limit=SUMMARIZE_LIMIT):
        if not msg.author.bot:
            messages.append(f"{msg.author.display_name}: {msg.content}")
    messages.reverse()  # Chronological order

    if not messages:
        await interaction.followup.send("No messages found to summarize.")
        return

    conversation_text = "\n".join(messages)
    prompt = (
        f"Summarize this Discord conversation in 3–5 bullet points. "
        f"Focus on main topics and decisions:\n\n{conversation_text}"
    )
    summary = await ask_claude(prompt, system="You are a helpful assistant that summarizes Discord conversations clearly and concisely.")

    embed = discord.Embed(
        title=f"📋 Summary of last {len(messages)} messages",
        description=summary,
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed)


# ─────────────────────────────────────────────
#  SLASH COMMAND — /warn  (mods only)
# ─────────────────────────────────────────────
@tree.command(name="warn", description="Warn a user (moderators only)")
@app_commands.describe(user="User to warn", reason="Reason for the warning")
async def slash_warn(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason given"):
    # Check if the caller has the mod role
    mod_role = discord.utils.get(interaction.guild.roles, name=MOD_ROLE_NAME)
    if mod_role not in interaction.user.roles and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need the Moderator role to use this command.", ephemeral=True)
        return

    warnings_db[interaction.guild.id][user.id].append({
        "reason": reason,
        "time": datetime.utcnow().isoformat(),
        "by": str(interaction.user),
    })
    count = len(warnings_db[interaction.guild.id][user.id])

    embed = discord.Embed(
        title="⚠️ Warning Issued",
        color=discord.Color.orange(),
    )
    embed.add_field(name="User", value=user.mention)
    embed.add_field(name="Reason", value=reason)
    embed.add_field(name="Total Warnings", value=f"{count}/{MAX_WARNINGS}")
    embed.set_footer(text=f"Issued by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

    # DM the warned user
    try:
        await user.send(
            f"⚠️ You received a warning in **{interaction.guild.name}**.\n"
            f"**Reason:** {reason}\n"
            f"You now have **{count}** warning(s)."
        )
    except discord.Forbidden:
        pass  # User has DMs disabled

    if count >= MAX_WARNINGS:
        await interaction.followup.send(
            f"🔴 **{user.display_name}** has hit {MAX_WARNINGS} warnings. Mods — consider taking action!"
        )


# ─────────────────────────────────────────────
#  SLASH COMMAND — /warnings
# ─────────────────────────────────────────────
@tree.command(name="warnings", description="Check a user's warnings")
@app_commands.describe(user="User to check")
async def slash_warnings(interaction: discord.Interaction, user: discord.Member):
    user_warnings = warnings_db[interaction.guild.id][user.id]

    if not user_warnings:
        await interaction.response.send_message(f"✅ {user.mention} has no warnings.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"⚠️ Warnings for {user.display_name}",
        color=discord.Color.orange(),
    )
    for i, w in enumerate(user_warnings, 1):
        embed.add_field(
            name=f"Warning {i}",
            value=f"**Reason:** {w['reason']}\n**Time:** {w['time'][:10]}",
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────
#  SLASH COMMAND — /clearwarnings  (mods only)
# ─────────────────────────────────────────────
@tree.command(name="clearwarnings", description="Clear all warnings for a user (moderators only)")
@app_commands.describe(user="User to clear warnings for")
async def slash_clearwarnings(interaction: discord.Interaction, user: discord.Member):
    mod_role = discord.utils.get(interaction.guild.roles, name=MOD_ROLE_NAME)
    if mod_role not in interaction.user.roles and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need the Moderator role to use this command.", ephemeral=True)
        return

    warnings_db[interaction.guild.id][user.id].clear()
    await interaction.response.send_message(f"✅ Cleared all warnings for {user.mention}.", ephemeral=True)


# ─────────────────────────────────────────────
#  SLASH COMMAND — /whatdoyouknow
# ─────────────────────────────────────────────
@tree.command(name="whatdoyouknow", description="See what the bot remembers about you")
async def slash_whatdoyouknow(interaction: discord.Interaction):
    if not MEMORY_ENABLED:
        await interaction.response.send_message(
            "🧠 My long-term memory isn't set up yet, so I don't remember anyone between restarts.",
            ephemeral=True,
        )
        return
    facts = get_user_facts(interaction.user.id)
    if not facts:
        await interaction.response.send_message(
            "🧠 I don't have anything saved about you yet — talk to me more!", ephemeral=True
        )
        return
    embed = discord.Embed(
        title=f"🧠 What I remember about {interaction.user.display_name}",
        description=facts,
        color=discord.Color.purple(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────
#  SLASH COMMAND — /forgetme
# ─────────────────────────────────────────────
@tree.command(name="forgetme", description="Make the bot permanently forget everything about you")
async def slash_forgetme(interaction: discord.Interaction):
    forget_user(interaction.user.id)
    await interaction.response.send_message(
        "🧠 Done — I've wiped everything I knew about you. Clean slate.", ephemeral=True
    )


# ─────────────────────────────────────────────
#  SLASH COMMAND — /clearmemory
# ─────────────────────────────────────────────
@tree.command(name="clearmemory", description="Clear the bot's memory of this channel's conversation")
async def slash_clearmemory(interaction: discord.Interaction):
    conversation_history[interaction.channel.id].clear()
    await interaction.response.send_message("🧠 Memory cleared — I've forgotten everything in this channel!", ephemeral=True)


# ─────────────────────────────────────────────
#  SLASH COMMAND — /help
# ─────────────────────────────────────────────
@tree.command(name="help", description="Show all available bot commands")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title=f"🤖 {BOT_NAME} — Commands",
        description="Here's everything I can do!",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="💬 Chat",
        value=(
            "`/ask [question]` — Ask me anything\n"
            "`@mention me` — Have a full conversation (I remember you, and can check "
            "warnings, recall what I know about others, or pull in recent chat on my own)\n"
            "`/whatdoyouknow` — See what I remember about you\n"
            "`/forgetme` — Make me forget you\n"
            "`/clearmemory` — Wipe my memory of this channel"
        ),
        inline=False,
    )
    embed.add_field(
        name="📋 Summarize",
        value=f"`/summarize` — Summarize the last {SUMMARIZE_LIMIT} messages here",
        inline=False,
    )
    embed.add_field(
        name="😈 Joke of the Day",
        value=(
            "`/setjokechannel #channel` — Set where daily jokes post (mods only)\n"
            "`/jokeme` — Get a joke on demand"
        ),
        inline=False,
    )
    embed.add_field(
        name="🛡️ Moderation (Mods only)",
        value=(
            "`/warn @user [reason]` — Issue a warning\n"
            "`/warnings @user` — View a user's warnings\n"
            "`/clearwarnings @user` — Clear warnings"
        ),
        inline=False,
    )
    embed.set_footer(text="Auto-mod is always active — bad words are removed automatically.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────
#  RUN THE BOT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
