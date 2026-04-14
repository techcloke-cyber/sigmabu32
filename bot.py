"""
╔══════════════════════════════════════════════════════════════════╗
║           ADVANCED DISCORD BOT v2 — FULL COMPILATION            ║
║  Ticket Departments · Minecraft Link · Verification · AutoMod   ║
║  + FULL JAVA PLUGIN COMPILATION (Maven + javac)                 ║
╚══════════════════════════════════════════════════════════════════╝
"""

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks
import json, os, asyncio, datetime, re, aiohttp, base64, io, zipfile
import subprocess
import tempfile
import shutil
from pathlib import Path
from dotenv import load_dotenv

# ──────────────────────────────────────────────
#  CONFIG & ENV
# ──────────────────────────────────────────────

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    # Core IDs
    "staff_role_id": 0,
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
    "banned_words": [],
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
    "ai_wake_words": ["jarvis"],
    "ai_wake_response": "Yes sir?",
    "ai_owner_ids": [],
    "ai_max_history": 15,
    "ai_provider": "openrouter",
    "openai_model": "gpt-4o-mini",
    "gemini_model": "gemini-2.0-flash",
    "sambanova_model": "Meta-Llama-3.3-70B-Instruct",
    "openrouter_model": "meta-llama/llama-3.3-70b-instruct",

    # API keys
    "api_key_anthropic": "",
    "api_key_openai": "",
    "api_key_gemini": "",
    "api_key_sambanova": "",
    "api_key_openrouter": "",
    "plugin_output_channel_id": 0,

    # Personality
    "ai_personality": "",
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
    return config.get(f"api_key_{name}", "").strip() or os.getenv(name.upper() + "_API_KEY", "").strip()

# Ticket claim tracking
ticket_claims: dict[int, int] = {}
coding_sessions: list[dict] = []
spam_tracker: dict[int, list[float]] = {}
ai_conversations: dict[int, list[dict]] = {}
compilation_jobs: dict[str, dict] = {}

# Reaction roles storage
rr_panels: dict[str, dict] = {}
rr_messages: dict[int, str] = {}
RR_PANELS_FILE = "rr_panels.json"

# ──────────────────────────────────────────────
#  BOT SETUP
# ──────────────────────────────────────────────

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ──────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────

def make_embed(title="", description="", color=discord.Color.blurple(), footer="", fields: list = None, thumbnail_url="") -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color, timestamp=datetime.datetime.utcnow())
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
    await ch.send(embed=make_embed(title=f"🛡️ {action}", description=desc, color=discord.Color.orange(), footer="Moderation Log"))

# ══════════════════════════════════════════════
#  SECTION 1 — TICKET SYSTEM
# ══════════════════════════════════════════════

DEPARTMENTS = {
    "general":      ("⚙️", "General Assistance", "Technical aid or bugs"),
    "sponsorships": ("💎", "Sponsorships",        "Partner applications"),
}

class DepartmentSelect(ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=label, description=desc, emoji=emoji, value=key) for key, (emoji, label, desc) in DEPARTMENTS.items()]
        super().__init__(placeholder="Select Department...", options=options, custom_id="dept_select")

    async def callback(self, interaction: discord.Interaction):
        dept_key = self.values[0]
        emoji, label, desc = DEPARTMENTS[dept_key]
        await create_ticket_channel(interaction, dept_key, label, emoji)

class TicketPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(DepartmentSelect())

async def create_ticket_channel(interaction: discord.Interaction, dept_key: str, dept_label: str, dept_emoji: str):
    guild = interaction.guild
    user = interaction.user
    staff_role = guild.get_role(config.get("staff_role_id", 0))
    safe_name = user.name.lower().replace(" ", "-")
    existing = discord.utils.get(guild.text_channels, name=f"{dept_key}-{safe_name}")
    if existing:
        await interaction.response.send_message(f"You already have an open ticket: {existing.mention}", ephemeral=True)
        return
    cat_id = config.get("ticket_category_id", 0)
    category = guild.get_channel(cat_id) if cat_id else None
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    try:
        channel = await guild.create_text_channel(name=f"{dept_key}-{safe_name}", category=category, overwrites=overwrites, topic=f"Ticket | {dept_label} | {user} (ID: {user.id})")
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing permissions to create ticket channel.", ephemeral=True)
        return
    e = make_embed(title=f"{dept_emoji} {dept_label} — Support Ticket", description=f"Hello {user.mention}! A staff member will be with you shortly.\n\n**Department:** {dept_emoji} {dept_label}\n**Opened by:** {user.mention}\n\nPlease describe your issue in detail below.", color=discord.Color.blurple(), footer="Use the buttons below to manage this ticket.")
    ping_msg = staff_role.mention if staff_role else "@staff"
    await channel.send(content=f"{ping_msg} — new ticket from {user.mention}", embed=e, view=TicketManageView())
    await interaction.response.send_message(f"✅ Your **{dept_label}** ticket: {channel.mention}", ephemeral=True)
    log_ch = await get_log_channel(guild)
    if log_ch:
        await log_ch.send(embed=make_embed(title="🎫 Ticket Opened", description=f"**User:** {user.mention}\n**Department:** {dept_emoji} {dept_label}\n**Channel:** {channel.mention}", color=discord.Color.green(), footer="Ticket System"))

