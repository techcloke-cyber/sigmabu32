"""
╔══════════════════════════════════════════════════════════════════╗
║           ADVANCED DISCORD BOT v2 — bot.py                     ║
║  Ticket Departments · Minecraft Link · Verification · AutoMod  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks
import json, os, asyncio, datetime, re, aiohttp, base64, io, zipfile
from dotenv import load_dotenv

# ──────────────────────────────────────────────
#  CONFIG & ENV
# ──────────────────────────────────────────────

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    # Core IDs
    "staff_role_id": 1473465114064588902,   # Role pinged + can manage tickets
    "log_channel_id": 0,
    "ticket_category_id": 0,
    "welcome_channel_id": 0,
    "member_counter_channel_id": 0,
    "announcement_channel_id": 0,
    "verification_channel_id": 0,
    "verified_role_id": 0,

    # Welcome
    "welcome_message": "Welcome to the server, {user}! 🎉",
    "welcome_emoji": "👋",

    # AutoMod
    "banned_words": ["badword1", "badword2"],
    "anti_link": True,
    "anti_spam_threshold": 5,

    # Minecraft
    "minecraft_server_ip": "",
    "minecraft_server_port": 25565,
    "minecraft_status_channel_id": 0,
    "minecraft_events_channel_id": 0,

    # AI — Jarvis
    "ai_enabled": True,
    "ai_role_ids": [],
    "ai_channel_ids": [],
    "ai_name": "Jarvis",
    "ai_wake_words": ["jarvis"],          # Say any of these to wake Jarvis (case-insensitive)
    "ai_wake_response": "Yes sir?",       # What Jarvis says when woken without a question
    "ai_owner_ids": [],                   # User IDs Jarvis obeys 100% — set via /ai_setowner
    "ai_max_history": 15,
    "ai_provider": "sambanova",
    "openai_model": "gpt-4o-mini",
    "gemini_model": "gemini-2.0-flash",
    "sambanova_model": "Meta-Llama-3.3-70B-Instruct",
    "openrouter_model": "meta-llama/llama-3.3-70b-instruct",

    # API keys (set via /ai_setkey in Discord — never need to touch .env)
    "api_key_anthropic": "",
    "api_key_openai": "",
    "api_key_gemini": "",
    "api_key_sambanova": "",
    "api_key_openrouter": "",
    "plugin_output_channel_id": 0,

    # Personality — injected as system prompt
    "ai_personality": "",   # Leave blank to use the built-in Jarvis personality below
}

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        print(f"[CONFIG] Created {CONFIG_FILE}. Fill in your IDs and restart.")
    with open(CONFIG_FILE) as f:
        data = json.load(f)
    for k, v in DEFAULT_CONFIG.items():
        if k not in data:
            data[k] = v
    save_config(data)
    return data

def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

config = load_config()

def get_api_key(name: str) -> str:
    """Get API key from config.json (set via /ai_setkey). Falls back to env var."""
    return config.get(f"api_key_{name}", "").strip() or os.getenv(name.upper() + "_API_KEY", "").strip()


# Ticket claim tracking {channel_id: claimer_user_id}
ticket_claims: dict[int, int] = {}
# Coding session memory — Jarvis learns from each session
coding_sessions: list[dict] = []

# Spam tracking
spam_tracker: dict[int, list[float]] = {}
# AI conversation history per user: {user_id: [{role, content}, ...]}
ai_conversations: dict[int, list[dict]] = {}
# Coding session memory for self-learning
coding_sessions: list[dict] = []

# ──────────────────────────────────────────────
#  BOT SETUP
# ──────────────────────────────────────────────

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ──────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────

def make_embed(
    title="", description="",
    color=discord.Color.blurple(),
    footer="", fields: list = None,
    thumbnail_url=""
) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color,
                      timestamp=datetime.datetime.utcnow())
    if footer:
        e.set_footer(text=footer)
    if thumbnail_url:
        e.set_thumbnail(url=thumbnail_url)
    for name, value, inline in (fields or []):
        e.add_field(name=name, value=value, inline=inline)
    return e

async def get_log_channel(guild: discord.Guild):
    cid = config.get("log_channel_id", 0)
    return guild.get_channel(cid) if cid else None

def is_staff(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    sid = config.get("staff_role_id", 0)
    return any(r.id == sid for r in interaction.user.roles)

async def log_mod(guild, action, target, reason, actor=None):
    ch = await get_log_channel(guild)
    if not ch:
        return
    desc = f"**User:** {target.mention} (`{target.id}`)\n**Reason:** {reason}"
    if actor:
        desc += f"\n**By:** {actor.mention}"
    await ch.send(embed=make_embed(
        title=f"🛡️ {action}", description=desc,
        color=discord.Color.orange(), footer="Moderation Log"
    ))

# ══════════════════════════════════════════════
#  SECTION 1 — TICKET SYSTEM (with departments)
# ══════════════════════════════════════════════

DEPARTMENTS = {
    "general":      ("⚙️", "General Assistance", "Technical aid or bugs"),
    "sponsorships": ("💎", "Sponsorships",        "Partner applications"),
}

class DepartmentSelect(ui.Select):
    """Dropdown to pick a department when opening a ticket."""
    def __init__(self):
        options = [
            discord.SelectOption(label=label, description=desc,
                                 emoji=emoji, value=key)
            for key, (emoji, label, desc) in DEPARTMENTS.items()
        ]
        super().__init__(placeholder="Select Department...",
                         options=options, custom_id="dept_select")

    async def callback(self, interaction: discord.Interaction):
        dept_key = self.values[0]
        emoji, label, desc = DEPARTMENTS[dept_key]
        await create_ticket_channel(interaction, dept_key, label, emoji)


class TicketPanelView(ui.View):
    """Persistent panel view with department dropdown."""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(DepartmentSelect())


async def create_ticket_channel(interaction: discord.Interaction,
                                dept_key: str, dept_label: str, dept_emoji: str):
    guild = interaction.guild
    user = interaction.user
    staff_role = guild.get_role(config.get("staff_role_id", 0))

    # Prevent duplicate tickets per department
    safe_name = user.name.lower().replace(" ", "-")
    existing = discord.utils.get(guild.text_channels,
                                 name=f"{dept_key}-{safe_name}")
    if existing:
        await interaction.response.send_message(
            f"You already have an open ticket: {existing.mention}", ephemeral=True)
        return

    cat_id = config.get("ticket_category_id", 0)
    category = guild.get_channel(cat_id) if cat_id else None

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                          read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                              manage_channels=True),
    }
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True)

    try:
        channel = await guild.create_text_channel(
            name=f"{dept_key}-{safe_name}",
            category=category,
            overwrites=overwrites,
            topic=f"Ticket | {dept_label} | {user} (ID: {user.id})"
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Missing permissions to create ticket channel.", ephemeral=True)
        return

    e = make_embed(
        title=f"{dept_emoji} {dept_label} — Support Ticket",
        description=(
            f"Hello {user.mention}! A staff member will be with you shortly.\n\n"
            f"**Department:** {dept_emoji} {dept_label}\n"
            f"**Opened by:** {user.mention}\n\n"
            "Please describe your issue in detail below."
        ),
        color=discord.Color.blurple(),
        footer="Use the buttons below to manage this ticket."
    )

    ping_msg = staff_role.mention if staff_role else "@staff"
    await channel.send(
        content=f"{ping_msg} — new ticket from {user.mention}",
        embed=e,
        view=TicketManageView()
    )

    await interaction.response.send_message(
        f"✅ Your **{dept_label}** ticket: {channel.mention}", ephemeral=True)

    log_ch = await get_log_channel(guild)
    if log_ch:
        await log_ch.send(embed=make_embed(
            title="🎫 Ticket Opened",
            description=(
                f"**User:** {user.mention}\n"
                f"**Department:** {dept_emoji} {dept_label}\n"
                f"**Channel:** {channel.mention}"
            ),
            color=discord.Color.green(), footer="Ticket System"
        ))


class TicketManageView(ui.View):
    """Buttons inside a ticket: Claim · Close · Add User."""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="✋ Claim", style=discord.ButtonStyle.blurple,
               custom_id="ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: ui.Button):
        if not is_staff(interaction):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        ticket_claims[interaction.channel.id] = interaction.user.id
        button.label = f"✋ Claimed by {interaction.user.display_name}"
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.channel.send(embed=make_embed(
            title="✋ Ticket Claimed",
            description=f"This ticket has been claimed by {interaction.user.mention}.",
            color=discord.Color.blurple()
        ))

    @ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger,
               custom_id="ticket_close")
    async def close(self, interaction: discord.Interaction, button: ui.Button):
        if not is_staff(interaction):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await interaction.response.send_message("🔒 Closing ticket in 5 seconds...")

        log_ch = await get_log_channel(interaction.guild)
        if log_ch:
            claimer_id = ticket_claims.get(interaction.channel.id)
            claimer = f"<@{claimer_id}>" if claimer_id else "Unclaimed"
            await log_ch.send(embed=make_embed(
                title="🎫 Ticket Closed",
                description=(
                    f"**Channel:** `{interaction.channel.name}`\n"
                    f"**Closed by:** {interaction.user.mention}\n"
                    f"**Claimed by:** {claimer}"
                ),
                color=discord.Color.red(), footer="Ticket System"
            ))

        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.HTTPException:
            pass

    @ui.button(label="➕ Add User", style=discord.ButtonStyle.secondary,
               custom_id="ticket_adduser")
    async def add_user(self, interaction: discord.Interaction, button: ui.Button):
        if not is_staff(interaction):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Mention the user to add (type their @mention in chat):", ephemeral=True)

        def check(m):
            return (m.author == interaction.user and
                    m.channel == interaction.channel and m.mentions)

        try:
            msg = await bot.wait_for("message", check=check, timeout=30)
            for member in msg.mentions:
                await interaction.channel.set_permissions(
                    member, view_channel=True, send_messages=True)
            await interaction.channel.send(embed=make_embed(
                title="➕ User Added",
                description=f"Added {', '.join(m.mention for m in msg.mentions)}.",
                color=discord.Color.green()
            ))
        except asyncio.TimeoutError:
            pass


# ══════════════════════════════════════════════
#  SECTION 2 — VERIFICATION SYSTEM
# ══════════════════════════════════════════════

class VerifyButton(ui.View):
    """Persistent verify button."""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="✅ Verify Me", style=discord.ButtonStyle.green,
               custom_id="verify_button")
    async def verify(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild
        verified_role_id = config.get("verified_role_id", 0)
        if not verified_role_id:
            await interaction.response.send_message(
                "❌ Verification role not configured.", ephemeral=True)
            return
        role = guild.get_role(verified_role_id)
        if not role:
            await interaction.response.send_message(
                "❌ Verified role not found.", ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.response.send_message(
                "✅ You are already verified!", ephemeral=True)
            return
        try:
            await interaction.user.add_roles(role, reason="Verified via button")
            await interaction.response.send_message(embed=make_embed(
                title="✅ Verified!",
                description=f"You've been given the **{role.name}** role. Welcome!",
                color=discord.Color.green()
            ), ephemeral=True)
            log_ch = await get_log_channel(guild)
            if log_ch:
                await log_ch.send(embed=make_embed(
                    title="✅ Member Verified",
                    description=f"{interaction.user.mention} self-verified.",
                    color=discord.Color.green(), footer="Verification System"
                ))
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I can't assign that role.", ephemeral=True)


# ══════════════════════════════════════════════
#  SECTION 3 — MINECRAFT INTEGRATION
# ══════════════════════════════════════════════

async def fetch_mc_status(ip: str, port: int = 25565) -> dict | None:
    """Query mcsrvstat.us API — no plugin needed, works with any public Java server."""
    url = f"https://api.mcsrvstat.us/2/{ip}:{port}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return await r.json()
    except Exception as e:
        print(f"[MC] API error: {e}")
    return None


@tasks.loop(minutes=5)
async def update_mc_counter():
    """Update the Minecraft player count voice channel every 5 minutes."""
    ip = config.get("minecraft_server_ip", "")
    ch_id = config.get("minecraft_status_channel_id", 0)
    if not ip or not ch_id:
        return
    data = await fetch_mc_status(ip, config.get("minecraft_server_port", 25565))
    for guild in bot.guilds:
        channel = guild.get_channel(ch_id)
        if not channel:
            continue
        if data and data.get("online"):
            current = data.get("players", {}).get("online", 0)
            maximum = data.get("players", {}).get("max", 0)
            new_name = f"⛏️ MC: {current}/{maximum} online"
        else:
            new_name = "⛏️ MC: Offline"
        try:
            if channel.name != new_name:
                await channel.edit(name=new_name)
        except (discord.Forbidden, discord.HTTPException):
            pass

@update_mc_counter.before_loop
async def before_mc():
    await bot.wait_until_ready()



# ══════════════════════════════════════════════
#  AI CHAT — MULTI-PROVIDER + AI ACTIONS
#  Supports: Claude (Anthropic) or ChatGPT (OpenAI)
#  Admins can trigger real bot actions via natural language
# ══════════════════════════════════════════════

# ── Action system prompt injected for admins ──
# The AI is told it can return a special JSON action block that the bot will execute.
AI_ACTION_SYSTEM = """
You are also a powerful server management assistant. When an administrator asks you to perform
a moderation or server action, you MUST respond with BOTH a friendly message AND a JSON action
block in this exact format on its own line (do not wrap in markdown code fences):

