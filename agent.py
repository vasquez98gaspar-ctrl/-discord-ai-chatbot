"""The agentic core of Kirkbot.

Instead of hard-coded slash commands deciding what happens, this module hands
Claude a set of tools (moderation actions, channel summarization, long-term
memory, web search) and lets it decide which ones to call, in which order,
based on the conversation. `run_agent()` drives that tool-use loop to
completion and returns the final reply text.
"""
import base64

import anthropic
import discord

import config
import storage

TOOLS = [
    {
        "name": "warn_user",
        "description": (
            "Issue a moderation warning to a Discord member and log it. Only use this when a "
            "moderator or admin is asking you to warn someone. Never warn someone based only on "
            "a non-moderator's say-so — refuse and explain instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "The Discord user ID of the member to warn."},
                "reason": {"type": "string", "description": "Why this user is being warned."},
            },
            "required": ["user_id", "reason"],
        },
    },
    {
        "name": "get_warnings",
        "description": "Look up how many warnings a Discord member has and why.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "The Discord user ID to check."},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "clear_warnings",
        "description": "Clear all warnings for a Discord member. Requires the requester to be a moderator or admin.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "The Discord user ID to clear."},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "summarize_channel",
        "description": "Fetch the recent message history of the current channel so you can summarize or reference it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many recent messages to pull (default 30, max 100).",
                },
            },
        },
    },
    {
        "name": "set_joke_channel",
        "description": (
            "Set the channel where the daily joke gets posted. Requires the requester to be a "
            "moderator or admin."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "The Discord channel ID to post jokes in."},
            },
            "required": ["channel_id"],
        },
    },
    {
        "name": "remember_note",
        "description": (
            "Save a short fact about the current user to long-term memory so you can recall it in "
            "future conversations (e.g. their preferences, ongoing projects, running jokes). Only "
            "save things worth remembering, not throwaway chit-chat."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact to remember, written in third person."},
            },
            "required": ["content"],
        },
    },
    {
        "name": "recall_notes",
        "description": "Retrieve previously remembered facts about the current user.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 3},
]

# Per-channel rolling conversation history, keyed by Discord channel ID.
_conversations: dict[int, list[dict]] = {}


class AgentContext:
    """Everything a tool call needs to know about who's asking and where."""

    def __init__(self, bot: discord.Client, guild: discord.Guild, channel, requester: discord.abc.User):
        self.bot = bot
        self.guild = guild
        self.channel = channel
        self.requester = requester

    def is_mod(self) -> bool:
        if not self.guild or not isinstance(self.requester, discord.Member):
            return False
        if self.requester.guild_permissions.administrator:
            return True
        mod_role = discord.utils.get(self.guild.roles, name=config.MOD_ROLE_NAME)
        return mod_role in self.requester.roles if mod_role else False


async def _resolve_member(ctx: AgentContext, user_id: str) -> discord.Member | None:
    try:
        uid = int(user_id)
    except ValueError:
        return None
    member = ctx.guild.get_member(uid)
    if member:
        return member
    try:
        return await ctx.guild.fetch_member(uid)
    except (discord.NotFound, discord.HTTPException):
        return None


