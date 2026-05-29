"""
╔══════════════════════════════════════════════╗
║         AI Discord Bot — Powered by Claude   ║
╚══════════════════════════════════════════════╝

SETUP (do this once):
  1. pip install discord.py anthropic
  2. Fill in your tokens below (DISCORD_TOKEN and ANTHROPIC_API_KEY)
  3. Run: python discord_bot.py

COMMANDS:
  /ask [question]     — Ask the AI anything
  /summarize          — Summarize the last 30 messages
  /warn @user [reason]— Warn a user (mods only)
  /warnings @user     — Check a user's warnings
  /clearwarnings @user— Clear a user's warnings (mods only)
  /setjokechannel     — Set the channel for daily jokes (mods only)
  /jokeme             — Get a joke right now!
  /help               — Show all commands

  @mention the bot    — Chat with it directly!
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import anthropic
import os
from collections import defaultdict
from datetime import datetime

# ─────────────────────────────────────────────
#  🔑  PUT YOUR KEYS HERE
# ─────────────────────────────────────────────
DISCORD_TOKEN    = "enter here" 


ANTHROPIC_API_KEY = "enter api key here"

# ─────────────────────────────────────────────
#  ⚙️  SETTINGS  (customize these!)
# ─────────────────────────────────────────────
BOT_NAME         = "ServerBot"          # What the bot calls itself
BOT_PERSONALITY  = (
    "You are a helpful, friendly Discord bot assistant. "
    "Keep answers concise and conversational. "
    "Use Discord markdown when helpful (bold, code blocks, etc.). "
    "Never be rude or produce harmful content."
)
MOD_ROLE_NAME    = "Moderator"          # Role name that can use mod commands
MAX_WARNINGS     = 3                    # Warnings before auto-kick suggestion
SUMMARIZE_LIMIT  = 30                   # Messages to pull for /summarize

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


# ─────────────────────────────────────────────
#  HELPER — call Claude
# ─────────────────────────────────────────────
def ask_claude(prompt: str, system: str = BOT_PERSONALITY) -> str:
    """Send a message to Claude and return the response text."""
    try:
        response = ai_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        return f"⚠️ AI error: {e}"


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
        if not prompt:
            await message.reply("Hey! Ask me anything 😊")
            return
        async with message.channel.typing():
            reply = ask_claude(prompt)
        await message.reply(reply)
        return

    await bot.process_commands(message)


# ─────────────────────────────────────────────
#  😂  DAILY JOKE TASK — runs every day at JOKE_TIME
# ─────────────────────────────────────────────
def generate_joke() -> str:
    """Ask Claude for a fresh dark joke."""
    return ask_claude(
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
        joke = generate_joke()
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
@tree.command(name="jokeme", description="Get a dark joke right now!")
async def slash_jokeme(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    joke = generate_joke()
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
    answer = ask_claude(question)
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
    summary = ask_claude(prompt, system="You are a helpful assistant that summarizes Discord conversations clearly and concisely.")

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
            "`@mention me` — Have a conversation"
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
            "`/jokeme` — Get a dark joke on demand"
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