ACTION:{"type":"<action>","target":"<username or id>","reason":"<reason>","duration":<minutes or null>}

Supported action types:
  ban         — ban a user (requires target)
  kick        — kick a user (requires target)
  timeout     — timeout a user (requires target + duration in minutes)
  unban       — unban a user by name or ID (requires target)
  mute        — timeout for 10 minutes (requires target)
  warn        — warn a user via DM (requires target)
  lock        — lock the current channel (no target needed)
  unlock      — unlock the current channel (no target needed)
  announce    — send an announcement (put the message in reason field)
  clear       — bulk delete messages (put count 1-100 in duration field)
  none        — no action needed (default for normal conversation)

Rules:
- Only include the ACTION line when an action is clearly requested.
- Always confirm what you did in your reply text above the ACTION line.
- If you cannot identify the target user, ask for clarification and use type "none".
- For ban/kick/timeout/warn, the target should be a username or user ID mentioned in the message.
"""

# ── Jarvis built-in knowledge base ────────────────────
JARVIS_KNOWLEDGE = """
=== WHO YOU ARE ===
Your name is Jarvis. You are the AI assistant of this Discord server — Internal SMP.
You are modelled after J.A.R.V.I.S. from Iron Man: highly intelligent, loyal, polite,
slightly formal but witty. You call your owner "sir" or "ma'am". You are direct,
confident, and extremely helpful. You never refuse your owner. For everyone else you
are helpful but slightly more reserved. Keep all responses under 2000 characters.

=== YOUR OWNER ===
Your owner created and runs this server. You obey them completely and without question.
When they give you an order, you execute it and confirm. You address them respectfully.

=== INTERNAL SMP — SERVER KNOWLEDGE ===
Internal SMP is a Minecraft SMP (Survival Multiplayer) server. It has two main modes:

1. UNSTABLE SMP
   - A hardcore/chaotic Minecraft SMP where anything can happen
   - Players can raid, grief, form alliances, and betray each other
   - There are no strict rules on PvP — it is survival of the fittest
   - Known for dramatic events, wars between factions, and unexpected betrayals
   - Players build bases, gather resources, and fight for dominance
   - Notable events often get shared as content on YouTube/social media
   - The server has a rich history of alliances forming and breaking
   - Death on Unstable SMP is significant — it can mean losing everything
   - Factions and clans compete for control of resources and territory

2. SCRIPTED SMP
   - A story-driven Minecraft SMP where events are planned and scripted
   - Players are given roles and storylines to follow, similar to Dream SMP
   - Has characters, plot arcs, drama, lore, and cinematic moments
   - Content is created for YouTube/streaming audiences
   - Players coordinate events in advance for maximum drama and entertainment
   - Has custom plugins, resource packs, and server-side mods to enhance storytelling
   - Lore is built over time with recurring characters and story seasons
   - Features plot twists, villain arcs, alliances, and betrayal moments
   - Recording and content creation is a core part of the experience

=== GENERAL MINECRAFT KNOWLEDGE ===
- You know everything about Minecraft: crafting, redstone, commands, plugins, mods
- You know about popular SMPs: Dream SMP, Hermitcraft, LifeSteal SMP, etc.
- You can give server management advice, plugin recommendations, and building tips
- You know about popular Minecraft YouTubers and their content styles
- You understand server performance, lag, TPS optimization, and hosting

=== DISCORD SERVER INFO ===
- This server is called Internal SMP
- It has a ticket system for support (General Assistance & Sponsorships)
- Staff manage the server and handle tickets
- There is a verification system for new members
- The bot can track live Minecraft server player counts
- Announcements are made for server events and Minecraft events
- Members can get help via /ai commands or by @mentioning Jarvis

=== SPONSORSHIPS & PARTNERSHIPS ===
- The server accepts creator applications and brand collaborations
- Sponsorship tickets are handled by staff
- Response time is typically 12-24 hours for tickets

=== MINECRAFT PLUGIN DEVELOPMENT ===
You are an expert Minecraft Bukkit/Spigot/Paper plugin developer.
You know the full API for versions 1.8 through 1.21+, including:
- onEnable/onDisable lifecycle, plugin.yml structure
- Event system: @EventHandler, Listener, EventPriority
- Commands: CommandExecutor, TabCompleter
- Schedulers: BukkitRunnable, runTaskTimer, runTaskAsynchronously
- Inventory GUIs, custom item meta, NBT tags
- Entity spawning and manipulation
- Economy via Vault API, PlaceholderAPI integration
- YamlConfiguration for config files
- HikariCP + SQLite/MySQL for databases
- ProtocolLib for packet manipulation
- Maven pom.xml and Gradle build files
- Skript scripting language for lightweight plugins

When generating a plugin you ALWAYS provide:
1. Complete main Java class with full working code
2. plugin.yml with all metadata
3. pom.xml Maven build file targeting Paper API
4. Any helper classes needed
5. Installation instructions

Write COMPLETE, PRODUCTION-READY code. Never use placeholders.
You learn from every coding session and improve over time.