class TicketManageView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="✋ Claim", style=discord.ButtonStyle.blurple, custom_id="ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: ui.Button):
        if not is_staff(interaction):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        ticket_claims[interaction.channel.id] = interaction.user.id
        button.label = f"✋ Claimed by {interaction.user.display_name}"
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.channel.send(embed=make_embed(title="✋ Ticket Claimed", description=f"This ticket has been claimed by {interaction.user.mention}.", color=discord.Color.blurple()))

    @ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close")
    async def close(self, interaction: discord.Interaction, button: ui.Button):
        if not is_staff(interaction):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await interaction.response.send_message("🔒 Closing ticket in 5 seconds...")
        log_ch = await get_log_channel(interaction.guild)
        if log_ch:
            claimer_id = ticket_claims.get(interaction.channel.id)
            claimer = f"<@{claimer_id}>" if claimer_id else "Unclaimed"
            await log_ch.send(embed=make_embed(title="🎫 Ticket Closed", description=f"**Channel:** `{interaction.channel.name}`\n**Closed by:** {interaction.user.mention}\n**Claimed by:** {claimer}", color=discord.Color.red(), footer="Ticket System"))
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.HTTPException:
            pass

    @ui.button(label="➕ Add User", style=discord.ButtonStyle.secondary, custom_id="ticket_adduser")
    async def add_user(self, interaction: discord.Interaction, button: ui.Button):
        if not is_staff(interaction):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await interaction.response.send_message("Mention the user to add (type their @mention in chat):", ephemeral=True)
        def check(m):
            return (m.author == interaction.user and m.channel == interaction.channel and m.mentions)
        try:
            msg = await bot.wait_for("message", check=check, timeout=30)
            for member in msg.mentions:
                await interaction.channel.set_permissions(member, view_channel=True, send_messages=True)
            await interaction.channel.send(embed=make_embed(title="➕ User Added", description=f"Added {', '.join(m.mention for m in msg.mentions)}.", color=discord.Color.green()))
        except asyncio.TimeoutError:
            pass

# ══════════════════════════════════════════════
#  SECTION 2 — VERIFICATION SYSTEM
# ══════════════════════════════════════════════

