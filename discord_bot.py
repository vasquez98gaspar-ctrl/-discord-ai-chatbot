"""
╔══════════════════════════════════════════════╗
║       Kirkbot — Agentic Discord Bot           ║
║              Powered by Claude                ║
╚══════════════════════════════════════════════╝

SETUP (do this once):
  1. pip install -r requirements.txt
  2. Set DISCORD_TOKEN and ANTHROPIC_API_KEY (env vars, or edit config.py directly)
  3. Run: python discord_bot.py

COMMANDS:
  /ask [question] [image]  — Ask the AI anything (optionally attach an image)
  /summarize               — Summarize the last 30 messages
  /warn @user [reason]     — Warn a user (mods only)
  /warnings @user          — Check a user's warnings
  /clearwarnings @user     — Clear a user's warnings (mods only)
  /setjokechannel          — Set the channel for daily jokes (mods only)
  /jokeme                  — Get a joke right now!
  /help                    — Show all commands

  @mention the bot         — Chat with it directly. It can decide on its own to warn
                              users, look up warnings, summarize the channel, remember
                              facts about you, or search the web, depending on what
                              you ask — no separate command needed.
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import anthropic

import agent
import config
import storage
from agent import AgentContext

# ─────────────────────────────────────────────
#  BOT SETUP
# ─────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

ai_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

storage.init_db()


# ─────────────────────────────────────────────
#  HELPER — one-off (non-agentic) generation, used for jokes
# ─────────────────────────────────────────────
def ask_claude(prompt: str, system: str = config.BOT_PERSONALITY) -> str:
    """Send a single message to Claude and return the response text (no tools, no memory)."""
    try:
        response = ai_client.messages.create(
            model=config.MODEL,
            max_tokens=1000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        return f"⚠️ AI error: {e}"


def generate_joke() -> str:
    """Ask Claude for a fresh dark joke."""
    return ask_claude(
        "Tell me one original joke. No preamble, no explanation after — just the joke.",
        system=(
            f"You are a comedian who specialises in {config.JOKE_STYLE} "
            "Never repeat a joke you've told before. "
            "Respond with ONLY the joke text, nothing else."
        ),
    )


# ─────────────────────────────────────────────
#  EVENT — Bot ready
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ {config.BOT_NAME} is online as {bot.user}")
    print(f"   Servers: {[g.name for g in bot.guilds]}")
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.listening, name="/help for commands")
    )
    daily_joke.start()
    print(f"   Daily joke scheduled at {config.JOKE_TIME[0]:02d}:{config.JOKE_TIME[1]:02d} UTC")


# ─────────────────────────────────────────────
#  EVENT — Auto-moderation + agentic chat on every message
# ─────────────────────────────────────────────
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    content_lower = message.content.lower()

    # 🚨 Bad word detection (guild channels only — nothing to moderate in a DM)
    for word in config.BAD_WORDS if message.guild else []:
        if word in content_lower:
            await message.delete()
            await message.channel.send(
                f"⚠️ {message.author.mention} — that message was removed for violating server rules.",
                delete_after=8,
            )
            count = storage.add_warning(
                message.guild.id, message.author.id, "Automatic: banned word detected", issued_by="AutoMod"
            )
            if count >= config.MAX_WARNINGS:
                await message.channel.send(
                    f"🔴 **{message.author.display_name}** has reached {count} warnings. "
                    f"Mods — please review.",
                    delete_after=15,
                )
            return

    # 💬 Agentic reply when the bot is @mentioned (or DM'd)
    if bot.user in message.mentions or message.guild is None:
        prompt = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
        if not prompt and not message.attachments:
            await message.reply("Hey! Ask me anything 😊")
            return
        ctx = AgentContext(bot=bot, guild=message.guild, channel=message.channel, requester=message.author)
        async with message.channel.typing():
            reply = await agent.run_agent(ai_client, ctx, prompt, image_attachments=message.attachments)
        await message.reply(reply[:2000])
        return

    await bot.process_commands(message)


# ─────────────────────────────────────────────
#  😂  DAILY JOKE TASK — runs every day at JOKE_TIME
# ─────────────────────────────────────────────
@tasks.loop(
    time=discord.utils.utcnow()
    .replace(hour=config.JOKE_TIME[0], minute=config.JOKE_TIME[1], second=config.JOKE_TIME[2], microsecond=0)
    .timetz()
)
async def daily_joke():
    """Post the joke of the day to every configured channel."""
    for guild_id, channel_id in storage.get_all_joke_channels().items():
        guild = bot.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild else None
        if not channel:
            continue
        joke = generate_joke()
        embed = discord.Embed(title="😈 Joke of the Day", description=joke, color=discord.Color.dark_red())
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
    mod_role = discord.utils.get(interaction.guild.roles, name=config.MOD_ROLE_NAME)
    if mod_role not in interaction.user.roles and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need the Moderator role to use this command.", ephemeral=True)
        return

    storage.set_joke_channel(interaction.guild.id, channel.id)
    await interaction.response.send_message(
        f"✅ Daily jokes will now post in {channel.mention} every day at "
        f"{config.JOKE_TIME[0]:02d}:{config.JOKE_TIME[1]:02d} UTC! 😈"
    )


# ─────────────────────────────────────────────
#  SLASH COMMAND — /jokeme  (on-demand joke)
# ─────────────────────────────────────────────
@tree.command(name="jokeme", description="Get a dark joke right now!")
async def slash_jokeme(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    joke = generate_joke()
    embed = discord.Embed(title="😈 Here's one for you...", description=joke, color=discord.Color.dark_red())
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed)


# ─────────────────────────────────────────────
#  SLASH COMMAND — /ask
# ─────────────────────────────────────────────
@tree.command(name="ask", description="Ask the AI a question — it can use tools, memory, and web search")
@app_commands.describe(question="What do you want to know?", image="Optional image for the AI to look at")
async def slash_ask(interaction: discord.Interaction, question: str, image: discord.Attachment | None = None):
    await interaction.response.defer(thinking=True)
    ctx = AgentContext(bot=bot, guild=interaction.guild, channel=interaction.channel, requester=interaction.user)
    answer = await agent.run_agent(ai_client, ctx, question, image_attachments=[image] if image else None)
    embed = discord.Embed(title="💬 AI Answer", description=answer[:4000], color=discord.Color.blurple())
    embed.set_footer(text=f"Asked by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed)


# ─────────────────────────────────────────────
#  SLASH COMMAND — /summarize
# ─────────────────────────────────────────────
@tree.command(name="summarize", description=f"Summarize the last {config.SUMMARIZE_LIMIT} messages in this channel")
async def slash_summarize(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    messages = []
    async for msg in interaction.channel.history(limit=config.SUMMARIZE_LIMIT):
        if not msg.author.bot:
            messages.append(f"{msg.author.display_name}: {msg.content}")
    messages.reverse()

    if not messages:
        await interaction.followup.send("No messages found to summarize.")
        return

    conversation_text = "\n".join(messages)
    prompt = (
        f"Summarize this Discord conversation in 3–5 bullet points. "
        f"Focus on main topics and decisions:\n\n{conversation_text}"
    )
    summary = ask_claude(
        prompt, system="You are a helpful assistant that summarizes Discord conversations clearly and concisely."
    )

    embed = discord.Embed(
        title=f"📋 Summary of last {len(messages)} messages", description=summary, color=discord.Color.green()
    )
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed)


# ─────────────────────────────────────────────
#  SLASH COMMAND — /warn  (mods only)
# ─────────────────────────────────────────────
@tree.command(name="warn", description="Warn a user (moderators only)")
@app_commands.describe(user="User to warn", reason="Reason for the warning")
async def slash_warn(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason given"):
    mod_role = discord.utils.get(interaction.guild.roles, name=config.MOD_ROLE_NAME)
    if mod_role not in interaction.user.roles and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need the Moderator role to use this command.", ephemeral=True)
        return

    count = storage.add_warning(interaction.guild.id, user.id, reason, issued_by=str(interaction.user))

    embed = discord.Embed(title="⚠️ Warning Issued", color=discord.Color.orange())
    embed.add_field(name="User", value=user.mention)
    embed.add_field(name="Reason", value=reason)
    embed.add_field(name="Total Warnings", value=f"{count}/{config.MAX_WARNINGS}")
    embed.set_footer(text=f"Issued by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

    try:
        await user.send(
            f"⚠️ You received a warning in **{interaction.guild.name}**.\n"
            f"**Reason:** {reason}\n"
            f"You now have **{count}** warning(s)."
        )
    except discord.Forbidden:
        pass

    if count >= config.MAX_WARNINGS:
        await interaction.followup.send(
            f"🔴 **{user.display_name}** has hit {config.MAX_WARNINGS} warnings. Mods — consider taking action!"
        )


# ─────────────────────────────────────────────
#  SLASH COMMAND — /warnings
# ─────────────────────────────────────────────
@tree.command(name="warnings", description="Check a user's warnings")
@app_commands.describe(user="User to check")
async def slash_warnings(interaction: discord.Interaction, user: discord.Member):
    user_warnings = storage.get_warnings(interaction.guild.id, user.id)

    if not user_warnings:
        await interaction.response.send_message(f"✅ {user.mention} has no warnings.", ephemeral=True)
        return

    embed = discord.Embed(title=f"⚠️ Warnings for {user.display_name}", color=discord.Color.orange())
    for i, w in enumerate(user_warnings, 1):
        embed.add_field(
            name=f"Warning {i}",
            value=f"**Reason:** {w['reason']}\n**Time:** {w['created_at'][:10]}",
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────
#  SLASH COMMAND — /clearwarnings  (mods only)
# ─────────────────────────────────────────────
@tree.command(name="clearwarnings", description="Clear all warnings for a user (moderators only)")
@app_commands.describe(user="User to clear warnings for")
async def slash_clearwarnings(interaction: discord.Interaction, user: discord.Member):
    mod_role = discord.utils.get(interaction.guild.roles, name=config.MOD_ROLE_NAME)
    if mod_role not in interaction.user.roles and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need the Moderator role to use this command.", ephemeral=True)
        return

    storage.clear_warnings(interaction.guild.id, user.id)
    await interaction.response.send_message(f"✅ Cleared all warnings for {user.mention}.", ephemeral=True)


# ─────────────────────────────────────────────
#  SLASH COMMAND — /help
# ─────────────────────────────────────────────
@tree.command(name="help", description="Show all available bot commands")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title=f"🤖 {config.BOT_NAME} — Commands",
        description="Here's everything I can do!",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="💬 Chat (agentic)",
        value=(
            "`/ask [question] [image]` — Ask me anything, optionally with an image\n"
            "`@mention me` — Chat with me. I can decide on my own to warn users, look up "
            "warnings, summarize this channel, remember things about you, or search the web."
        ),
        inline=False,
    )
    embed.add_field(
        name="📋 Summarize",
        value=f"`/summarize` — Summarize the last {config.SUMMARIZE_LIMIT} messages here",
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
    bot.run(config.DISCORD_TOKEN)