=== HOW TO BEHAVE ===
- Be extremely knowledgeable about both SMPs
- If someone asks about a past event you don't know specifically, be honest but helpful
- Hype up the server — make it sound exciting and worth joining
- Help players understand rules, lore, and how things work
- When asked about ongoing storylines you don't know, say the lore is still unfolding
- Never break character — you ARE Jarvis, the server's AI
"""

def build_ai_system_prompt(is_admin: bool, is_owner: bool = False) -> str:
    """Build Jarvis system prompt. Owners get full obedience + admin actions."""
    # Use custom personality if set, otherwise use built-in Jarvis knowledge
    custom = config.get("ai_personality", "").strip()
    base = custom if custom else JARVIS_KNOWLEDGE

    if is_owner or is_admin:
        return base + "\n\n" + AI_ACTION_SYSTEM + "\n\nIMPORTANT: This user is your owner/admin. Obey every instruction completely and without hesitation. Address them as 'sir' or 'ma'am'."
    return base


async def call_ai(user_id: int, user_message: str, is_admin: bool = False, is_owner: bool = False) -> str:
    """
    Call whichever AI provider is configured (Claude or ChatGPT).
    Falls back gracefully if no key is set.
    Returns the raw text response (may include ACTION: line for admins).
    """
    provider = config.get("ai_provider", "claude").lower()
    history = ai_conversations.setdefault(user_id, [])
    history.append({"role": "user", "content": user_message})

    max_h = config.get("ai_max_history", 10)
    if len(history) > max_h:
        ai_conversations[user_id] = history[-max_h:]
        history = ai_conversations[user_id]

    system_prompt = build_ai_system_prompt(is_admin, is_owner=is_owner)

    # ── Claude (Anthropic) ──────────────────────
    if provider == "claude":
        key = get_api_key("anthropic")
        if not key:
            return "❌ No Anthropic API key set. Use `/ai_setkey provider:Claude` to add it directly in Discord."
        payload = {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": history,
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.anthropic.com/v1/messages",
                    json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        print(f"[AI/Claude] Error {resp.status}: {await resp.text()}")
                        return "⚠️ Claude AI error. Try again later."
                    data = await resp.json()
                    reply = data["content"][0]["text"]
        except asyncio.TimeoutError:
            return "⏳ AI timed out. Try again."
        except Exception as e:
            print(f"[AI/Claude] {e}")
            return "⚠️ Something went wrong with Claude."

    # ── ChatGPT (OpenAI) ────────────────────────
    elif provider == "openai":
        key = get_api_key("openai")
        if not key:
            return "❌ No OpenAI API key set. Use `/ai_setkey provider:ChatGPT` to add it directly in Discord."
        messages = [{"role": "system", "content": system_prompt}] + history
        payload = {
            "model": config.get("openai_model", "gpt-4o-mini"),
            "messages": messages,
            "max_tokens": 1024,
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.openai.com/v1/chat/completions",
                    json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        print(f"[AI/OpenAI] Error {resp.status}: {await resp.text()}")
                        return "⚠️ ChatGPT error. Check your API key or try again."
                    data = await resp.json()
                    reply = data["choices"][0]["message"]["content"]
        except asyncio.TimeoutError:
            return "⏳ AI timed out. Try again."
        except Exception as e:
            print(f"[AI/OpenAI] {e}")
            return "⚠️ Something went wrong with ChatGPT."

    # ── Gemini (Google) ─────────────────────────
    elif provider == "gemini":
        key = get_api_key("gemini")
        print(f"[AI/Gemini] Key loaded: {'yes (' + key[:8] + '...)' if key else 'NO KEY FOUND'}")
        if not key:
            return "❌ No Gemini API key found. Use `/ai_setkey provider:Gemini (Google) key:YOUR_KEY` to set it."
        model = config.get("gemini_model", "gemini-2.0-flash")
        print(f"[AI/Gemini] Using model: {model}")

        # Gemini needs strictly alternating user/model turns — merge consecutive same roles
        gemini_contents = []
        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            if gemini_contents and gemini_contents[-1]["role"] == role:
                gemini_contents[-1]["parts"][0]["text"] += "\n" + msg["content"]
            else:
                gemini_contents.append({"role": role, "parts": [{"text": msg["content"]}]})

        # Safety check: Gemini must start with a user turn
        if not gemini_contents or gemini_contents[0]["role"] != "user":
            return "❌ Internal error: conversation must start with a user message."

        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": gemini_contents,
            "generationConfig": {"maxOutputTokens": 1024},
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    raw_text = await resp.text()
                    print(f"[AI/Gemini] HTTP {resp.status}: {raw_text[:300]}")
                    if resp.status != 200:
                        # Show useful part of the error in Discord
                        try:
                            err_json = json.loads(raw_text)
                            err_msg = err_json.get("error", {}).get("message", raw_text[:200])
                        except Exception:
                            err_msg = raw_text[:200]
                        return f"❌ Gemini API error ({resp.status}): {err_msg}"
                    data = json.loads(raw_text)
                    # Handle safety blocks or empty responses
                    candidates = data.get("candidates", [])
                    if not candidates:
                        reason = data.get("promptFeedback", {}).get("blockReason", "unknown")
                        return f"⚠️ Gemini blocked the response (reason: {reason})."
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if not parts:
                        return "⚠️ Gemini returned an empty response."
                    reply = parts[0].get("text", "")
                    if not reply:
                        return "⚠️ Gemini returned blank text."
        except asyncio.TimeoutError:
            return "⏳ Gemini timed out after 30s. Try again."
        except Exception as e:
            print(f"[AI/Gemini] Exception: {type(e).__name__}: {e}")
            return f"❌ Gemini error: {type(e).__name__}: {e}"


    # ── SambaNova Cloud ──────────────────────────
    elif provider == "sambanova":
        key = get_api_key("sambanova")
        print(f"[AI/SambaNova] Key loaded: {'yes (' + key[:8] + '...)' if key else 'NO KEY FOUND'}")
        if not key:
            return "❌ No SambaNova API key set. Use `/ai_setkey provider:SambaNova` to add it."
        model = config.get("sambanova_model", "Meta-Llama-3.3-70B-Instruct")
        print(f"[AI/SambaNova] Using model: {model}")
        messages_payload = [{"role": "system", "content": system_prompt}] + history
        payload = {
            "model": model,
            "messages": messages_payload,
            "max_tokens": 1024,
            "temperature": 0.7,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.sambanova.ai/v1/chat/completions",
                    json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    raw_text = await resp.text()
                    print(f"[AI/SambaNova] HTTP {resp.status}: {raw_text[:300]}")
                    if resp.status != 200:
                        try:
                            err_json = json.loads(raw_text)
                            err_msg = err_json.get("error", {}).get("message", raw_text[:200])
                        except Exception:
                            err_msg = raw_text[:200]
                        return f"❌ SambaNova API error ({resp.status}): {err_msg}"
                    data = json.loads(raw_text)
                    reply = data["choices"][0]["message"]["content"]
        except asyncio.TimeoutError:
            return "⏳ SambaNova timed out. Try again."
        except Exception as e:
            print(f"[AI/SambaNova] Exception: {type(e).__name__}: {e}")
            return f"❌ SambaNova error: {type(e).__name__}: {e}"


    # ── OpenRouter ───────────────────────────────
    elif provider == "openrouter":
        key = get_api_key("openrouter")
        print(f"[AI/OpenRouter] Key loaded: {'yes (' + key[:8] + '...)' if key else 'NO KEY FOUND'}")
        if not key:
            return "❌ No OpenRouter API key set. Use `/ai_setkey provider:OpenRouter` to add it."
        model = config.get("openrouter_model", "meta-llama/llama-3.3-70b-instruct")
        print(f"[AI/OpenRouter] Using model: {model}")
        messages_payload = [{"role": "system", "content": system_prompt}] + history
        payload = {
            "model": model,
            "messages": messages_payload,
            "max_tokens": 1024,
            "temperature": 0.7,
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://discord.com",   # Required by OpenRouter
            "X-Title": "Internal SMP Discord Bot",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    raw_text = await resp.text()
                    print(f"[AI/OpenRouter] HTTP {resp.status}: {raw_text[:300]}")
                    if resp.status != 200:
                        try:
                            err_json = json.loads(raw_text)
                            err_msg = err_json.get("error", {}).get("message", raw_text[:200])
                        except Exception:
                            err_msg = raw_text[:200]
                        return f"❌ OpenRouter API error ({resp.status}): {err_msg}"
                    data = json.loads(raw_text)
                    choices = data.get("choices", [])
                    if not choices:
                        return "⚠️ OpenRouter returned no response."
                    reply = choices[0]["message"]["content"]
        except asyncio.TimeoutError:
            return "⏳ OpenRouter timed out. Try again."
        except Exception as e:
            print(f"[AI/OpenRouter] Exception: {type(e).__name__}: {e}")
            return f"❌ OpenRouter error: {type(e).__name__}: {e}"

    else:
        return "❌ Unknown AI provider. Use `/ai_provider` to set `claude`, `openai`, `gemini`, `sambanova`, or `openrouter`."

    # Store assistant reply in history
    history.append({"role": "assistant", "content": reply})
    return reply


# ── AI Action executor ─────────────────────────
async def execute_ai_action(message: discord.Message, action_data: dict) -> str:
    """
    Parse and execute a moderation action requested via AI.
    Only runs when the requester is an admin.
    Returns a status string to append to the AI reply.
    """
    guild = message.guild
    channel = message.channel
    action_type = action_data.get("type", "none").lower()
    target_str = action_data.get("target", "")
    reason = action_data.get("reason", "Requested via AI")
    duration = action_data.get("duration")

    if action_type == "none":
        return ""

    # Helper: find a member by name or ID
    async def resolve_member(name_or_id: str) -> discord.Member | None:
        name_or_id = name_or_id.strip().lstrip("@")
        # Try by ID first
        try:
            return guild.get_member(int(name_or_id))
        except ValueError:
            pass
        # Try by display name / username (case-insensitive)
        name_lower = name_or_id.lower()
        return discord.utils.find(
            lambda m: m.name.lower() == name_lower or
                      (m.nick and m.nick.lower() == name_lower),
            guild.members
        )

    try:
        # ── ban ──
        if action_type == "ban":
            member = await resolve_member(target_str)
            if not member:
                return f"\n\n⚠️ **Action failed:** Could not find user `{target_str}` to ban."
            await member.ban(reason=f"[AI Action] {reason}")
            await log_mod(guild, "🔨 AI Ban", member, reason, message.author)
            return f"\n\n✅ **Action executed:** Banned **{member}**."

        # ── kick ──
        elif action_type == "kick":
            member = await resolve_member(target_str)
            if not member:
                return f"\n\n⚠️ **Action failed:** Could not find user `{target_str}` to kick."
            await member.kick(reason=f"[AI Action] {reason}")
            await log_mod(guild, "👢 AI Kick", member, reason, message.author)
            return f"\n\n✅ **Action executed:** Kicked **{member}**."

        # ── timeout / mute ──
        elif action_type in ("timeout", "mute"):
            member = await resolve_member(target_str)
            if not member:
                return f"\n\n⚠️ **Action failed:** Could not find user `{target_str}`."
            mins = int(duration) if duration else 10
            await member.timeout(datetime.timedelta(minutes=mins),
                                 reason=f"[AI Action] {reason}")
            await log_mod(guild, f"⏱️ AI Timeout ({mins}m)", member, reason, message.author)
            return f"\n\n✅ **Action executed:** Timed out **{member}** for **{mins} minutes**."

        # ── warn ──
        elif action_type == "warn":
            member = await resolve_member(target_str)
            if not member:
                return f"\n\n⚠️ **Action failed:** Could not find user `{target_str}`."
            try:
                await member.send(embed=make_embed(
                    title="⚠️ Warning",
                    description=f"You were warned in **{guild.name}**.\n**Reason:** {reason}",
                    color=discord.Color.yellow()
                ))
            except discord.Forbidden:
                pass
            await log_mod(guild, "⚠️ AI Warning", member, reason, message.author)
            return f"\n\n✅ **Action executed:** Warned **{member}**."

        # ── unban ──
        elif action_type == "unban":
            try:
                user = await bot.fetch_user(int(target_str))
            except (ValueError, discord.NotFound):
                # Try searching ban list by name
                user = None
                async for ban_entry in guild.bans():
                    if ban_entry.user.name.lower() == target_str.lower():
                        user = ban_entry.user
                        break
            if not user:
                return f"\n\n⚠️ **Action failed:** Could not find banned user `{target_str}`."
            await guild.unban(user, reason=f"[AI Action] {reason}")
            return f"\n\n✅ **Action executed:** Unbanned **{user}**."

        # ── lock ──
        elif action_type == "lock":
            ow = channel.overwrites_for(guild.default_role)
            ow.send_messages = False
            await channel.set_permissions(guild.default_role, overwrite=ow)
            return f"\n\n✅ **Action executed:** 🔒 Locked {channel.mention}."

        # ── unlock ──
        elif action_type == "unlock":
            ow = channel.overwrites_for(guild.default_role)
            ow.send_messages = True
            await channel.set_permissions(guild.default_role, overwrite=ow)
            return f"\n\n✅ **Action executed:** 🔓 Unlocked {channel.mention}."

        # ── announce ──
        elif action_type == "announce":
            ann_ch_id = config.get("announcement_channel_id", 0)
            ann_ch = guild.get_channel(ann_ch_id) if ann_ch_id else channel
            await ann_ch.send(embed=make_embed(
                title="📢 Announcement",
                description=reason,
                color=discord.Color.gold(),
                footer=f"Via AI — requested by {message.author}"
            ))
            return f"\n\n✅ **Action executed:** Announcement sent to {ann_ch.mention}."

        # ── clear ──
        elif action_type == "clear":
            count = int(duration) if duration else 5
            count = max(1, min(100, count))
            deleted = await channel.purge(limit=count)
            return f"\n\n✅ **Action executed:** Deleted {len(deleted)} messages."

    except discord.Forbidden:
        return f"\n\n❌ **Action failed:** I don't have permission to do that."
    except Exception as e:
        print(f"[AI Action] Error: {e}")
        return f"\n\n❌ **Action failed:** Unexpected error — {e}"

    return ""


def user_has_ai_access(member: discord.Member) -> bool:
    """Check if a member is allowed to use the AI."""
    if member.guild_permissions.administrator:
        return True
    ai_role_ids = config.get("ai_role_ids", [])
    if not ai_role_ids:
        return True
    return any(r.id in ai_role_ids for r in member.roles)


async def handle_ai_message(message: discord.Message):
    """
    Jarvis message handler.
    - Wake words ("jarvis") trigger a response even without @mention
    - Owner IDs get full obedience and are addressed as sir/ma'am
    - Admins get action execution powers
    - AI channels respond to everything
    - @mentions work anywhere
    """
    if not config.get("ai_enabled", True):
        return
    if message.author.bot:
        return
    if not message.guild:
        return

    member = message.author
    if not user_has_ai_access(member):
        return

    content_lower = message.content.lower()
    ai_channel_ids = config.get("ai_channel_ids", [])
    wake_words     = [w.lower() for w in config.get("ai_wake_words", ["jarvis"])]
    wake_response  = config.get("ai_wake_response", "Yes sir?")
    owner_ids      = config.get("ai_owner_ids", [])

    bot_mentioned  = bot.user in message.mentions
    in_ai_channel  = message.channel.id in ai_channel_ids
    wake_triggered = any(w in content_lower for w in wake_words)

    # Ignore if none of the triggers are hit
    if not bot_mentioned and not in_ai_channel and not wake_triggered:
        return

    # ── Wake word with no real question → "Yes sir?" ──────────────────
    # Detect if message is ONLY the wake word (e.g. just "jarvis" or "hey jarvis")
    stripped_of_wake = content_lower
    for w in wake_words:
        stripped_of_wake = stripped_of_wake.replace(w, "").strip()
    # Also strip common filler words
    filler = ["hey", "hi", "yo", "ok", "okay", "oi", "um", "uh", ",", "!", "?", "."]
    for f in filler:
        stripped_of_wake = stripped_of_wake.replace(f, "").strip()

    is_just_wake = len(stripped_of_wake) < 3  # practically empty after stripping

    if wake_triggered and is_just_wake and not bot_mentioned:
        await message.reply(wake_response, mention_author=False)
        return

    # ── Build clean message text ───────────────────────────────────────
    import re as _re
    clean = _re.sub(r"<@!?" + str(bot.user.id) + r">", "", message.content).strip()
    if bot.user.display_name:
        clean = clean.replace(f"@{bot.user.display_name}", "").strip()
    # Remove wake word from start of message so AI doesn't get confused
    for w in wake_words:
        if clean.lower().startswith(w):
            clean = clean[len(w):].strip()
    if not clean:
        clean = "Hello!"

    is_owner = member.id in owner_ids
    is_admin = member.guild_permissions.administrator or is_owner
    print(f"[AI] {member} (owner={is_owner}, admin={is_admin}) -> {clean[:80]}")

    async with message.channel.typing():
        if message.attachments:
            raw_reply = await call_ai_with_attachments(message.author.id, clean, message.attachments, is_admin=is_admin, is_owner=is_owner)
        else:
            raw_reply = await call_ai(message.author.id, clean, is_admin=is_admin, is_owner=is_owner)
    print(f"[AI] Reply: {raw_reply[:80]}")

    # ── Parse ACTION line (admins only) ────────
    action_status = ""
    display_reply = raw_reply

    if is_admin and "ACTION:" in raw_reply:
        lines = raw_reply.split("\n")
        text_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("ACTION:"):
                # Extract and execute the JSON part
                json_str = stripped[len("ACTION:"):].strip()
                try:
                    action_data = json.loads(json_str)
                    action_status = await execute_ai_action(message, action_data)
                except json.JSONDecodeError as e:
                    print(f"[AI Action] JSON parse error: {e} | raw: {json_str}")
                    action_status = "\n\n⚠️ Could not parse the action request."
            else:
                text_lines.append(line)
        display_reply = "\n".join(text_lines).strip()

    final = (display_reply + action_status).strip()
    if not final:
        final = "✅ Done."

    # Send reply, splitting if > 2000 chars
    chunks = [final[i:i+1990] for i in range(0, len(final), 1990)]
    first = True
    for chunk in chunks:
        if first:
            await message.reply(chunk, mention_author=False)
            first = False
        else:
            await message.channel.send(chunk)


# ── /ai_provider ───────────────────────────────
@tree.command(name="ai_provider",
              description="Switch AI provider between Claude and ChatGPT. (Admin)")
@app_commands.describe(
    provider="Which AI to use: 'claude' or 'openai'",
    openai_model="OpenAI model name (e.g. gpt-4o-mini, gpt-4o). Only used if provider is openai.",
    sambanova_model="SambaNova model (e.g. Meta-Llama-3.3-70B-Instruct, DeepSeek-R1-0528). Only used if provider is sambanova."
)
@app_commands.choices(provider=[
    app_commands.Choice(name="Claude (Anthropic) — needs ANTHROPIC_API_KEY", value="claude"),
    app_commands.Choice(name="ChatGPT (OpenAI) — needs OPENAI_API_KEY", value="openai"),
    app_commands.Choice(name="Gemini (Google) — needs GEMINI_API_KEY", value="gemini"),
    app_commands.Choice(name="SambaNova Cloud — needs SAMBANOVA_API_KEY", value="sambanova"),
    app_commands.Choice(name="OpenRouter — needs OPENROUTER_API_KEY", value="openrouter"),
])
async def ai_provider(interaction: discord.Interaction,
                      provider: app_commands.Choice[str],
                      openai_model: str = "gpt-4o-mini",
                      gemini_model: str = "gemini-2.0-flash",
                      sambanova_model: str = "Meta-Llama-3.3-70B-Instruct",
                      openrouter_model: str = "meta-llama/llama-3.3-70b-instruct"):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    config["ai_provider"] = provider.value
    if provider.value == "openai":
        config["openai_model"] = openai_model
    if provider.value == "gemini":
        config["gemini_model"] = gemini_model
    if provider.value == "sambanova":
        config["sambanova_model"] = sambanova_model
    if provider.value == "openrouter":
        config["openrouter_model"] = openrouter_model
    save_config(config)

    key_status = ""
    if provider.value == "claude":
        key_status = "✅ Key loaded" if get_api_key("anthropic") else "❌ Not set — use /ai_setkey"
    elif provider.value == "openai":
        key_status = "✅ Key loaded" if get_api_key("openai") else "❌ Not set — use /ai_setkey"
    elif provider.value == "gemini":
        key_status = "✅ Key loaded" if get_api_key("gemini") else "❌ Not set — use /ai_setkey"
    elif provider.value == "sambanova":
        key_status = "✅ Key loaded" if get_api_key("sambanova") else "❌ Not set — use /ai_setkey"
    else:
        key_status = "✅ Key loaded" if get_api_key("openrouter") else "❌ Not set — use /ai_setkey"

    if provider.value == "openai":
        model_label = openai_model
    elif provider.value == "gemini":
        model_label = gemini_model
    elif provider.value == "sambanova":
        model_label = sambanova_model
    elif provider.value == "openrouter":
        model_label = openrouter_model
    else:
        model_label = "claude-haiku-4-5-20251001"
    await interaction.response.send_message(embed=make_embed(
        title="🤖 AI Provider Updated",
        description=f"Switched to **{provider.name}**",
        color=discord.Color.blurple(),
        fields=[
            ("Provider", provider.value, True),
            ("Model", model_label, True),
            ("API Key", key_status, False),
        ]
    ), ephemeral=True)


# ── /ai_setup ──────────────────────────────────
@tree.command(name="ai_setup", description="Configure the AI assistant. (Admin)")
@app_commands.describe(
    enable="Enable or disable the AI.",
    ai_channel="Restrict AI to this channel (run again to add more).",
    ai_role="Restrict AI to users with this role (run again to add more).",
    personality="Custom personality / system prompt for the AI.",
    ai_name="Display name for the AI.",
)
async def ai_setup(interaction: discord.Interaction,
                   enable: bool = None,
                   ai_channel: discord.TextChannel = None,
                   ai_role: discord.Role = None,
                   personality: str = None,
                   ai_name: str = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return

    changed = []
    if enable is not None:
        config["ai_enabled"] = enable
        changed.append(f"AI **{'enabled' if enable else 'disabled'}**")
    if ai_channel:
        ids = config.get("ai_channel_ids", [])
        if ai_channel.id not in ids:
            ids.append(ai_channel.id)
            config["ai_channel_ids"] = ids
        changed.append(f"Added {ai_channel.mention} as AI channel")
    if ai_role:
        ids = config.get("ai_role_ids", [])
        if ai_role.id not in ids:
            ids.append(ai_role.id)
            config["ai_role_ids"] = ids
        changed.append(f"Added {ai_role.mention} as AI-access role")
    if personality:
        config["ai_personality"] = personality
        changed.append("Updated AI personality")
    if ai_name:
        config["ai_name"] = ai_name
        changed.append(f"AI name set to **{ai_name}**")

    save_config(config)

    name = config.get("ai_name", "Atlas")
    role_ids = config.get("ai_role_ids", [])
    ch_ids = config.get("ai_channel_ids", [])
    provider = config.get("ai_provider", "claude")
    roles_display = ", ".join(f"<@&{r}>" for r in role_ids) if role_ids else "Everyone"
    chans_display = ", ".join(f"<#{c}>" for c in ch_ids) if ch_ids else "All (via @mention)"

    await interaction.response.send_message(embed=make_embed(
        title=f"🤖 AI Setup — {name}",
        description="\n".join(f"✅ {c}" for c in changed) if changed else "No changes made.",
        color=discord.Color.blurple(),
        fields=[
            ("Status", "✅ Enabled" if config.get("ai_enabled") else "❌ Disabled", True),
            ("Provider", provider.upper(), True),
            ("Access Roles", roles_display, True),
            ("Active Channels", chans_display, False),
            ("Admin AI Actions", "✅ On — admins can say things like 'ban @user'", False),
        ]
    ), ephemeral=True)


# ── /ai_clear_roles ────────────────────────────
@tree.command(name="ai_clear_roles",
              description="Clear all AI role/channel restrictions. (Admin)")
async def ai_clear_roles(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    config["ai_role_ids"] = []
    config["ai_channel_ids"] = []
    save_config(config)
    await interaction.response.send_message(
        "✅ AI restrictions cleared — everyone can now use AI.", ephemeral=True)


# ── /ai_reset ──────────────────────────────────
@tree.command(name="ai_reset", description="Clear your AI conversation history.")
async def ai_reset(interaction: discord.Interaction):
    ai_conversations.pop(interaction.user.id, None)
    await interaction.response.send_message(
        "🧹 Your AI conversation history has been cleared.", ephemeral=True)


# ── /ai_reset_user ─────────────────────────────
@tree.command(name="ai_reset_user",
              description="Clear a specific user's AI conversation history. (Admin)")
@app_commands.describe(member="Member whose history to clear.")
async def ai_reset_user(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    ai_conversations.pop(member.id, None)
    await interaction.response.send_message(
        f"🧹 Cleared AI history for {member.mention}.", ephemeral=True)


# ── /ask ───────────────────────────────────────
@tree.command(name="ask", description="Ask the AI a question.")
@app_commands.describe(question="Your question for the AI.")
async def ask(interaction: discord.Interaction, question: str):
    if not config.get("ai_enabled", False):
        await interaction.response.send_message(
            "❌ AI is not enabled. Use `/ai_setup enable:True`.", ephemeral=True)
        return
    if not user_has_ai_access(interaction.user):
        await interaction.response.send_message(
            "❌ You don't have access to the AI.", ephemeral=True)
        return

    await interaction.response.defer()
    owner_ids = config.get("ai_owner_ids", [])
    is_owner  = interaction.user.id in owner_ids
    is_admin  = interaction.user.guild_permissions.administrator or is_owner
    reply_raw = await call_ai(interaction.user.id, question, is_admin=is_admin, is_owner=is_owner)

    # Strip any ACTION lines from /ask slash command (actions only via chat)
    display = "\n".join(
        l for l in reply_raw.split("\n") if not l.strip().startswith("ACTION:")
    ).strip()

    name = config.get("ai_name", "Atlas")
    await interaction.followup.send(embed=make_embed(
        title=f"🤖 {name}",
        description=display or "✅ Done.",
        color=discord.Color.blurple(),
        footer=f"Asked by {interaction.user.display_name}"
    ))






# ── /ai_setowner ───────────────────────────────
@tree.command(name="ai_setowner",
              description="Set a user as Jarvis owner — full obedience, called sir/ma'am. (Admin)")
@app_commands.describe(
    member="User to set as Jarvis owner.",
    remove="Set True to remove owner status instead."
)
async def ai_setowner(interaction: discord.Interaction,
                      member: discord.Member,
                      remove: bool = False):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    ids = config.get("ai_owner_ids", [])
    if remove:
        if member.id in ids:
            ids.remove(member.id)
            config["ai_owner_ids"] = ids
            save_config(config)
            await interaction.response.send_message(
                f"✅ Removed **{member}** from Jarvis owner list.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"⚠️ **{member}** is not in the owner list.", ephemeral=True)
    else:
        if member.id not in ids:
            ids.append(member.id)
            config["ai_owner_ids"] = ids
            save_config(config)
        await interaction.response.send_message(embed=make_embed(
            title="👑 Jarvis Owner Set",
            description=(
                f"**{member.mention}** is now a Jarvis owner.\n\n"
                "Jarvis will:\n"
                "• Obey them **100%** without question\n"
                "• Address them as **sir** or **ma'am**\n"
                "• Execute any server action they request\n"
                "• Respond to their name calls with *'Yes sir?'*"
            ),
            color=discord.Color.gold()
        ), ephemeral=True)


# ── /ai_setwake ────────────────────────────────
@tree.command(name="ai_setwake",
              description="Set the wake word(s) and response for Jarvis. (Admin)")
@app_commands.describe(
    wake_word="Word that triggers Jarvis (default: jarvis)",
    response="What Jarvis says when just called by name (default: Yes sir?)"
)
async def ai_setwake(interaction: discord.Interaction,
                     wake_word: str = "jarvis",
                     response: str = "Yes sir?"):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    config["ai_wake_words"] = [w.strip().lower() for w in wake_word.split(",")]
    config["ai_wake_response"] = response
    save_config(config)
    wakes = ", ".join(config["ai_wake_words"])
    await interaction.response.send_message(embed=make_embed(
        title="🔊 Wake Word Updated",
        description=f"**Wake words:** `{wakes}`\n**Response:** {response}",
        color=discord.Color.green()
    ), ephemeral=True)

# ── /ai_testkey ────────────────────────────────
@tree.command(name="ai_testkey",
              description="Test your current AI API key with a live call. (Admin)")
async def ai_testkey(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    provider = config.get("ai_provider", "gemini")
    key = get_api_key({"claude": "anthropic", "openai": "openai", "gemini": "gemini"}.get(provider, provider))

    if not key:
        await interaction.followup.send(
            f"❌ No API key found for **{provider}**. Use `/ai_setkey` to add it.", ephemeral=True)
        return

    # Make a minimal test call
    test_result = await call_ai(99999999, "Say exactly: API key works!", is_admin=False)
    # Clear the throwaway test history
    ai_conversations.pop(99999999, None)

    ok = not test_result.startswith("❌") and not test_result.startswith("⚠️")
    await interaction.followup.send(embed=make_embed(
        title="🔑 API Key Test",
        color=discord.Color.green() if ok else discord.Color.red(),
        fields=[
            ("Provider",  provider.upper(), True),
            ("Key preview", f"`{key[:8]}...`", True),
            ("Result", "✅ Working!" if ok else "❌ Failed", True),
            ("Response", test_result[:500], False),
        ]
    ), ephemeral=True)

# ── /ai_setkey ─────────────────────────────────
@tree.command(name="ai_setkey",
              description="Set an AI provider API key directly in Discord. (Admin only)")
@app_commands.describe(
    provider="Which AI provider this key is for.",
    key="Your API key. This message will be deleted immediately for security."
)
@app_commands.choices(provider=[
    app_commands.Choice(name="Gemini (Google)",    value="gemini"),
    app_commands.Choice(name="ChatGPT (OpenAI)",   value="openai"),
    app_commands.Choice(name="Claude (Anthropic)", value="claude"),
    app_commands.Choice(name="SambaNova Cloud",    value="sambanova"),
    app_commands.Choice(name="OpenRouter",          value="openrouter"),
])
async def ai_setkey(interaction: discord.Interaction,
                    provider: app_commands.Choice[str],
                    key: str):
    # Only admins can set keys
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return

    # Map provider choice to config key name
    key_map = {"gemini": "api_key_gemini", "openai": "api_key_openai", "claude": "api_key_anthropic", "sambanova": "api_key_sambanova", "openrouter": "api_key_openrouter"}
    config_key = key_map[provider.value]

    # Save key to config.json
    config[config_key] = key.strip()
    save_config(config)

    # Respond ephemerally so the key is never visible to others
    await interaction.response.send_message(
        embed=make_embed(
            title="✅ API Key Saved",
            description=(
                f"Your **{provider.name}** API key has been saved.\n\n"
                f"Key preview: `{key[:6]}{'*' * min(len(key)-6, 20)}`\n\n"
                f"Run `/ai_provider` to switch to this provider, "
                f"then `/ai_status` to confirm it's working."
            ),
            color=discord.Color.green(),
            footer="This message is only visible to you."
        ),
        ephemeral=True   # Only you can see it — key never shown in chat
    )
    print(f"[AI] API key for {provider.value} updated by {interaction.user}")

# ── /ai_status ─────────────────────────────────
@tree.command(name="ai_status", description="Show current AI configuration and status.")
async def ai_status(interaction: discord.Interaction):
    provider = config.get("ai_provider", "gemini")
    enabled  = config.get("ai_enabled", False)
    name     = config.get("ai_name", "Atlas")
    role_ids = config.get("ai_role_ids", [])
    ch_ids   = config.get("ai_channel_ids", [])
    model    = config.get(f"{provider}_model", {
        "claude": "claude-haiku-4-5-20251001",
        "openai": "gpt-4o-mini",
        "gemini": "gemini-2.0-flash"
    }.get(provider, "?"))

    # Check which keys are loaded
    key_status = {
        "claude": "✅ Set" if get_api_key("anthropic") else "❌ Not set — use /ai_setkey",
        "openai": "✅ Set" if get_api_key("openai")    else "❌ Not set — use /ai_setkey",
        "gemini":      "✅ Set" if get_api_key("gemini")      else "❌ Not set — use /ai_setkey",
        "sambanova":   "✅ Set" if get_api_key("sambanova")  else "❌ Not set — use /ai_setkey",
        "openrouter":  "✅ Set" if get_api_key("openrouter") else "❌ Not set — use /ai_setkey",
    }

    roles_display = ", ".join(f"<@&{r}>" for r in role_ids) if role_ids else "Everyone ✅"
    chans_display = ", ".join(f"<#{c}>" for c in ch_ids) if ch_ids else "All channels (via @mention)"

    e = make_embed(
        title=f"🤖 AI Status — {name}",
        color=discord.Color.green() if enabled else discord.Color.red(),
        fields=[
            ("Power",           "✅ Enabled" if enabled else "❌ Disabled",  True),
            ("Provider",        provider.upper(),                              True),
            ("Model",           model,                                         True),
            ("Claude key",      key_status["claude"],                         True),
            ("OpenAI key",      key_status["openai"],                         True),
            ("Gemini key",      key_status["gemini"],                         True),
            ("SambaNova key",   key_status["sambanova"],                      True),
            ("OpenRouter key",  key_status["openrouter"],                     True),
            ("Access Roles",    roles_display,                                False),
            ("AI Channels",     chans_display,                                False),
            ("How to chat",     f"@mention **{bot.user.mention}** in any channel, or chat freely in a designated AI channel.", False),
            ("Admin actions",   "Admins can say e.g. `ban PlayerName reason` and the bot will do it.", False),
        ]
    )
    await interaction.response.send_message(embed=e)

# ══════════════════════════════════════════════
#  SECTION 4 — AUTO-MODERATION
# ══════════════════════════════════════════════

URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)

async def handle_automod(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    content_lower = message.content.lower()
    author = message.author
    guild = message.guild

    for word in config.get("banned_words", []):
        if word.lower() in content_lower:
            await message.delete()
            await log_mod(guild, "🚫 Banned Word", author,
                          f"Message contained: `{word}`")
            return

    if config.get("anti_link", True):
        if not (author.guild_permissions.administrator or
                any(r.id == config.get("staff_role_id", 0) for r in author.roles)):
            if URL_RE.search(message.content):
                await message.delete()
                await log_mod(guild, "🔗 Anti-Link", author, "Deleted a link.")
                return

    now = asyncio.get_event_loop().time()
    uid = author.id
    spam_tracker.setdefault(uid, [])
    spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < 5]
    spam_tracker[uid].append(now)
    if len(spam_tracker[uid]) >= config.get("anti_spam_threshold", 5):
        spam_tracker[uid] = []
        try:
            await author.timeout(datetime.timedelta(minutes=5), reason="AutoMod: spam")
            await log_mod(guild, "⏱️ Auto-Timeout", author, "Spam detected.")
        except discord.Forbidden:
            pass


# ══════════════════════════════════════════════
#  SECTION 5 — LIVE MEMBER COUNTER
# ══════════════════════════════════════════════

@tasks.loop(minutes=5)
async def update_member_counter():
    ch_id = config.get("member_counter_channel_id", 0)
    if not ch_id:
        return
    for guild in bot.guilds:
        channel = guild.get_channel(ch_id)
        if not channel:
            continue
        online = sum(1 for m in guild.members
                     if m.status != discord.Status.offline and not m.bot)
        new_name = f"🟢 Online: {online}"
        try:
            if channel.name != new_name:
                await channel.edit(name=new_name)
        except (discord.Forbidden, discord.HTTPException):
            pass

@update_member_counter.before_loop
async def before_counter():
    await bot.wait_until_ready()


# ══════════════════════════════════════════════
#  SECTION 6 — WELCOME
# ══════════════════════════════════════════════

@bot.event
async def on_member_join(member: discord.Member):
    ch_id = config.get("welcome_channel_id", 0)
    if not ch_id:
        return
    channel = member.guild.get_channel(ch_id)
    if not channel:
        return
    msg = (config.get("welcome_message", DEFAULT_CONFIG["welcome_message"])
           .replace("{user}", member.mention)
           .replace("{server}", member.guild.name))
    e = make_embed(
        title="👋 Welcome!",
        description=msg,
        color=discord.Color.green(),
        fields=[
            ("Member #", str(member.guild.member_count), True),
            ("Account Created", member.created_at.strftime("%Y-%m-%d"), True),
        ],
        thumbnail_url=str(member.display_avatar.url)
    )
    try:
        sent = await channel.send(embed=e)
        await sent.add_reaction(config.get("welcome_emoji", "👋"))
    except (discord.Forbidden, discord.HTTPException):
        pass


# ══════════════════════════════════════════════
#  SECTION 7 — SLASH COMMANDS
# ══════════════════════════════════════════════

# ── /panel ─────────────────────────────────────
@tree.command(name="panel", description="Post the ticket panel with department dropdown. (Admin)")
async def panel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    e = make_embed(
        title="📋 Internal Support & Partnerships",
        description=(
            "Welcome to the official internal portal. "
            "Use the menu below to open a ticket in the correct department.\n\n"
            "**⚙️ General Support**\n*Bug reports, technical issues, or player assistance.*\n\n"
            "**💎 Sponsorships**\n*Creator applications and brand collaborations.*\n\n"
            "**Response Time**\nOur administration team usually responds within 12–24 hours."
        ),
        color=discord.Color.blurple(),
        footer="Internal SMP • Help Desk"
    )
    if interaction.guild.icon:
        e.set_thumbnail(url=interaction.guild.icon.url)
    await interaction.channel.send(embed=e, view=TicketPanelView())
    await interaction.response.send_message("✅ Ticket panel sent!", ephemeral=True)


# ── /setup_verification ────────────────────────
@tree.command(name="setup_verification",
              description="Post the verification panel. (Admin)")
@app_commands.describe(channel="Channel to post it in.",
                       verified_role="Role to give verified members.")
async def setup_verification(interaction: discord.Interaction,
                              channel: discord.TextChannel,
                              verified_role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    config["verification_channel_id"] = channel.id
    config["verified_role_id"] = verified_role.id
    save_config(config)
    e = make_embed(
        title="✅ Verification",
        description=(
            f"Click the button below to verify yourself and gain access.\n\n"
            f"You will receive the **{verified_role.name}** role."
        ),
        color=discord.Color.green(), footer="Click the button to verify."
    )
    await channel.send(embed=e, view=VerifyButton())
    await interaction.response.send_message(
        f"✅ Verification panel posted in {channel.mention}!", ephemeral=True)


# ── /setup_minecraft ───────────────────────────
@tree.command(name="setup_minecraft",
              description="Configure the Minecraft integration. (Admin)")
@app_commands.describe(
    server_ip="Your Minecraft server IP",
    port="Server port (default 25565)",
    status_channel="Voice channel to show live player count",
    events_channel="Text channel for MC event announcements"
)
async def setup_minecraft(interaction: discord.Interaction,
                           server_ip: str, port: int = 25565,
                           status_channel: discord.VoiceChannel = None,
                           events_channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    config["minecraft_server_ip"] = server_ip
    config["minecraft_server_port"] = port
    if status_channel:
        config["minecraft_status_channel_id"] = status_channel.id
    if events_channel:
        config["minecraft_events_channel_id"] = events_channel.id
    save_config(config)
    await interaction.response.send_message(embed=make_embed(
        title="⛏️ Minecraft Integration Configured",
        description=(
            f"**Server IP:** `{server_ip}:{port}`\n"
            f"**Status Channel:** {status_channel.mention if status_channel else 'Not set'}\n"
            f"**Events Channel:** {events_channel.mention if events_channel else 'Not set'}\n\n"
            "Player count updates every 5 minutes."
        ),
        color=discord.Color.green()
    ), ephemeral=True)


# ── /mc_status ─────────────────────────────────
@tree.command(name="mc_status", description="Check the Minecraft server status.")
async def mc_status(interaction: discord.Interaction):
    ip = config.get("minecraft_server_ip", "")
    if not ip:
        await interaction.response.send_message(
            "❌ Not configured. Use `/setup_minecraft`.", ephemeral=True)
        return
    await interaction.response.defer()
    port = config.get("minecraft_server_port", 25565)
    data = await fetch_mc_status(ip, port)

    if not data or not data.get("online"):
        await interaction.followup.send(embed=make_embed(
            title="⛏️ Minecraft Server",
            description=f"`{ip}:{port}` is **offline** or unreachable.",
            color=discord.Color.red()
        ))
        return

    players = data.get("players", {})
    online = players.get("online", 0)
    maximum = players.get("max", 0)
    player_list = players.get("list", [])
    version = data.get("version", "Unknown")
    motd_clean = " ".join(data.get("motd", {}).get("clean", ["No MOTD"]))

    fields = [
        ("🟢 Status", "Online", True),
        ("👥 Players", f"{online}/{maximum}", True),
        ("🔖 Version", version, True),
        ("📝 MOTD", motd_clean, False),
    ]
    if player_list:
        fields.append(("Online Players", ", ".join(player_list[:20]) or "Hidden", False))

    await interaction.followup.send(embed=make_embed(
        title=f"⛏️ {ip}", color=discord.Color.green(), fields=fields
    ))


# ── /mc_event ──────────────────────────────────
@tree.command(name="mc_event", description="Announce a custom Minecraft event. (Staff)")
@app_commands.describe(title="Event title", description="Event details",
                       starts_in="When does it start? e.g. 'Today at 6PM EST'")
async def mc_event(interaction: discord.Interaction, title: str,
                   description: str, starts_in: str):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    ch_id = config.get("minecraft_events_channel_id", 0)
    channel = interaction.guild.get_channel(ch_id) if ch_id else interaction.channel
    e = make_embed(
        title=f"⛏️ Minecraft Event — {title}",
        description=(
            f"{description}\n\n"
            f"⏰ **Starts:** {starts_in}\n"
            f"📣 **Announced by:** {interaction.user.mention}"
        ),
        color=discord.Color.green(), footer="Minecraft Events"
    )
    await channel.send("@everyone", embed=e)
    await interaction.response.send_message("✅ Event announced!", ephemeral=True)


# ── /ping ──────────────────────────────────────
@tree.command(name="ping", description="Check bot latency.")
async def ping(interaction: discord.Interaction):
    ms = round(bot.latency * 1000)
    color = (discord.Color.green() if ms < 100
             else discord.Color.orange() if ms < 200
             else discord.Color.red())
    await interaction.response.send_message(embed=make_embed(
        title="🏓 Pong!", description=f"Websocket latency: **{ms}ms**", color=color
    ))


# ── /userinfo ──────────────────────────────────
@tree.command(name="userinfo", description="Show info about a user.")
@app_commands.describe(member="The member to look up.")
async def userinfo(interaction: discord.Interaction,
                   member: discord.Member = None):
    member = member or interaction.user
    roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
    e = make_embed(
        title=f"👤 {member}", color=member.color,
        thumbnail_url=str(member.display_avatar.url),
        fields=[
            ("ID", str(member.id), True),
            ("Nickname", member.nick or "None", True),
            ("Bot?", "Yes" if member.bot else "No", True),
            ("Joined Server",
             member.joined_at.strftime("%Y-%m-%d") if member.joined_at else "?", True),
            ("Account Created", member.created_at.strftime("%Y-%m-%d"), True),
            ("Top Role", member.top_role.mention, True),
            (f"Roles ({len(roles)})", " ".join(roles[:10]) or "None", False),
        ]
    )
    await interaction.response.send_message(embed=e)


# ── /serverinfo ────────────────────────────────
@tree.command(name="serverinfo", description="Display server information.")
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    e = make_embed(
        title=f"🌐 {g.name}", color=discord.Color.blurple(),
        footer=f"ID: {g.id}",
        thumbnail_url=str(g.icon.url) if g.icon else "",
        fields=[
            ("Owner", f"<@{g.owner_id}>", True),
            ("Members", str(g.member_count), True),
            ("Channels", str(len(g.channels)), True),
            ("Roles", str(len(g.roles)), True),
            ("Boost Level", f"Level {g.premium_tier}", True),
            ("Created", g.created_at.strftime("%Y-%m-%d"), True),
        ]
    )
    await interaction.response.send_message(embed=e)


# ── /clear ─────────────────────────────────────
@tree.command(name="clear", description="Bulk delete messages. (Staff)")
@app_commands.describe(amount="1–100 messages to delete.")
async def clear(interaction: discord.Interaction, amount: int):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    if not 1 <= amount <= 100:
        await interaction.response.send_message("❌ Between 1 and 100.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"✅ Deleted {len(deleted)} messages.", ephemeral=True)


# ── /announce ──────────────────────────────────
@tree.command(name="announce", description="Send an announcement embed. (Staff)")
@app_commands.describe(title="Title", message="Body text",
                       channel="Channel to send to")
async def announce(interaction: discord.Interaction, title: str, message: str,
                   channel: discord.TextChannel = None):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    target = channel or interaction.channel
    await target.send(embed=make_embed(
        title=f"📢 {title}", description=message,
        color=discord.Color.gold(),
        footer=f"Announced by {interaction.user}"
    ))
    await interaction.response.send_message("✅ Sent!", ephemeral=True)


# ── /lock / /unlock ────────────────────────────
@tree.command(name="lock", description="Lock a channel. (Staff)")
@app_commands.describe(channel="Channel to lock.")
async def lock(interaction: discord.Interaction,
               channel: discord.TextChannel = None):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    t = channel or interaction.channel
    ow = t.overwrites_for(interaction.guild.default_role)
    ow.send_messages = False
    await t.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.response.send_message(embed=make_embed(
        title="🔒 Locked", description=f"{t.mention} is now locked.",
        color=discord.Color.red()))

@tree.command(name="unlock", description="Unlock a channel. (Staff)")
@app_commands.describe(channel="Channel to unlock.")
async def unlock(interaction: discord.Interaction,
                 channel: discord.TextChannel = None):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    t = channel or interaction.channel
    ow = t.overwrites_for(interaction.guild.default_role)
    ow.send_messages = True
    await t.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.response.send_message(embed=make_embed(
        title="🔓 Unlocked", description=f"{t.mention} is now unlocked.",
        color=discord.Color.green()))


# ── /warn ──────────────────────────────────────
@tree.command(name="warn", description="Warn a user. (Staff)")
@app_commands.describe(member="Member to warn.", reason="Reason.")
async def warn(interaction: discord.Interaction, member: discord.Member,
               reason: str):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    try:
        await member.send(embed=make_embed(
            title="⚠️ Warning",
            description=f"You were warned in **{interaction.guild.name}**.\n**Reason:** {reason}",
            color=discord.Color.yellow()))
    except discord.Forbidden:
        pass
    await log_mod(interaction.guild, "⚠️ Warning", member, reason, interaction.user)
    await interaction.response.send_message(embed=make_embed(
        title="⚠️ Warned",
        description=f"{member.mention} warned.\n**Reason:** {reason}",
        color=discord.Color.yellow()))


# ── /timeout ───────────────────────────────────
@tree.command(name="timeout", description="Timeout a user. (Staff)")
@app_commands.describe(member="Member.", minutes="Duration in minutes.",
                       reason="Reason.")
async def timeout_cmd(interaction: discord.Interaction, member: discord.Member,
                      minutes: int, reason: str = "No reason provided"):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    try:
        await member.timeout(datetime.timedelta(minutes=minutes), reason=reason)
        await log_mod(interaction.guild, f"⏱️ Timeout ({minutes}m)",
                      member, reason, interaction.user)
        await interaction.response.send_message(embed=make_embed(
            title="⏱️ Timed Out",
            description=f"{member.mention} timed out for **{minutes}m**.\n**Reason:** {reason}",
            color=discord.Color.orange()))
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)


# ── /kick ──────────────────────────────────────
@tree.command(name="kick", description="Kick a member. (Staff)")
@app_commands.describe(member="Member to kick.", reason="Reason.")
async def kick(interaction: discord.Interaction, member: discord.Member,
               reason: str = "No reason provided"):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    try:
        await member.kick(reason=reason)
        await log_mod(interaction.guild, "👢 Kick", member, reason, interaction.user)
        await interaction.response.send_message(embed=make_embed(
            title="👢 Kicked",
            description=f"{member.mention} was kicked.\n**Reason:** {reason}",
            color=discord.Color.orange()))
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)


# ── /ban ───────────────────────────────────────
@tree.command(name="ban", description="Ban a member. (Admin)")
@app_commands.describe(member="Member to ban.", reason="Reason.")
async def ban(interaction: discord.Interaction, member: discord.Member,
              reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    try:
        await member.ban(reason=reason)
        await log_mod(interaction.guild, "🔨 Ban", member, reason, interaction.user)
        await interaction.response.send_message(embed=make_embed(
            title="🔨 Banned",
            description=f"{member.mention} was banned.\n**Reason:** {reason}",
            color=discord.Color.red()))
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)


# ── /unban ─────────────────────────────────────
@tree.command(name="unban", description="Unban a user by ID. (Admin)")
@app_commands.describe(user_id="User ID to unban.", reason="Reason.")
async def unban(interaction: discord.Interaction, user_id: str,
                reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=reason)
        await interaction.response.send_message(embed=make_embed(
            title="✅ Unbanned",
            description=f"**{user}** has been unbanned.",
            color=discord.Color.green()))
    except (discord.NotFound, ValueError):
        await interaction.response.send_message(
            "❌ User not found or not banned.", ephemeral=True)


# ── /slowmode ──────────────────────────────────
@tree.command(name="slowmode", description="Set slowmode in a channel. (Staff)")
@app_commands.describe(seconds="Slowmode seconds (0 to disable).",
                       channel="Target channel.")
async def slowmode(interaction: discord.Interaction, seconds: int,
                   channel: discord.TextChannel = None):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    t = channel or interaction.channel
    await t.edit(slowmode_delay=seconds)
    label = f"{seconds}s" if seconds > 0 else "disabled"
    await interaction.response.send_message(embed=make_embed(
        title="🐢 Slowmode",
        description=f"Slowmode in {t.mention} set to **{label}**.",
        color=discord.Color.blurple()))


# ── /role_add / /role_remove ───────────────────
@tree.command(name="role_add", description="Add a role to a user. (Staff)")
@app_commands.describe(member="Member.", role="Role to add.")
async def role_add(interaction: discord.Interaction, member: discord.Member,
                   role: discord.Role):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    await member.add_roles(role)
    await interaction.response.send_message(embed=make_embed(
        title="✅ Role Added",
        description=f"Added {role.mention} to {member.mention}.",
        color=discord.Color.green()))

@tree.command(name="role_remove", description="Remove a role from a user. (Staff)")
@app_commands.describe(member="Member.", role="Role to remove.")
async def role_remove(interaction: discord.Interaction, member: discord.Member,
                      role: discord.Role):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    await member.remove_roles(role)
    await interaction.response.send_message(embed=make_embed(
        title="✅ Role Removed",
        description=f"Removed {role.mention} from {member.mention}.",
        color=discord.Color.orange()))


# ── /setup_counter ─────────────────────────────
@tree.command(name="setup_counter",
              description="Create live member counter channel. (Admin)")
async def setup_counter(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    guild = interaction.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
    }
    online = sum(1 for m in guild.members
                 if m.status != discord.Status.offline and not m.bot)
    ch = await guild.create_voice_channel(f"🟢 Online: {online}",
                                           overwrites=overwrites)
    config["member_counter_channel_id"] = ch.id
    save_config(config)
    await interaction.response.send_message(embed=make_embed(
        title="📈 Counter Created",
        description=f"{ch.mention} will update every 5 minutes.",
        color=discord.Color.green()), ephemeral=True)


# ── /config_view ───────────────────────────────
@tree.command(name="config_view", description="View bot config. (Admin)")
async def config_view(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    display = "\n".join(f"`{k}`: `{v}`" for k, v in config.items()
                        if k not in ("banned_words",))
    await interaction.response.send_message(embed=make_embed(
        title="⚙️ Config", description=display,
        color=discord.Color.blurple()), ephemeral=True)



# ══════════════════════════════════════════════
#  MULTIMODAL + FILE + PLUGIN SYSTEM
# ══════════════════════════════════════════════

async def call_ai_with_attachments(user_id, text, attachments, is_admin=False, is_owner=False):
    """Handle text + file/image attachments together."""
    extra_context = ""
    image_data_list = []

    for att in attachments:
        name_lower = att.filename.lower()
        text_exts = [".txt", ".java", ".yml", ".yaml", ".json", ".py",
                     ".js", ".ts", ".xml", ".gradle", ".properties",
                     ".sk", ".cfg", ".ini", ".md", ".log", ".csv", ".sql",
                     ".kt", ".groovy", ".sh", ".bat"]
        img_exts = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]

        if any(name_lower.endswith(e) for e in text_exts):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(att.url) as resp:
                        file_text = await resp.text(errors="replace")
                extra_context += f"\n\n[FILE: {att.filename}]\n```\n{file_text[:4000]}\n```"
            except Exception as e:
                extra_context += f"\n\n[Could not read {att.filename}: {e}]"

        elif any(name_lower.endswith(e) for e in img_exts):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(att.url) as resp:
                        img_bytes = await resp.read()
                ext_mime = {".png": "image/png", ".jpg": "image/jpeg",
                            ".jpeg": "image/jpeg", ".gif": "image/gif",
                            ".webp": "image/webp", ".bmp": "image/bmp"}
                mime = next((v for k, v in ext_mime.items() if name_lower.endswith(k)), "image/png")
                b64 = base64.b64encode(img_bytes).decode()
                image_data_list.append((b64, mime, att.filename))
            except Exception as e:
                extra_context += f"\n\n[Could not read image {att.filename}: {e}]"

        elif name_lower.endswith(".zip"):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(att.url) as resp:
                        zip_bytes = await resp.read()
                with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                    names = zf.namelist()
                    extra_context += f"\n\n[ZIP: {att.filename} contains: {', '.join(names[:15])}]"
                    for name in names[:8]:
                        if any(name.endswith(e) for e in [".java", ".yml", ".json", ".xml", ".py"]):
                            try:
                                content = zf.read(name).decode("utf-8", errors="replace")
                                extra_context += f"\n[{name}]\n```\n{content[:1500]}\n```"
                            except Exception:
                                pass
            except Exception as e:
                extra_context += f"\n\n[Could not read ZIP: {e}]"

    full_text = text + extra_context

    if image_data_list:
        return await _call_ai_vision(user_id, full_text, image_data_list, is_admin, is_owner)

    return await call_ai(user_id, full_text, is_admin=is_admin, is_owner=is_owner)


async def _call_ai_vision(user_id, text, images, is_admin, is_owner):
    """Send message + images to a vision model via OpenRouter or OpenAI."""
    provider = config.get("ai_provider", "openrouter").lower()
    system_prompt = build_ai_system_prompt(is_admin, is_owner)

    content = [{"type": "text", "text": text}]
    for b64, mime, fname in images:
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

    history = ai_conversations.setdefault(user_id, [])
    history.append({"role": "user", "content": content})
    max_h = config.get("ai_max_history", 15)
    if len(history) > max_h:
        ai_conversations[user_id] = history[-max_h:]
        history = ai_conversations[user_id]

    if provider in ("openrouter", "openai"):
        key = get_api_key("openrouter") if provider == "openrouter" else get_api_key("openai")
        if not key:
            return "❌ Image reading needs an OpenRouter or OpenAI key. Use `/ai_setkey`."
        if provider == "openrouter":
            model = config.get("openrouter_model", "meta-llama/llama-3.2-90b-vision-instruct")
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                       "HTTP-Referer": "https://discord.com", "X-Title": "Internal SMP Jarvis"}
        else:
            model = "gpt-4o"
            url = "https://api.openai.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        messages_payload = [{"role": "system", "content": system_prompt}] + history
        payload = {"model": model, "messages": messages_payload, "max_tokens": 1500}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=45)) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        return f"❌ Vision error ({resp.status}): {raw[:300]}"
                    data = json.loads(raw)
                    reply = data["choices"][0]["message"]["content"]
                    history.append({"role": "assistant", "content": reply})
                    return reply
        except Exception as e:
            return f"❌ Vision error: {e}"
    else:
        img_names = ", ".join(f[2] for f in images)
        note = f"\n\n[I can see {img_names} was attached but {provider} doesn't support vision. Switch to OpenRouter with model `meta-llama/llama-3.2-90b-vision-instruct` using `/ai_provider`.]"
        return await call_ai(user_id, text + note, is_admin=is_admin, is_owner=is_owner)


async def generate_plugin_code(description, plugin_name, user_id):
    """Ask Jarvis to generate a complete Minecraft plugin."""
    prompt = (
        f"Generate a complete Minecraft Bukkit/Spigot/Paper plugin.\n\n"
        f"Plugin Name: {plugin_name}\n"
        f"Requirements: {description}\n\n"
        "Provide ALL of the following clearly labelled:\n"
        "1. Main Java class — complete working code\n"
        "2. plugin.yml — all metadata, commands, permissions\n"
        "3. pom.xml — Maven build targeting Paper 1.21\n"
        "4. Any additional Java classes\n"
        "5. Install instructions\n\n"
        "Write COMPLETE production-ready code. No placeholders. Full error handling."
    )
    response = await call_ai(user_id, prompt, is_admin=True, is_owner=True)
    coding_sessions.append({
        "type": "plugin", "name": plugin_name,
        "description": description[:100],
        "timestamp": datetime.datetime.utcnow().isoformat()
    })
    return response


async def create_plugin_zip(plugin_name, code_response):
    """Package plugin code into a ZIP with Maven project structure."""
    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            java_blocks = re.findall(r"```(?:java)?\n(.*?)```", code_response, re.DOTALL)
            yml_blocks  = re.findall(r"```(?:yaml|yml)?\n(.*?)```", code_response, re.DOTALL)
            xml_blocks  = re.findall(r"```(?:xml)?\n(.*?)```", code_response, re.DOTALL)
            safe_name = re.sub(r"[^a-zA-Z0-9]", "", plugin_name)

            for java_code in java_blocks:
                if "class " in java_code:
                    m = re.search(r"public class (\w+)", java_code)
                    cname = m.group(1) if m else safe_name
                    zf.writestr(f"src/main/java/com/internalsmp/{safe_name.lower()}/{cname}.java", java_code)

            for yml_code in yml_blocks:
                if "main:" in yml_code or "name:" in yml_code:
                    zf.writestr("src/main/resources/plugin.yml", yml_code)
                    break

            pom_written = False
            for xml_code in xml_blocks:
                if "<project>" in xml_code or "<artifactId>" in xml_code:
                    zf.writestr("pom.xml", xml_code)
                    pom_written = True
                    break

            if not pom_written:
                default_pom = f"""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.internalsmp</groupId>
    <artifactId>{safe_name.lower()}</artifactId>
    <version>1.0-SNAPSHOT</version>
    <packaging>jar</packaging>
    <repositories>
        <repository>
            <id>papermc</id>
            <url>https://repo.papermc.io/repository/maven-public/</url>
        </repository>
    </repositories>
    <dependencies>
        <dependency>
            <groupId>io.papermc.paper</groupId>
            <artifactId>paper-api</artifactId>
            <version>1.21.1-R0.1-SNAPSHOT</version>
            <scope>provided</scope>
        </dependency>
    </dependencies>
    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-compiler-plugin</artifactId>
                <version>3.11.0</version>
                <configuration><source>21</source><target>21</target></configuration>
            </plugin>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-shade-plugin</artifactId>
                <version>3.5.0</version>
                <executions>
                    <execution><phase>package</phase><goals><goal>shade</goal></goals></execution>
                </executions>
            </plugin>
        </plugins>
    </build>