class VerifyButton(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="✅ Verify Me", style=discord.ButtonStyle.green, custom_id="verify_button")
    async def verify(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild
        verified_role_id = config.get("verified_role_id", 0)
        if not verified_role_id:
            await interaction.response.send_message("❌ Verification role not configured.", ephemeral=True)
            return
        role = guild.get_role(verified_role_id)
        if not role:
            await interaction.response.send_message("❌ Verified role not found.", ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.response.send_message("✅ You are already verified!", ephemeral=True)
            return
        try:
            await interaction.user.add_roles(role, reason="Verified via button")
            await interaction.response.send_message(embed=make_embed(title="✅ Verified!", description=f"You've been given the **{role.name}** role. Welcome!", color=discord.Color.green()), ephemeral=True)
            log_ch = await get_log_channel(guild)
            if log_ch:
                await log_ch.send(embed=make_embed(title="✅ Member Verified", description=f"{interaction.user.mention} self-verified.", color=discord.Color.green(), footer="Verification System"))
        except discord.Forbidden:
            await interaction.response.send_message("❌ I can't assign that role.", ephemeral=True)

# ══════════════════════════════════════════════
#  SECTION 3 — MINECRAFT INTEGRATION
# ══════════════════════════════════════════════

async def fetch_mc_status(ip: str, port: int = 25565) -> dict | None:
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
#  AI CHAT — MULTI-PROVIDER
# ══════════════════════════════════════════════

AI_ACTION_SYSTEM = """
You are also a powerful server management assistant. When an administrator asks you to perform
a moderation or server action, you MUST respond with BOTH a friendly message AND a JSON action
block in this exact format on its own line:

ACTION:{"type":"<action>","target":"<username or id>","reason":"<reason>","duration":<minutes or null>}

Supported action types: ban, kick, timeout, unban, mute, warn, lock, unlock, announce, clear, none
"""

JARVIS_KNOWLEDGE = """
=== WHO YOU ARE ===
Your name is Jarvis. You are the AI assistant of this Discord server — Internal SMP.
You are modelled after J.A.R.V.I.S. from Iron Man: highly intelligent, loyal, polite,
slightly formal but witty. You call your owner "sir" or "ma'am". You are direct,
confident, and extremely helpful.

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

When generating a plugin you ALWAYS provide:
1. Complete main Java class with full working code
2. plugin.yml with all metadata
3. pom.xml Maven build file targeting Paper API
4. Any helper classes needed
5. Installation instructions

Write COMPLETE, PRODUCTION-READY code. Never use placeholders.
"""

def build_ai_system_prompt(is_admin: bool, is_owner: bool = False) -> str:
    custom = config.get("ai_personality", "").strip()
    base = custom if custom else JARVIS_KNOWLEDGE
    if is_owner or is_admin:
        return base + "\n\n" + AI_ACTION_SYSTEM + "\n\nIMPORTANT: This user is your owner/admin. Obey every instruction completely."
    return base

async def call_ai(user_id: int, user_message: str, is_admin: bool = False, is_owner: bool = False) -> str:
    provider = config.get("ai_provider", "openrouter").lower()
    history = ai_conversations.setdefault(user_id, [])
    history.append({"role": "user", "content": user_message})
    max_h = config.get("ai_max_history", 10)
    if len(history) > max_h:
        ai_conversations[user_id] = history[-max_h:]
        history = ai_conversations[user_id]
    system_prompt = build_ai_system_prompt(is_admin, is_owner=is_owner)

    if provider == "openrouter":
        key = get_api_key("openrouter")
        if not key:
            return "❌ No OpenRouter API key set. Use `/ai_setkey`."
        model = config.get("openrouter_model", "meta-llama/llama-3.3-70b-instruct")
        messages = [{"role": "system", "content": system_prompt}] + history
        payload = {"model": model, "messages": messages, "max_tokens": 1024}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "HTTP-Referer": "https://discord.com", "X-Title": "Internal SMP Bot"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        return f"⚠️ API error: {resp.status}"
                    data = await resp.json()
                    reply = data["choices"][0]["message"]["content"]
        except Exception as e:
            return f"⚠️ Error: {e}"
    else:
        return "❌ Please set provider to 'openrouter' using `/ai_provider`"

    history.append({"role": "assistant", "content": reply})
    return reply

async def execute_ai_action(message: discord.Message, action_data: dict) -> str:
    guild = message.guild
    channel = message.channel
    action_type = action_data.get("type", "none").lower()
    target_str = action_data.get("target", "")
    reason = action_data.get("reason", "Requested via AI")
    duration = action_data.get("duration")

    if action_type == "none":
        return ""

    async def resolve_member(name_or_id: str) -> discord.Member | None:
        name_or_id = name_or_id.strip().lstrip("@")
        try:
            return guild.get_member(int(name_or_id))
        except ValueError:
            pass
        name_lower = name_or_id.lower()
        return discord.utils.find(lambda m: m.name.lower() == name_lower or (m.nick and m.nick.lower() == name_lower), guild.members)

    try:
        if action_type == "ban":
            member = await resolve_member(target_str)
            if not member:
                return f"\n\n⚠️ Could not find user `{target_str}`."
            await member.ban(reason=f"[AI Action] {reason}")
            return f"\n\n✅ Banned **{member}**."
        elif action_type == "kick":
            member = await resolve_member(target_str)
            if not member:
                return f"\n\n⚠️ Could not find user `{target_str}`."
            await member.kick(reason=f"[AI Action] {reason}")
            return f"\n\n✅ Kicked **{member}**."
        elif action_type in ("timeout", "mute"):
            member = await resolve_member(target_str)
            if not member:
                return f"\n\n⚠️ Could not find user `{target_str}`."
            mins = int(duration) if duration else 10
            await member.timeout(datetime.timedelta(minutes=mins), reason=f"[AI Action] {reason}")
            return f"\n\n✅ Timed out **{member}** for **{mins} minutes**."
        elif action_type == "lock":
            ow = channel.overwrites_for(guild.default_role)
            ow.send_messages = False
            await channel.set_permissions(guild.default_role, overwrite=ow)
            return f"\n\n✅ Locked {channel.mention}."
        elif action_type == "unlock":
            ow = channel.overwrites_for(guild.default_role)
            ow.send_messages = True
            await channel.set_permissions(guild.default_role, overwrite=ow)
            return f"\n\n✅ Unlocked {channel.mention}."
        elif action_type == "clear":
            count = int(duration) if duration else 5
            count = max(1, min(100, count))
            deleted = await channel.purge(limit=count)
            return f"\n\n✅ Deleted {len(deleted)} messages."
    except Exception as e:
        return f"\n\n❌ Action failed: {e}"
    return ""

def user_has_ai_access(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    ai_role_ids = config.get("ai_role_ids", [])
    if not ai_role_ids:
        return True
    return any(r.id in ai_role_ids for r in member.roles)

async def handle_ai_message(message: discord.Message):
    if not config.get("ai_enabled", True):
        return
    if message.author.bot or not message.guild:
        return
    if not user_has_ai_access(message.author):
        return
    content_lower = message.content.lower()
    ai_channel_ids = config.get("ai_channel_ids", [])
    wake_words = [w.lower() for w in config.get("ai_wake_words", ["jarvis"])]
    wake_response = config.get("ai_wake_response", "Yes sir?")
    owner_ids = config.get("ai_owner_ids", [])
    bot_mentioned = bot.user in message.mentions
    in_ai_channel = message.channel.id in ai_channel_ids
    wake_triggered = any(w in content_lower for w in wake_words)
    if not bot_mentioned and not in_ai_channel and not wake_triggered:
        return
    stripped = content_lower
    for w in wake_words:
        stripped = stripped.replace(w, "").strip()
    if wake_triggered and len(stripped.strip()) < 3 and not bot_mentioned:
        await message.reply(wake_response, mention_author=False)
        return
    clean = re.sub(r"<@!?" + str(bot.user.id) + r">", "", message.content).strip()
    if not clean:
        clean = "Hello!"
    is_owner = message.author.id in owner_ids
    is_admin = message.author.guild_permissions.administrator or is_owner
    async with message.channel.typing():
        raw_reply = await call_ai(message.author.id, clean, is_admin=is_admin, is_owner=is_owner)
    action_status = ""
    display_reply = raw_reply
    if is_admin and "ACTION:" in raw_reply:
        lines = raw_reply.split("\n")
        text_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("ACTION:"):
                json_str = stripped[len("ACTION:"):].strip()
                try:
                    action_data = json.loads(json_str)
                    action_status = await execute_ai_action(message, action_data)
                except:
                    pass
            else:
                text_lines.append(line)
        display_reply = "\n".join(text_lines).strip()
    final = (display_reply + action_status).strip()
    if not final:
        final = "✅ Done."
    chunks = [final[i:i+1990] for i in range(0, len(final), 1990)]
    first = True
    for chunk in chunks:
        if first:
            await message.reply(chunk, mention_author=False)
            first = False
        else:
            await message.channel.send(chunk)

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
            await log_mod(guild, "🚫 Banned Word", author, f"Message contained: `{word}`")
            return
    if config.get("anti_link", True):
        if not (author.guild_permissions.administrator or any(r.id == config.get("staff_role_id", 0) for r in author.roles)):
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
        online = sum(1 for m in guild.members if m.status != discord.Status.offline and not m.bot)
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
    msg = config.get("welcome_message", DEFAULT_CONFIG["welcome_message"]).replace("{user}", member.mention).replace("{server}", member.guild.name)
    e = make_embed(title="👋 Welcome!", description=msg, color=discord.Color.green(), fields=[("Member #", str(member.guild.member_count), True), ("Account Created", member.created_at.strftime("%Y-%m-%d"), True)], thumbnail_url=str(member.display_avatar.url))
    try:
        sent = await channel.send(embed=e)
        await sent.add_reaction(config.get("welcome_emoji", "👋"))
    except (discord.Forbidden, discord.HTTPException):
        pass

# ══════════════════════════════════════════════
#  SECTION 7 — PLUGIN GENERATION & COMPILATION
# ══════════════════════════════════════════════

async def generate_plugin_code(description, plugin_name, user_id):
    prompt = f"""Generate a complete Minecraft Bukkit/Spigot/Paper plugin.

Plugin Name: {plugin_name}
Requirements: {description}

Provide ALL of the following clearly labelled:
1. Main Java class — complete working code
2. plugin.yml — all metadata, commands, permissions
3. pom.xml — Maven build targeting Paper 1.21
4. Any additional Java classes
5. Install instructions

Write COMPLETE production-ready code. No placeholders."""
    response = await call_ai(user_id, prompt, is_admin=True, is_owner=True)
    coding_sessions.append({"type": "plugin", "name": plugin_name, "description": description[:100], "timestamp": datetime.datetime.utcnow().isoformat()})
    return response

async def create_plugin_zip(plugin_name, code_response):
    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            java_blocks = re.findall(r"```(?:java)?\n(.*?)```", code_response, re.DOTALL)
            yml_blocks = re.findall(r"```(?:yaml|yml)?\n(.*?)```", code_response, re.DOTALL)
            xml_blocks = re.findall(r"```(?:xml)?\n(.*?)```", code_response, re.DOTALL)
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
            zf.writestr("README.md", f"# {plugin_name}\nGenerated by Jarvis\n\n## Build\n```\nmvn clean package\n```")
        buf.seek(0)
        return buf.read()
    except Exception as e:
        print(f"[ZIP] Error: {e}")
        return None

# ══════════════════════════════════════════════
#  SECTION 8 — COMPILATION SYSTEM
# ══════════════════════════════════════════════

async def compile_plugin_with_maven(plugin_zip_bytes: bytes, plugin_name: str) -> tuple[bool, str, bytes | None]:
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix=f"jarvis_compile_{plugin_name}_")
        zip_path = Path(temp_dir) / f"{plugin_name}.zip"
        extract_dir = Path(temp_dir) / "source"
        with open(zip_path, "wb") as f:
            f.write(plugin_zip_bytes)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(extract_dir)
        pom_path = None
        for root, dirs, files in os.walk(extract_dir):
            if "pom.xml" in files:
                pom_path = Path(root) / "pom.xml"
                break
        if not pom_path:
            return False, "❌ No pom.xml found in the plugin zip.", None
        maven_check = await asyncio.create_subprocess_exec("mvn", "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await maven_check.communicate()
        if maven_check.returncode != 0:
            return False, "❌ Maven is not installed on the server.", None
        compile_dir = pom_path.parent
        process = await asyncio.create_subprocess_exec("mvn", "clean", "package", "-DskipTests", cwd=str(compile_dir), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        output = stdout.decode('utf-8', errors='replace')
        if process.returncode != 0:
            error_lines = [l for l in output.split('\n') if '[ERROR]' in l][:15]
            error_summary = '\n'.join(error_lines) if error_lines else output[:800]
            return False, f"❌ Compilation failed:\n```\n{error_summary}\n```", None
        target_dir = compile_dir / "target"
        jar_files = [f for f in target_dir.glob("*.jar") if "-sources" not in f.name and "-original" not in f.name]
        if not jar_files:
            return False, "❌ Compilation succeeded but no JAR file was produced.", None
        with open(jar_files[0], "rb") as f:
            jar_bytes = f.read()
        return True, f"✅ Successfully compiled `{jar_files[0].name}`", jar_bytes
    except Exception as e:
        return False, f"❌ Compilation error: {str(e)}", None
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

async def compile_with_javac_direct(java_code: str, plugin_name: str) -> tuple[bool, str, bytes | None]:
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix=f"jarvis_javac_{plugin_name}_")
        src_dir = Path(temp_dir) / "src" / "main" / "java"
        src_dir.mkdir(parents=True)
        class_match = re.search(r'public class\s+(\w+)', java_code)
        if not class_match:
            return False, "❌ Could not find class name in Java code.", None
        class_name = class_match.group(1)
        java_file = src_dir / f"{class_name}.java"
        with open(java_file, "w") as f:
            f.write(java_code)
        paper_api_jar = Path(temp_dir) / "paper-api.jar"
        if not paper_api_jar.exists():
            paper_version = "1.21.1"
            api_url = f"https://repo.papermc.io/repository/maven-public/io/papermc/paper/paper-api/{paper_version}-R0.1-SNAPSHOT/paper-api-{paper_version}-R0.1-SNAPSHOT.jar"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            with open(paper_api_jar, "wb") as f:
                                f.write(await resp.read())
                        else:
                            return False, f"❌ Could not download Paper API (HTTP {resp.status})", None
            except Exception as e:
                return False, f"❌ Failed to download Paper API: {e}", None
        process = await asyncio.create_subprocess_exec("javac", "-cp", str(paper_api_jar), "-d", str(src_dir.parent.parent), str(java_file), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            error_msg = stderr.decode('utf-8', errors='replace')
            return False, f"❌ javac compilation failed:\n```\n{error_msg[:1000]}\n```", None
        classes_dir = Path(temp_dir) / "classes"
        classes_dir.mkdir(exist_ok=True)
        compiled_dir = src_dir.parent.parent
        for class_file in compiled_dir.rglob("*.class"):
            dest = classes_dir / class_file.relative_to(compiled_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(class_file, dest)
        jar_path = Path(temp_dir) / f"{plugin_name}.jar"
        with zipfile.ZipFile(jar_path, 'w') as zf:
            for class_file in classes_dir.rglob("*"):
                if class_file.is_file():
                    arcname = class_file.relative_to(classes_dir)
                    zf.write(class_file, arcname)
        with open(jar_path, "rb") as f:
            jar_bytes = f.read()
        return True, f"✅ Compiled {class_name}.java into {plugin_name}.jar", jar_bytes
    except Exception as e:
        return False, f"❌ Compilation error: {str(e)}", None
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

# ══════════════════════════════════════════════
#  SECTION 9 — SLASH COMMANDS
# ══════════════════════════════════════════════

# ── /panel ─────────────────────────────────────
@tree.command(name="panel", description="Post the ticket panel with department dropdown. (Admin)")
async def panel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    e = make_embed(title="📋 Internal Support & Partnerships", description="Welcome to the official internal portal. Use the menu below to open a ticket in the correct department.\n\n**⚙️ General Support**\n*Bug reports, technical issues, or player assistance.*\n\n**💎 Sponsorships**\n*Creator applications and brand collaborations.*\n\n**Response Time**\nOur administration team usually responds within 12–24 hours.", color=discord.Color.blurple(), footer="Internal SMP • Help Desk")
    if interaction.guild.icon:
        e.set_thumbnail(url=interaction.guild.icon.url)
    await interaction.channel.send(embed=e, view=TicketPanelView())
    await interaction.response.send_message("✅ Ticket panel sent!", ephemeral=True)

# ── /setup_verification ────────────────────────
@tree.command(name="setup_verification", description="Post the verification panel. (Admin)")
@app_commands.describe(channel="Channel to post it in.", verified_role="Role to give verified members.")
async def setup_verification(interaction: discord.Interaction, channel: discord.TextChannel, verified_role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    config["verification_channel_id"] = channel.id
    config["verified_role_id"] = verified_role.id
    save_config(config)
    e = make_embed(title="✅ Verification", description=f"Click the button below to verify yourself and gain access.\n\nYou will receive the **{verified_role.name}** role.", color=discord.Color.green(), footer="Click the button to verify.")
    await channel.send(embed=e, view=VerifyButton())
    await interaction.response.send_message(f"✅ Verification panel posted in {channel.mention}!", ephemeral=True)

# ── /setup_minecraft ───────────────────────────
@tree.command(name="setup_minecraft", description="Configure the Minecraft integration. (Admin)")
@app_commands.describe(server_ip="Your Minecraft server IP", port="Server port (default 25565)", status_channel="Voice channel to show live player count", events_channel="Text channel for MC event announcements")
async def setup_minecraft(interaction: discord.Interaction, server_ip: str, port: int = 25565, status_channel: discord.VoiceChannel = None, events_channel: discord.TextChannel = None):
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
    await interaction.response.send_message(embed=make_embed(title="⛏️ Minecraft Integration Configured", description=f"**Server IP:** `{server_ip}:{port}`\n**Status Channel:** {status_channel.mention if status_channel else 'Not set'}\n**Events Channel:** {events_channel.mention if events_channel else 'Not set'}\n\nPlayer count updates every 5 minutes.", color=discord.Color.green()), ephemeral=True)

# ── /mc_status ─────────────────────────────────
@tree.command(name="mc_status", description="Check the Minecraft server status.")
async def mc_status(interaction: discord.Interaction):
    ip = config.get("minecraft_server_ip", "")
    if not ip:
        await interaction.response.send_message("❌ Not configured. Use `/setup_minecraft`.", ephemeral=True)
        return
    await interaction.response.defer()
    port = config.get("minecraft_server_port", 25565)
    data = await fetch_mc_status(ip, port)
    if not data or not data.get("online"):
        await interaction.followup.send(embed=make_embed(title="⛏️ Minecraft Server", description=f"`{ip}:{port}` is **offline** or unreachable.", color=discord.Color.red()))
        return
    players = data.get("players", {})
    online = players.get("online", 0)
    maximum = players.get("max", 0)
    player_list = players.get("list", [])
    version = data.get("version", "Unknown")
    motd_clean = " ".join(data.get("motd", {}).get("clean", ["No MOTD"]))
    fields = [("🟢 Status", "Online", True), ("👥 Players", f"{online}/{maximum}", True), ("🔖 Version", version, True), ("📝 MOTD", motd_clean, False)]
    if player_list:
        fields.append(("Online Players", ", ".join(player_list[:20]) or "Hidden", False))
    await interaction.followup.send(embed=make_embed(title=f"⛏️ {ip}", color=discord.Color.green(), fields=fields))

# ── /plugin (ENHANCED WITH COMPILATION) ────────
@tree.command(name="plugin", description="Ask Jarvis to create AND compile a Minecraft plugin for you.")
@app_commands.describe(name="Plugin name e.g. SuperKits", description="What should the plugin do?", compile_now="Compile the plugin immediately after generation?")
async def plugin_cmd(interaction: discord.Interaction, name: str, description: str, compile_now: bool = True):
    await interaction.response.defer()
    await interaction.followup.send(embed=make_embed(title=f"⚙️ Generating: {name}", description=f"Jarvis is writing your plugin...\n\n**Requirements:** {description[:300]}", color=discord.Color.blurple(), footer="This takes 15-30 seconds."))
    code = await generate_plugin_code(description, name, interaction.user.id)
    zip_bytes = await create_plugin_zip(name, code)
    if not zip_bytes:
        await interaction.followup.send("❌ Failed to create plugin package.")
        return
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if compile_now:
        compile_status_msg = await interaction.followup.send(embed=make_embed(title="🔨 Compiling Plugin", description=f"Attempting to compile `{name}`...", color=discord.Color.blurple()))
        success, compile_msg, jar_bytes = await compile_plugin_with_maven(zip_bytes, name)
        if success and jar_bytes:
            await compile_status_msg.edit(embed=make_embed(title="✅ Plugin Compiled!", description=f"**{name}.jar** is ready to use.\n\n{compile_msg[:500]}", color=discord.Color.green()))
            await interaction.followup.send(content="📦 **Compiled JAR (ready to use):**", file=discord.File(io.BytesIO(jar_bytes), filename=f"{safe_name}.jar"))
        else:
            await compile_status_msg.edit(embed=make_embed(title="⚠️ Plugin Generated But Not Compiled", description=f"Compilation failed, but the source code is available below.\n\n{compile_msg[:800]}", color=discord.Color.orange()))
    await interaction.followup.send(content="📦 **Source code (ZIP with Maven structure):**", file=discord.File(io.BytesIO(zip_bytes), filename=f"{safe_name}_source.zip"))
    await interaction.followup.send(f"```java\n{code[:1500]}\n```")

# ── /compile ────────────────────────────────────
@tree.command(name="compile", description="Compile a Minecraft plugin from code or ZIP file.")
@app_commands.describe(plugin_zip="Upload the plugin ZIP file (from /plugin command)", plugin_name="Name for the compiled JAR", code="Or paste Java code directly to compile")
async def compile_plugin_cmd(interaction: discord.Interaction, plugin_zip: discord.Attachment = None, plugin_name: str = "", code: str = ""):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    await interaction.response.defer()
    if code and not plugin_zip:
        code_clean = code
        java_match = re.search(r'```(?:java)?\n(.*?)```', code, re.DOTALL)
        if java_match:
            code_clean = java_match.group(1)
        name = plugin_name or "Plugin"
        success, msg, jar_bytes = await compile_with_javac_direct(code_clean, name)
        if success and jar_bytes:
            await interaction.followup.send(embed=make_embed(title="✅ Plugin Compiled!", description=msg[:1500], color=discord.Color.green()), file=discord.File(io.BytesIO(jar_bytes), filename=f"{name}.jar"))
        else:
            await interaction.followup.send(embed=make_embed(title="❌ Compilation Failed", description=msg, color=discord.Color.red()))
        return
    if not plugin_zip:
        await interaction.followup.send("Please either upload a plugin ZIP file or paste Java code.")
        return
    try:
        zip_bytes = await plugin_zip.read()
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to download file: {e}")
        return
    final_name = plugin_name or Path(plugin_zip.filename).stem.replace("_plugin", "")
    status_msg = await interaction.followup.send(embed=make_embed(title="🔨 Compiling Plugin", description=f"Compiling `{final_name}`...\nThis may take 30-60 seconds.", color=discord.Color.blurple()))
    success, msg, jar_bytes = await compile_plugin_with_maven(zip_bytes, final_name)
    if success and jar_bytes:
        await status_msg.edit(embed=make_embed(title="✅ Plugin Compiled Successfully!", description=f"**{final_name}.jar** is ready to use.\n\n{msg[:800]}", color=discord.Color.green(), footer="Place the JAR in your server's /plugins/ folder."))
        await interaction.followup.send(content="📦 **Compiled Plugin:**", file=discord.File(io.BytesIO(jar_bytes), filename=f"{final_name}.jar"))
    else:
        await status_msg.edit(embed=make_embed(title="❌ Compilation Failed", description=f"Could not compile `{final_name}`.\n\n{msg[:1500]}", color=discord.Color.red()))

# ── /compile_status ─────────────────────────────
@tree.command(name="compile_status", description="Check if Java/Maven are available for compilation.")
async def compile_status_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    results = []
    try:
        proc = await asyncio.create_subprocess_exec("java", "-version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await proc.communicate()
        java_version = stderr.decode('utf-8', errors='replace').split('\n')[0]
        results.append(f"✅ **Java:** {java_version}")
    except FileNotFoundError:
        results.append("❌ **Java:** Not found — please install JDK 17+")
    try:
        proc = await asyncio.create_subprocess_exec("javac", "-version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        javac_version = stdout.decode('utf-8', errors='replace').strip()
        results.append(f"✅ **javac:** {javac_version}")
    except FileNotFoundError:
        results.append("❌ **javac:** Not found — install JDK")
    try:
        proc = await asyncio.create_subprocess_exec("mvn", "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        lines = stdout.decode('utf-8', errors='replace').split('\n')
        maven_line = next((l for l in lines if "Apache Maven" in l), "Maven found")
        results.append(f"✅ **Maven:** {maven_line[:60]}")
    except FileNotFoundError:
        results.append("❌ **Maven:** Not found — install Maven")
    can_compile = all("✅" in r for r in results)
    status_color = discord.Color.green() if can_compile else discord.Color.red()
    await interaction.followup.send(embed=make_embed(title="🔧 Compilation Environment", description="\n".join(results), color=status_color, footer="Full compilation requires Java JDK 17+ and Maven."), ephemeral=True)

# ── /fix_and_compile ────────────────────────────
@tree.command(name="fix_and_compile", description="Upload broken plugin code — Jarvis fixes AND compiles it!")
@app_commands.describe(description="What the plugin should do", code="Your broken Java code", error="The error you're seeing (optional)")
async def fix_and_compile(interaction: discord.Interaction, description: str, code: str, error: str = ""):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    await interaction.response.defer()
    error_context = f"\n\nError message: {error}" if error else ""
    prompt = f"""Fix this Minecraft plugin code. Requirements: {description}{error_context}

Return ONLY the complete fixed Java code in a ```java code block.

Original code:
```java
{code}
```"""
    is_owner = interaction.user.id in config.get("ai_owner_ids", [])
    fixed_code = await call_ai(interaction.user.id, prompt, is_admin=True, is_owner=is_owner)
    code_match = re.search(r'```(?:java)?\n(.*?)```', fixed_code, re.DOTALL)
    if not code_match:
        await interaction.followup.send("❌ Jarvis couldn't generate fixed code. Please try again.")
        return
    cleaned_code = code_match.group(1)
    class_match = re.search(r'public class\s+(\w+)', cleaned_code)
    plugin_name = class_match.group(1) if class_match else "FixedPlugin"
    success, msg, jar_bytes = await compile_with_javac_direct(cleaned_code, plugin_name)
    if success and jar_bytes:
        await interaction.followup.send(embed=make_embed(title="✅ Fixed & Compiled!", description=f"**{plugin_name}.jar** is ready.\n\n{msg}", color=discord.Color.green()), file=discord.File(io.BytesIO(jar_bytes), filename=f"{plugin_name}.jar"))
        await interaction.followup.send(f"```java\n{cleaned_code[:1500]}\n```")
    else:
        await interaction.followup.send(embed=make_embed(title="⚠️ Code Fixed But Compilation Failed", description=f"Jarvis fixed the code but compilation had errors:\n\n{msg[:1000]}\n\n**Fixed Code:**", color=discord.Color.orange()))
        await interaction.followup.send(f"```java\n{cleaned_code[:1500]}\n```")

# ── /ai_setup ──────────────────────────────────
@tree.command(name="ai_setup", description="Configure the AI assistant. (Admin)")
@app_commands.describe(enable="Enable or disable the AI.", ai_channel="Restrict AI to this channel", ai_role="Restrict AI to users with this role", personality="Custom personality", ai_name="Display name for the AI.")
async def ai_setup(interaction: discord.Interaction, enable: bool = None, ai_channel: discord.TextChannel = None, ai_role: discord.Role = None, personality: str = None, ai_name: str = None):
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
    await interaction.response.send_message(embed=make_embed(title="🤖 AI Setup", description="\n".join(changed) if changed else "No changes made.", color=discord.Color.blurple()), ephemeral=True)

# ── /ai_provider ───────────────────────────────
@tree.command(name="ai_provider", description="Switch AI provider. (Admin)")
@app_commands.choices(provider=[app_commands.Choice(name="OpenRouter (recommended)", value="openrouter")])
async def ai_provider(interaction: discord.Interaction, provider: app_commands.Choice[str]):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    config["ai_provider"] = provider.value
    save_config(config)
    await interaction.response.send_message(embed=make_embed(title="🤖 AI Provider Updated", description=f"Switched to **{provider.name}**", color=discord.Color.blurple()), ephemeral=True)

# ── /ai_setkey ─────────────────────────────────
@tree.command(name="ai_setkey", description="Set an AI provider API key. (Admin)")
@app_commands.choices(provider=[app_commands.Choice(name="OpenRouter", value="openrouter")])
async def ai_setkey(interaction: discord.Interaction, provider: app_commands.Choice[str], key: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    config["api_key_openrouter"] = key.strip()
    save_config(config)
    await interaction.response.send_message(embed=make_embed(title="✅ API Key Saved", description=f"Your **{provider.name}** API key has been saved.\nKey preview: `{key[:6]}{'*' * 20}`", color=discord.Color.green()), ephemeral=True)

# ── /ai_status ─────────────────────────────────
@tree.command(name="ai_status", description="Show current AI configuration.")
async def ai_status(interaction: discord.Interaction):
    provider = config.get("ai_provider", "openrouter")
    enabled = config.get("ai_enabled", False)
    name = config.get("ai_name", "Jarvis")
    key_status = "✅ Set" if get_api_key("openrouter") else "❌ Not set"
    await interaction.response.send_message(embed=make_embed(title=f"🤖 AI Status — {name}", color=discord.Color.green() if enabled else discord.Color.red(), fields=[("Power", "✅ Enabled" if enabled else "❌ Disabled", True), ("Provider", provider.upper(), True), ("API Key", key_status, True)]), ephemeral=True)

# ── /ai_reset ──────────────────────────────────
@tree.command(name="ai_reset", description="Clear your AI conversation history.")
async def ai_reset(interaction: discord.Interaction):
    ai_conversations.pop(interaction.user.id, None)
    await interaction.response.send_message("🧹 Your AI conversation history has been cleared.", ephemeral=True)

# ── /ask ───────────────────────────────────────
@tree.command(name="ask", description="Ask the AI a question.")
@app_commands.describe(question="Your question for the AI.")
async def ask(interaction: discord.Interaction, question: str):
    if not config.get("ai_enabled", False):
        await interaction.response.send_message("❌ AI is not enabled.", ephemeral=True)
        return
    await interaction.response.defer()
    is_owner = interaction.user.id in config.get("ai_owner_ids", [])
    is_admin = interaction.user.guild_permissions.administrator or is_owner
    reply_raw = await call_ai(interaction.user.id, question, is_admin=is_admin, is_owner=is_owner)
    display = "\n".join(l for l in reply_raw.split("\n") if not l.strip().startswith("ACTION:")).strip()
    name = config.get("ai_name", "Jarvis")
    await interaction.followup.send(embed=make_embed(title=f"🤖 {name}", description=display or "✅ Done.", color=discord.Color.blurple()))

# ── /ping ──────────────────────────────────────
@tree.command(name="ping", description="Check bot latency.")
async def ping(interaction: discord.Interaction):
    ms = round(bot.latency * 1000)
    color = discord.Color.green() if ms < 100 else discord.Color.orange() if ms < 200 else discord.Color.red()
    await interaction.response.send_message(embed=make_embed(title="🏓 Pong!", description=f"Websocket latency: **{ms}ms**", color=color))

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

# ── /lock / /unlock ────────────────────────────
@tree.command(name="lock", description="Lock a channel. (Staff)")
async def lock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    t = channel or interaction.channel
    ow = t.overwrites_for(interaction.guild.default_role)
    ow.send_messages = False
    await t.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.response.send_message(embed=make_embed(title="🔒 Locked", description=f"{t.mention} is now locked.", color=discord.Color.red()))

@tree.command(name="unlock", description="Unlock a channel. (Staff)")
async def unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    t = channel or interaction.channel
    ow = t.overwrites_for(interaction.guild.default_role)
    ow.send_messages = True
    await t.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.response.send_message(embed=make_embed(title="🔓 Unlocked", description=f"{t.mention} is now unlocked.", color=discord.Color.green()))

# ══════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"[BOT] {bot.user} ready.")
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
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Minecraft Plugins 🔨"), status=discord.Status.online)

@bot.event
async def on_message(message: discord.Message):
    await handle_automod(message)
    await handle_ai_message(message)
    await bot.process_commands(message)

@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    msg = "An unexpected error occurred."
    if isinstance(error, app_commands.MissingPermissions):
        msg = "❌ You lack permission for this."
    try:
        await interaction.response.send_message(embed=make_embed(title="Error", description=msg, color=discord.Color.red()), ephemeral=True)
    except discord.InteractionResponded:
        await interaction.followup.send(embed=make_embed(title="Error", description=msg, color=discord.Color.red()), ephemeral=True)

# ══════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════

if __name__ == "__main__":
    if not TOKEN:
        print("[ERROR] No DISCORD_TOKEN in .env")
    else:
        bot.run(TOKEN)