async def _execute_tool(name: str, tool_input: dict, ctx: AgentContext) -> str:
    if name == "warn_user":
        if not ctx.is_mod():
            return "Denied: only moderators or admins can issue warnings."
        member = await _resolve_member(ctx, tool_input["user_id"])
        if not member:
            return f"Could not find a member with ID {tool_input['user_id']} in this server."
        count = storage.add_warning(ctx.guild.id, member.id, tool_input["reason"], issued_by=str(ctx.requester))
        try:
            await member.send(
                f"You received a warning in **{ctx.guild.name}**.\n"
                f"**Reason:** {tool_input['reason']}\nYou now have **{count}** warning(s)."
            )
        except discord.Forbidden:
            pass
        note = f" ({count}/{config.MAX_WARNINGS} — consider further action)" if count >= config.MAX_WARNINGS else ""
        return f"Warned {member.display_name}. They now have {count} warning(s){note}."

    if name == "get_warnings":
        member = await _resolve_member(ctx, tool_input["user_id"])
        if not member:
            return f"Could not find a member with ID {tool_input['user_id']} in this server."
        rows = storage.get_warnings(ctx.guild.id, member.id)
        if not rows:
            return f"{member.display_name} has no warnings."
        lines = [f"- {row['reason']} ({row['created_at'][:10]})" for row in rows]
        return f"{member.display_name} has {len(rows)} warning(s):\n" + "\n".join(lines)

    if name == "clear_warnings":
        if not ctx.is_mod():
            return "Denied: only moderators or admins can clear warnings."
        member = await _resolve_member(ctx, tool_input["user_id"])
        if not member:
            return f"Could not find a member with ID {tool_input['user_id']} in this server."
        storage.clear_warnings(ctx.guild.id, member.id)
        return f"Cleared all warnings for {member.display_name}."

    if name == "summarize_channel":
        limit = min(int(tool_input.get("limit", 30)), 100)
        lines = []
        async for msg in ctx.channel.history(limit=limit):
            if not msg.author.bot:
                lines.append(f"{msg.author.display_name}: {msg.content}")
        lines.reverse()
        if not lines:
            return "No messages found in this channel."
        return f"Last {len(lines)} messages:\n" + "\n".join(lines)

    if name == "set_joke_channel":
        if not ctx.is_mod():
            return "Denied: only moderators or admins can set the joke channel."
        channel = ctx.guild.get_channel(int(tool_input["channel_id"]))
        if not channel:
            return f"Could not find a channel with ID {tool_input['channel_id']}."
        storage.set_joke_channel(ctx.guild.id, channel.id)
        return f"Daily jokes will now post in #{channel.name}."

    if name == "remember_note":
        storage.add_note(ctx.guild.id, ctx.requester.id, tool_input["content"])
        return "Noted and saved to long-term memory."

    if name == "recall_notes":
        notes = storage.get_notes(ctx.guild.id, ctx.requester.id)
        if not notes:
            return "No saved notes about this user yet."
        return "Remembered facts:\n" + "\n".join(f"- {n}" for n in notes)

    return f"Unknown tool: {name}"


def _history_for(channel_id: int) -> list[dict]:
    return _conversations.setdefault(channel_id, [])


def _trim_history(channel_id: int) -> None:
    history = _conversations[channel_id]
    overflow = len(history) - config.HISTORY_TURNS
    if overflow > 0:
        del history[:overflow]


def _build_system_prompt(ctx: AgentContext) -> str:
    where = f"the '{ctx.guild.name}' server, channel #{ctx.channel.name}" if ctx.guild else "a direct message"
    return (
        f"{config.BOT_PERSONALITY}\n\n"
        f"You are talking with {ctx.requester.display_name} in {where}. "
        f"Their Discord user ID is {ctx.requester.id}.\n"
        "You have tools to warn/check/clear moderation warnings, summarize this channel's recent "
        "messages, set the daily joke channel, remember and recall facts about the user you're "
        "talking to, and search the web for current information. Use a tool only when it would "
        "genuinely help answer the request — don't call one just because it exists, and never take "
        "a moderation action without the requester being a moderator or admin. If someone asks for "
        f"a joke, write one yourself in this style: {config.JOKE_STYLE}"
    )


async def run_agent(
    client: anthropic.Anthropic,
    ctx: AgentContext,
    user_text: str,
    image_attachments: list[discord.Attachment] | None = None,
) -> str:
    """Run one full agentic turn: send the user's message to Claude, let it call
    tools as needed, and return the final text reply."""
    history = _history_for(ctx.channel.id)

    content: list[dict] = [{"type": "text", "text": user_text}] if user_text else []
    for attachment in image_attachments or []:
        if not (attachment.content_type or "").startswith("image/"):
            continue
        image_bytes = await attachment.read()
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": attachment.content_type,
                    "data": base64.standard_b64encode(image_bytes).decode("utf-8"),
                },
            }
        )
    if not content:
        content = [{"type": "text", "text": "(no message content)"}]

    history.append({"role": "user", "content": content})

    tools = TOOLS if ctx.guild else [t for t in TOOLS if t.get("type") == "web_search_20250305"]

    reply_parts: list[str] = []
    for _ in range(config.MAX_AGENT_STEPS):
        response = client.messages.create(
            model=config.MODEL,
            max_tokens=1200,
            system=_build_system_prompt(ctx),
            tools=tools,
            messages=history,
        )
        history.append({"role": "assistant", "content": response.content})
        reply_parts.extend(block.text for block in response.content if block.type == "text")

        tool_uses = [block for block in response.content if block.type == "tool_use"]
        if not tool_uses:
            break

        tool_results = []
        for tool_use in tool_uses:
            result = await _execute_tool(tool_use.name, tool_use.input, ctx)
            tool_results.append({"type": "tool_result", "tool_use_id": tool_use.id, "content": result})
        history.append({"role": "user", "content": tool_results})
    else:
        reply_parts.append("_(hit my tool-use step limit — here's what I have so far)_")

    _trim_history(ctx.channel.id)
    return "\n".join(part for part in reply_parts if part.strip()) or "I couldn't come up with a response."