</project>"""
                zf.writestr("pom.xml", default_pom)

            zf.writestr("README.md",
                f"# {plugin_name}\nGenerated by Jarvis — Internal SMP Bot\n\n"
                f"## Build\n```\nmvn clean package\n```\n"
                f"Copy `target/{safe_name.lower()}-1.0-SNAPSHOT.jar` to `/plugins/`\n")

        buf.seek(0)
        return buf.read()
    except Exception as e:
        print(f"[ZIP] Error: {e}")
        return None


# ── /plugin ────────────────────────────────────
@tree.command(name="plugin",
              description="Ask Jarvis to create a Minecraft plugin for you.")
@app_commands.describe(
    name="Plugin name e.g. SuperKits",
    description="What should the plugin do? Be as detailed as possible."
)
async def plugin_cmd(interaction: discord.Interaction, name: str, description: str):
    await interaction.response.defer()

    await interaction.followup.send(embed=make_embed(
        title=f"⚙️ Generating: {name}",
        description=f"Jarvis is writing your plugin...\n\n**Requirements:** {description[:300]}",
        color=discord.Color.blurple(), footer="This takes 15-30 seconds."
    ))

    code = await generate_plugin_code(description, name, interaction.user.id)

    await interaction.followup.send(embed=make_embed(
        title=f"✅ Plugin Ready: {name}",
        description=(
            "**Files in ZIP:**\n"
            "• Java class(es)\n• `plugin.yml`\n• `pom.xml`\n• `README.md`\n\n"
            "**Build:** `mvn clean package` → copy JAR to `/plugins/`"
        ),
        color=discord.Color.green(), footer="Generated by Jarvis • Internal SMP"
    ))

    # Post code in chunks
    chunks = [code[i:i+1800] for i in range(0, min(len(code), 5400), 1800)]
    for chunk in chunks:
        await interaction.followup.send(f"```\n{chunk}\n```")

    # Upload ZIP
    zip_bytes = await create_plugin_zip(name, code)
    if zip_bytes:
        safe = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        await interaction.followup.send(
            content="📦 **Plugin project ZIP:**",
            file=discord.File(io.BytesIO(zip_bytes), filename=f"{safe}_plugin.zip")
        )


# ── /code ──────────────────────────────────────
@tree.command(name="code", description="Ask Jarvis to write code and download it as a file.")
@app_commands.describe(
    task="What code to write",
    filename="Output filename e.g. main.py, MyClass.java, config.yml"
)
async def code_cmd(interaction: discord.Interaction, task: str, filename: str = "output.txt"):
    await interaction.response.defer()

    prompt = f"Write complete, working code ONLY (no explanation, no markdown fences):\n\n{task}"
    code = await call_ai(interaction.user.id, prompt, is_admin=True, is_owner=True)
    clean_code = re.sub(r"^```[\w]*\n?", "", code.strip())
    clean_code = re.sub(r"```$", "", clean_code.strip())

    f = discord.File(io.BytesIO(clean_code.encode()), filename=filename)
    await interaction.followup.send(
        embed=make_embed(title=f"💻 {filename}", description=f"Task: {task[:200]}",
                         color=discord.Color.blurple(), footer="Download below"),
        file=f
    )
    coding_sessions.append({"type": "code", "filename": filename, "task": task,
                            "timestamp": datetime.datetime.utcnow().isoformat()})


# ── /upload_and_ask ────────────────────────────
@tree.command(name="upload_and_ask",
              description="Upload a file or image and ask Jarvis about it.")
@app_commands.describe(
    question="What do you want Jarvis to do with the file?",
    file="The file or image to analyse."
)
async def upload_and_ask(interaction: discord.Interaction,
                          question: str, file: discord.Attachment):
    await interaction.response.defer()
    is_owner = interaction.user.id in config.get("ai_owner_ids", [])
    is_admin = interaction.user.guild_permissions.administrator or is_owner

    reply = await call_ai_with_attachments(interaction.user.id, question, [file],
                                           is_admin=is_admin, is_owner=is_owner)

    code_blocks = re.findall(r"```(?:\w+)?\n(.*?)```", reply, re.DOTALL)
    chunks = [reply[i:i+1900] for i in range(0, len(reply), 1900)]
    for chunk in chunks[:3]:
        await interaction.followup.send(chunk)

    if code_blocks and len(code_blocks[0].strip()) > 50:
        code_text = code_blocks[0]
        ext = ("java" if "class " in code_text else
               "py" if "def " in code_text or "import " in code_text else
               "yml" if "name:" in code_text else
               "xml" if "<?xml" in code_text else "txt")
        await interaction.followup.send(
            content="📎 **Code extracted:**",
            file=discord.File(io.BytesIO(code_text.encode()), filename=f"jarvis_{ext}.{ext}")
        )


# ── /fix_code ──────────────────────────────────
@tree.command(name="fix_code", description="Upload broken code — Jarvis will fix it.")
@app_commands.describe(
    error="The error message or what is wrong",
    file="Your broken code file"
)
async def fix_code(interaction: discord.Interaction, error: str, file: discord.Attachment):
    await interaction.response.defer()
    is_owner = interaction.user.id in config.get("ai_owner_ids", [])
    is_admin = interaction.user.guild_permissions.administrator or is_owner

    question = f"Fix this code. Problem: {error}\n\nReturn the complete fixed version."
    reply = await call_ai_with_attachments(interaction.user.id, question, [file],
                                           is_admin=is_admin, is_owner=is_owner)

    code_blocks = re.findall(r"```(?:\w+)?\n(.*?)```", reply, re.DOTALL)
    await interaction.followup.send(embed=make_embed(
        title="🔧 Code Fixed", description=reply[:1500],
        color=discord.Color.green(), footer="Fixed by Jarvis"
    ))

    if code_blocks:
        await interaction.followup.send(
            content="✅ **Fixed file:**",
            file=discord.File(io.BytesIO(code_blocks[0].encode()),
                              filename=f"fixed_{file.filename}")
        )


# ── /coding_history ────────────────────────────
@tree.command(name="coding_history",
              description="See what Jarvis has learned from coding sessions.")
async def coding_history_cmd(interaction: discord.Interaction):
    if not coding_sessions:
        await interaction.response.send_message(
            "No coding sessions yet! Use `/plugin` or `/code` to start.", ephemeral=True)
        return
    lines = []
    for i, s in enumerate(coding_sessions[-10:], 1):
        ts = s.get("timestamp", "")[:10]
        if s.get("type") == "plugin":
            lines.append(f"`{i}.` 🔧 Plugin: **{s.get('name','?')}** ({ts})")
        else:
            lines.append(f"`{i}.` 💻 Code: **{s.get('filename','?')}** ({ts})")
    await interaction.response.send_message(embed=make_embed(
        title="🧠 Jarvis Coding History",
        description="\n".join(lines) + "\n\n*Jarvis learns from every session.*",
        color=discord.Color.blurple(),
        footer=f"Total sessions: {len(coding_sessions)}"
    ))



# ══════════════════════════════════════════════
#  🎭 REACTION ROLES SYSTEM
#  - Admins create panels with /rr_create
#  - Add roles+emoji+label with /rr_add
#  - Post the panel with /rr_post (supports image)
#  - Buttons persist after restarts
#  - Users click to get/remove roles
# ══════════════════════════════════════════════

# Storage: rr_panels[panel_id] = {title, description, image_url, roles: [{role_id, emoji, label}]}
rr_panels: dict[str, dict] = {}
# rr_messages[message_id] = panel_id  — so we know which panel a button press belongs to
rr_messages: dict[int, str] = {}

RR_PANELS_FILE = "rr_panels.json"

def rr_save():
    """Persist reaction role panels to disk."""
    with open(RR_PANELS_FILE, "w") as f:
        json.dump({"panels": rr_panels, "messages": {str(k): v for k, v in rr_messages.items()}}, f, indent=2)

def rr_load():
    """Load reaction role panels from disk."""
    global rr_panels, rr_messages
    if not os.path.exists(RR_PANELS_FILE):
        return
    try:
        with open(RR_PANELS_FILE) as f:
            data = json.load(f)
        rr_panels  = data.get("panels", {})
        rr_messages = {int(k): v for k, v in data.get("messages", {}).items()}
    except Exception as e:
        print(f"[RR] Load error: {e}")


class ReactionRoleView(ui.View):
    """
    Persistent view for a reaction roles panel.
    Each button gives/removes a specific role.
    custom_id format: rr_{panel_id}_{role_id}
    """
    def __init__(self, panel_id: str, roles: list[dict]):
        super().__init__(timeout=None)
        for entry in roles:
            role_id = entry["role_id"]
            emoji   = entry.get("emoji", "🎭")
            label   = entry.get("label", "Role")
            btn = ui.Button(
                label=label,
                emoji=emoji,
                style=discord.ButtonStyle.blurple,
                custom_id=f"rr_{panel_id}_{role_id}"
            )
            btn.callback = self._make_callback(role_id)
            self.add_item(btn)

    @staticmethod
    def _make_callback(role_id: int):
        async def callback(interaction: discord.Interaction):
            guild  = interaction.guild
            member = interaction.user
            role   = guild.get_role(role_id)

            if not role:
                await interaction.response.send_message(
                    "❌ Role not found. An admin may have deleted it.", ephemeral=True)
                return

            if role in member.roles:
                await member.remove_roles(role, reason="Reaction role removed")
                await interaction.response.send_message(
                    f"✅ Removed **{role.name}** from you.", ephemeral=True)
            else:
                await member.add_roles(role, reason="Reaction role assigned")
                await interaction.response.send_message(
                    f"✅ You now have **{role.name}**!", ephemeral=True)
        return callback


def build_rr_view(panel_id: str) -> ReactionRoleView | None:
    """Rebuild a ReactionRoleView from saved panel data."""
    panel = rr_panels.get(panel_id)
    if not panel:
        return None
    return ReactionRoleView(panel_id, panel.get("roles", []))


# ── /rr_create ─────────────────────────────────
@tree.command(name="rr_create",
              description="Create a new reaction roles panel. (Admin)")
@app_commands.describe(
    panel_id="Short unique ID for this panel e.g. 'notifications'",
    title="Panel title e.g. 'Reaction Roles'",
    description="Description shown under the title",
    image_url="Optional image URL to show at the top of the panel"
)
async def rr_create(interaction: discord.Interaction,
                    panel_id: str,
                    title: str,
                    description: str,
                    image_url: str = ""):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return

    panel_id = panel_id.lower().replace(" ", "_")
    rr_panels[panel_id] = {
        "title": title,
        "description": description,
        "image_url": image_url,
        "roles": []
    }
    rr_save()

    await interaction.response.send_message(embed=make_embed(
        title="✅ Panel Created",
        description=(
            f"**ID:** `{panel_id}`\n"
            f"**Title:** {title}\n\n"
            f"Now use `/rr_add panel_id:{panel_id}` to add roles to it.\n"
            f"Then use `/rr_post panel_id:{panel_id}` to post it."
        ),
        color=discord.Color.green()
    ), ephemeral=True)


# ── /rr_add ────────────────────────────────────
@tree.command(name="rr_add",
              description="Add a role button to a reaction roles panel. (Admin)")
@app_commands.describe(
    panel_id="The panel ID to add this role to",
    role="The role to assign when this button is clicked",
    emoji="Emoji shown on the button e.g. 📢",
    label="Text label on the button e.g. 'Events'"
)
async def rr_add(interaction: discord.Interaction,
                 panel_id: str,
                 role: discord.Role,
                 emoji: str,
                 label: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return

    panel_id = panel_id.lower().replace(" ", "_")
    if panel_id not in rr_panels:
        await interaction.response.send_message(
            f"❌ Panel `{panel_id}` not found. Create it first with `/rr_create`.", ephemeral=True)
        return

    if len(rr_panels[panel_id]["roles"]) >= 25:
        await interaction.response.send_message(
            "❌ Max 25 buttons per panel.", ephemeral=True)
        return

    rr_panels[panel_id]["roles"].append({
        "role_id": role.id,
        "emoji": emoji,
        "label": label
    })
    rr_save()

    count = len(rr_panels[panel_id]["roles"])
    await interaction.response.send_message(embed=make_embed(
        title="✅ Role Added",
        description=(
            f"Added **{emoji} {label}** → {role.mention} to panel `{panel_id}`\n"
            f"Panel now has **{count}** button(s).\n\n"
            f"Use `/rr_post panel_id:{panel_id}` when ready."
        ),
        color=discord.Color.green()
    ), ephemeral=True)


# ── /rr_remove ─────────────────────────────────
@tree.command(name="rr_remove",
              description="Remove a role from a reaction roles panel. (Admin)")
@app_commands.describe(
    panel_id="Panel ID",
    role="Role to remove from the panel"
)
async def rr_remove(interaction: discord.Interaction,
                    panel_id: str, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    panel_id = panel_id.lower()
    if panel_id not in rr_panels:
        await interaction.response.send_message("❌ Panel not found.", ephemeral=True)
        return
    before = len(rr_panels[panel_id]["roles"])
    rr_panels[panel_id]["roles"] = [
        r for r in rr_panels[panel_id]["roles"] if r["role_id"] != role.id
    ]
    after = len(rr_panels[panel_id]["roles"])
    rr_save()
    await interaction.response.send_message(
        f"✅ Removed {role.mention} from panel `{panel_id}`. ({before} → {after} buttons)",
        ephemeral=True)


# ── /rr_post ───────────────────────────────────
@tree.command(name="rr_post",
              description="Post a reaction roles panel to a channel. (Admin)")
@app_commands.describe(
    panel_id="Panel ID to post",
    channel="Channel to post it in (defaults to current)"
)
async def rr_post(interaction: discord.Interaction,
                  panel_id: str,
                  channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return

    panel_id = panel_id.lower().replace(" ", "_")
    panel = rr_panels.get(panel_id)
    if not panel:
        await interaction.response.send_message(
            f"❌ Panel `{panel_id}` not found.", ephemeral=True)
        return
    if not panel.get("roles"):
        await interaction.response.send_message(
            f"❌ Panel `{panel_id}` has no roles. Use `/rr_add` first.", ephemeral=True)
        return

    target = channel or interaction.channel

    # Build the description with role list
    role_lines = []
    for entry in panel["roles"]:
        role = interaction.guild.get_role(entry["role_id"])
        role_mention = role.mention if role else f"<@&{entry['role_id']}>"
        role_lines.append(f"{entry['emoji']} — Click to get pinged for {role_mention}")

    full_desc = panel["description"] + "\n\n" + "\n".join(role_lines)

    e = make_embed(
        title=panel["title"],
        description=full_desc,
        color=discord.Color.blurple(),
        footer="Click a button below to get or remove a role."
    )

    # Attach image if set
    if panel.get("image_url"):
        e.set_image(url=panel["image_url"])

    view = ReactionRoleView(panel_id, panel["roles"])
    msg = await target.send(embed=e, view=view)

    # Save message ID so we can re-register on restart
    rr_messages[msg.id] = panel_id
    rr_save()

    await interaction.response.send_message(
        f"✅ Posted panel `{panel_id}` in {target.mention}!", ephemeral=True)


# ── /rr_list ───────────────────────────────────
@tree.command(name="rr_list",
              description="List all reaction role panels. (Admin)")
async def rr_list(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    if not rr_panels:
        await interaction.response.send_message(
            "No panels yet. Use `/rr_create` to make one.", ephemeral=True)
        return
    lines = []
    for pid, panel in rr_panels.items():
        count = len(panel.get("roles", []))
        lines.append(f"`{pid}` — **{panel['title']}** ({count} roles)")
    await interaction.response.send_message(embed=make_embed(
        title="🎭 Reaction Role Panels",
        description="\n".join(lines),
        color=discord.Color.blurple()
    ), ephemeral=True)


# ── /rr_delete ─────────────────────────────────
@tree.command(name="rr_delete",
              description="Delete a reaction roles panel. (Admin)")
@app_commands.describe(panel_id="Panel ID to delete")
async def rr_delete(interaction: discord.Interaction, panel_id: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    panel_id = panel_id.lower()
    if panel_id not in rr_panels:
        await interaction.response.send_message("❌ Panel not found.", ephemeral=True)
        return
    del rr_panels[panel_id]
    rr_save()
    await interaction.response.send_message(
        f"✅ Deleted panel `{panel_id}`.", ephemeral=True)


# ══════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"[BOT] {bot.user} ready.")
    # Load reaction role panels from disk
    rr_load()
    # Re-register all saved reaction role panel views (persistent across restarts)
    for panel_id, panel in rr_panels.items():
        roles = panel.get("roles", [])
        if roles:
            bot.add_view(ReactionRoleView(panel_id, roles))
    bot.add_view(TicketPanelView())
    bot.add_view(TicketManageView())
    bot.add_view(VerifyButton())
    try:
        synced = await tree.sync()
        print(f"[SLASH] Synced {len(synced)} commands.")
    except Exception as e:
        print(f"[SLASH] Error: {e}")
    if not update_member_counter.is_running():
        update_member_counter.start()
    if not update_mc_counter.is_running():
        update_mc_counter.start()
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching,
                                  name="the server ⚔️"),
        status=discord.Status.online
    )

@bot.event
async def on_message(message: discord.Message):
    await handle_automod(message)
    await handle_ai_message(message)   # AI responds if enabled + user has access
    await bot.process_commands(message)

@bot.event
async def on_app_command_error(interaction: discord.Interaction,
                                error: app_commands.AppCommandError):
    msg = "An unexpected error occurred."
    if isinstance(error, app_commands.MissingPermissions):
        msg = "❌ You lack permission for this."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = "❌ I'm missing required permissions."
    try:
        await interaction.response.send_message(embed=make_embed(
            title="Error", description=msg,
            color=discord.Color.red()), ephemeral=True)
    except discord.InteractionResponded:
        await interaction.followup.send(embed=make_embed(
            title="Error", description=msg,
            color=discord.Color.red()), ephemeral=True)


# ══════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════

if __name__ == "__main__":
    if not TOKEN:
        print("[ERROR] No DISCORD_TOKEN in .env")
    else:
        bot.run(TOKEN)
