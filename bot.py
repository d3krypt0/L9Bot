import discord
from discord.ext import commands
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
import pytz
import re

# ==============================
# Config & Setup
# ==============================

TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Multi-channel support: ENV var "DISCORD_CHANNEL_IDS" = comma-separated IDs
CHANNEL_IDS = os.getenv("DISCORD_CHANNEL_IDS", "")
CHANNEL_IDS = [int(cid.strip()) for cid in CHANNEL_IDS.split(",") if cid.strip().isdigit()]

# Active channels (configured + auto-discovered)
active_channels = set(CHANNEL_IDS)

# Philippines timezone
ph_tz = pytz.timezone("Asia/Manila")

# Load bosses.json
with open("bosses.json", "r", encoding="utf-8") as f:
    BOSSES = json.load(f)

# Respawn schedule memory
respawn_schedule = {}
DATA_FILE = "respawns.json"

# Default pre-alert (minutes before respawn)
PRE_ALERT_MINUTES = 10

# ==============================
# Helpers
# ==============================

def save_respawn_data():
    """Save current respawn schedule to file."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({b: t.isoformat() for b, t in respawn_schedule.items()}, f)

def load_respawn_data():
    """Load respawn schedule from file (if exists)."""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            for b, t in raw.items():
                respawn_schedule[b] = datetime.fromisoformat(t)

def format_countdown(respawn_time):
    """Return countdown string like '2h 15m' or '⏳ Any moment now!'."""
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    diff = (respawn_time - now).total_seconds()
    if diff <= 0:
        return "⏳ Any moment now!"
    mins = int(diff // 60)
    hours, mins = divmod(mins, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days: parts.append(f"{days}d")
    if hours: parts.append(f"{hours}h")
    if mins: parts.append(f"{mins}m")
    return " ".join(parts)

def find_boss(name: str):
    """Case-insensitive + partial boss matching."""
    name = name.lower()
    matches = [b for b in BOSSES if name in b.lower()]
    if len(matches) == 1:
        return matches[0]
    return None

def parse_time(time_str, base_date=None):
    """Parse PH time string into datetime (UTC-aware)."""
    if not base_date:
        base_date = datetime.now(ph_tz).date()
    try:
        match = re.match(r"(\d{1,2}):(\d{2})(?:\s?(am|pm))?", time_str.lower())
        if not match:
            return None
        hour, minute, ampm = match.groups()
        hour, minute = int(hour), int(minute)
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        dt = datetime.combine(base_date, datetime.min.time()) \
             .replace(hour=hour, minute=minute, tzinfo=ph_tz)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

# ==============================
# Bot Setup
# ==============================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ==============================
# Events
# ==============================

@bot.event
async def on_ready():
    global active_channels
    print(f"✅ Logged in as {bot.user}")

    # Auto-discover channels in each server if none are configured
    if not CHANNEL_IDS:
        for guild in bot.guilds:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    active_channels.add(channel.id)
                    print(f"📡 Added channel {channel.name} ({channel.id}) from {guild.name}")
                    break

    # Resume scheduled bosses still pending
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    for boss, respawn_time in respawn_schedule.items():
        if respawn_time > now:
            schedule_boss(boss, respawn_time)

# ==============================
# Announcer
# ==============================

async def announce_boss(boss, respawn_time):
    """
    Announce boss respawns across all active channels.
    - Sends one 10-minute warning
    - Sends one respawn alert
    """
    # Pre-alert
    alert_time = respawn_time - timedelta(minutes=PRE_ALERT_MINUTES)
    wait_seconds = (alert_time - datetime.utcnow().replace(tzinfo=timezone.utc)).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
        for cid in active_channels:
            channel = bot.get_channel(cid)
            if channel:
                await channel.send(f"⏳ @everyone **{boss.capitalize()} will respawn in {PRE_ALERT_MINUTES} minutes!** Get ready!")

    # Final alert
    wait_seconds = (respawn_time - datetime.utcnow().replace(tzinfo=timezone.utc)).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
    for cid in active_channels:
        channel = bot.get_channel(cid)
        if channel:
            await channel.send(f"⚔️ @everyone **{boss.capitalize()} has respawned!** Go hunt!")

    # Cleanup
    if boss in respawn_schedule:
        del respawn_schedule[boss]
        save_respawn_data()

def schedule_boss(boss, respawn_time):
    """Schedule announcement for a boss respawn."""
    asyncio.create_task(announce_boss(boss, respawn_time))

# ==============================
# Commands
# ==============================

@bot.command(name="dead")
async def dead(ctx, boss_name: str, time_str: str = None):
    """Mark a boss dead at a given time (default: now)."""
    boss = find_boss(boss_name)
    if not boss:
        await ctx.send(f"❌ Unknown boss: {boss_name}")
        return

    killed_time = parse_time(time_str) if time_str else datetime.now(ph_tz).astimezone(timezone.utc)
    if not killed_time:
        await ctx.send("❌ Invalid time format. Use `HH:MM` or `HH:MM AM/PM`.")
        return

    interval = BOSSES[boss]["interval"]
    if not interval:
        await ctx.send(f"🗓️ {boss.capitalize()} has a fixed respawn schedule.")
        return

    respawn_time = killed_time + timedelta(hours=interval)
    respawn_schedule[boss] = respawn_time
    save_respawn_data()
    schedule_boss(boss, respawn_time)

    ph_time = respawn_time.astimezone(ph_tz).strftime("%I:%M %p").lstrip("0").lower()
    await ctx.send(f"✅ {boss.capitalize()} respawn set → {ph_time} PH")

@bot.command(name="deadat")
async def deadat(ctx, *, bulk: str):
    """Log multiple boss deaths at given times."""
    lines = bulk.strip().splitlines()
    results = []
    base_date = datetime.now(ph_tz).date()

    for line in lines:
        if "-" not in line:
            continue
        time_str, boss_name = [s.strip() for s in line.split("-", 1)]
        boss = find_boss(boss_name)
        if not boss:
            results.append(f"❌ Unknown boss: {boss_name}")
            continue

        killed_time = parse_time(time_str, base_date)
        if not killed_time:
            results.append(f"❌ Invalid time: {time_str}")
            continue

        interval = BOSSES[boss]["interval"]
        if not interval:
            results.append(f"🗓️ {boss.capitalize()} has a fixed respawn schedule.")
            continue

        respawn_time = killed_time + timedelta(hours=interval)
        respawn_schedule[boss] = respawn_time
        save_respawn_data()
        schedule_boss(boss, respawn_time)
        ph_time = respawn_time.astimezone(ph_tz).strftime("%I:%M %p").lstrip("0").lower()
        results.append(f"✅ {boss.capitalize()} set → {ph_time} PH")

    await ctx.send("\n".join(results))

@bot.command(name="up")
async def up(ctx, *, bulk: str):
    """
    Bulk set future respawn times.
    Format (must include date line):
    !up
    September 22
    1:15 am - Gareth
    2:24 am - Ego
    """
    lines = bulk.strip().splitlines()
    results = []
    base_date = None

    # First line must be date
    if lines:
        try:
            base_date = datetime.strptime(lines[0], "%B %d").date()
            lines = lines[1:]
        except ValueError:
            await ctx.send("❌ First line must be a date like 'September 22'")
            return

    for line in lines:
        if "-" not in line:
            continue
        time_str, boss_name = [s.strip() for s in line.split("-", 1)]
        boss = find_boss(boss_name)
        if not boss:
            results.append(f"❌ Unknown boss: {boss_name}")
            continue

        respawn_time = parse_time(time_str, base_date)
        if not respawn_time:
            results.append(f"❌ Invalid time: {time_str}")
            continue

        respawn_schedule[boss] = respawn_time
        save_respawn_data()
        schedule_boss(boss, respawn_time)
        ph_time = respawn_time.astimezone(ph_tz).strftime("%I:%M %p").lstrip("0").lower()
        results.append(f"✅ {boss.capitalize()} set → {ph_time} PH")

    await ctx.send("\n".join(results))

@bot.command(name="boss")
async def boss_list(ctx, option: str = None):
    """List respawn timers (soon or all)."""
    with_timers = []
    without_timers = []
    now = datetime.utcnow().replace(tzinfo=timezone.utc)

    for boss, data in BOSSES.items():
        if boss in respawn_schedule:
            respawn_time = respawn_schedule[boss]
            if respawn_time > now:
                with_timers.append((boss, respawn_time))
            else:
                del respawn_schedule[boss]
                save_respawn_data()
        else:
            without_timers.append((boss, data))

    with_timers.sort(key=lambda x: x[1])
    if option and option.lower() == "soon":
        with_timers = with_timers[:5]

    today_str = datetime.now(ph_tz).strftime("%B %d (%A)")
    lines = [f"**⚔️ Boss Respawn Timers — {today_str}**\n"]

    for boss, respawn_time in with_timers:
        ph_time = respawn_time.astimezone(ph_tz)
        countdown = format_countdown(respawn_time)
        lines.append(f"**{ph_time.strftime('%I:%M %p').lstrip('0').lower()}** — {boss.capitalize()} *(in {countdown})*")

    if not option or option.lower() != "soon":
        fixed, unknown = [], []
        for boss, data in without_timers:
            if data["schedule"]:
                fixed.append(f"🗓️ {boss.capitalize()} — Fixed: {', '.join(data['schedule'])}")
            else:
                unknown.append(f"❌ {boss.capitalize()} — No respawn data")
        if fixed:
            lines.append("\n**📌 Fixed Bosses:**")
            lines.extend(fixed)
        if unknown:
            lines.append("\n**❓ No Info Bosses:**")
            lines.extend(unknown)

    await ctx.send("\n".join(lines))

@bot.command(name="next")
async def next_boss(ctx, boss_name: str):
    """Show next respawn time for a specific boss."""
    boss = find_boss(boss_name)
    if not boss:
        await ctx.send(f"❌ Unknown boss: {boss_name}")
        return
    if boss not in respawn_schedule:
        await ctx.send(f"❌ {boss.capitalize()} has no respawn data.")
        return
    respawn_time = respawn_schedule[boss]
    ph_time = respawn_time.astimezone(ph_tz).strftime("%I:%M %p").lstrip("0").lower()
    countdown = format_countdown(respawn_time)
    await ctx.send(f"**{boss.capitalize()}** respawns at **{ph_time} PH** *(in {countdown})*")

@bot.command(name="setprealert")
async def setprealert(ctx, minutes: int):
    """Set pre-alert notification time before boss respawns."""
    global PRE_ALERT_MINUTES
    if minutes <= 0:
        await ctx.send("❌ Minutes must be positive.")
        return
    PRE_ALERT_MINUTES = minutes
    await ctx.send(f"✅ Pre-alert set to {minutes} minutes before respawn.")

@bot.command(name="commands")
async def commands_list(ctx):
    """Show all available commands."""
    cmds = [
        ("!dead <boss> [time]", "Mark a boss dead (uses PH time). Time optional."),
        ("!deadat <bulk input>", "Log one or multiple boss deaths at specific PH times."),
        ("!up <date + bulk input>", "Set next spawn times for one or multiple bosses. Date required."),
        ("!boss [soon]", "Show respawn timers. 'soon' shows next 5 respawns only."),
        ("!next <boss>", "Show next respawn time and countdown for a specific boss."),
        ("!setprealert <minutes>", "Set pre-alert notification time before boss respawns."),
        ("!commands", "Show this commands list."),
        ("!testchannels", "Check which channels the bot can send to."),
    ]
    longest = max(len(c[0]) for c in cmds)
    lines = ["**⚔️ LordNine Bot Commands — Usage**\n"]
    for cmd, desc in cmds:
        lines.append(f"`{cmd.ljust(longest)}` - {desc}")
    await ctx.send("\n".join(lines))

@bot.command(name="testchannels")
async def testchannels(ctx):
    """Debug which channels bot can send to."""
    ok, fail = [], []
    for cid in active_channels:
        channel = bot.get_channel(cid)
        if channel:
            try:
                await channel.send("✅ Test message")
                ok.append(f"{channel.guild.name} → {channel.name}")
            except Exception:
                fail.append(str(cid))
        else:
            fail.append(str(cid))
    await ctx.send(f"✅ OK: {', '.join(ok)} | ❌ Fail: {', '.join(fail)}")

# ==============================
# Startup
# ==============================

load_respawn_data()
bot.run(TOKEN)