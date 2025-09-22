# Imports
import discord
from discord.ext import commands
import asyncio
import json
import os
import re
import pytz
from datetime import datetime, timedelta, date, timezone
from zoneinfo import ZoneInfo

# Timezone
ph_tz = pytz.timezone("Asia/Manila")

# ==============================
# Helper Functions
# ==============================
def get_color(dt):
    """Return a color based on how soon the respawn is."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    delta = (dt - now).total_seconds()
    if delta <= 0:
        return 0x95a5a6  # gray
    elif delta <= 600:  # 10 minutes
        return 0xe74c3c  # red
    elif delta <= 3600:  # 1 hour
        return 0xf1c40f  # yellow
    else:
        return 0x2ecc71  # green

def format_time(dt):
    """Convert UTC datetime to PH time in 12h format."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ph_time = dt.astimezone(ph_tz)
    return ph_time.strftime("%b %d, %I:%M %p")

def format_countdown(dt):
    """Return countdown like '2h 15m'."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    delta = dt - now
    if delta.total_seconds() <= 0:
        return "⏳ Any moment now!"
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"

def parse_time_str(s: str):
    """Parse time string like '17:00', '5:00 PM'."""
    if not s:
        return None
    s_clean = s.strip().upper().replace(".", "")
    try:
        if "AM" in s_clean or "PM" in s_clean:
            return datetime.strptime(s_clean, "%I:%M %p").time()
        else:
            return datetime.strptime(s_clean, "%H:%M").time()
    except ValueError:
        return None

def find_boss(query: str):
    """Resolve boss name by exact, partial, or case-insensitive match."""
    query = query.lower()
    # Exact match
    if query in BOSSES:
        return query
    # Partial match
    matches = [b for b in BOSSES if query in b.lower()]
    if len(matches) == 1:
        return matches[0]
    return None

def get_channels():
    """Return a list of valid Discord channel objects."""
    channels = [bot.get_channel(cid) for cid in CHANNEL_IDS]
    return [ch for ch in channels if ch]  # filter out None

# ==============================
# Config
# ==============================
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
BOSS_FILE = "bosses.json"
SAVE_FILE = "respawn_data.json"
# Supports multiple channel IDs separated by commas
CHANNEL_IDS = os.getenv("DISCORD_CHANNEL_IDS", "")
CHANNEL_IDS = [int(cid.strip()) for cid in CHANNEL_IDS.split(",") if cid.strip()]


# Load boss data
with open(BOSS_FILE, "r") as f:
    BOSSES = json.load(f)

# JSON persistence
def load_respawn_data():
    if os.path.exists(SAVE_FILE):
        with open(SAVE_FILE, "r") as f:
            data = json.load(f)
            result = {}
            for boss, t in data.items():
                dt = datetime.fromisoformat(t)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                result[boss] = dt
            return result
    return {}

def save_respawn_data():
    with open(SAVE_FILE, "w") as f:
        json.dump({boss: t.isoformat() for boss, t in respawn_schedule.items()}, f, indent=4)

# ==============================
# Bot setup
# ==============================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

respawn_schedule = load_respawn_data()
PRE_ALERT_MINUTES = 10

# ==============================
# Events
# ==============================
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    for boss, respawn_time in respawn_schedule.items():
        if respawn_time > now:
            schedule_boss(boss, respawn_time)

# ==============================
# Announcements
# ==============================
async def announce_boss(boss, respawn_time):
    for channel in get_channels():
        return

    # Pre-alert (10 minutes before)
    alert_time = respawn_time - timedelta(minutes=PRE_ALERT_MINUTES)
    wait_seconds = (alert_time - datetime.utcnow().replace(tzinfo=timezone.utc)).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
        await channel.send(f"⏳ @everyone **{boss.capitalize()} will respawn in {PRE_ALERT_MINUTES} minutes!** Get ready!")

    # Final alert
    wait_seconds = (respawn_time - datetime.utcnow().replace(tzinfo=timezone.utc)).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
    await channel.send(f"⚔️ @everyone **{boss.capitalize()} has respawned!** Go hunt!")

    if boss in respawn_schedule:
        del respawn_schedule[boss]
        save_respawn_data()

def schedule_boss(boss, respawn_time):
    respawn_schedule[boss] = respawn_time
    asyncio.create_task(announce_boss(boss, respawn_time))

# ==============================
# Commands
# ==============================
@bot.command(name="boss")
async def boss(ctx, option: str = None):
    """List respawn timers and fixed bosses."""
    with_timers = []
    fixed = []
    noinfo = []

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
            if data["schedule"]:
                fixed.append((boss, data["schedule"]))
            else:
                noinfo.append(boss)

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
        if fixed:
            lines.append("\n**📌 Fixed Schedule Bosses:**")
            for boss, sched in fixed:
                lines.append(f"🗓️ {boss.capitalize()} — {', '.join(sched)}")
        if noinfo:
            lines.append("\n**❌ No Info Bosses:**")
            for boss in noinfo:
                lines.append(f"❌ {boss.capitalize()} — No respawn data")

    await ctx.send("\n".join(lines))

@bot.command(name="next")
async def next_cmd(ctx, *, boss: str = None):
    """Show next respawn time for a boss."""
    if not boss:
        await ctx.send("❌ Please provide a boss name.")
        return

    boss_key = find_boss(boss)
    if not boss_key:
        await ctx.send(f"❌ Unknown boss: {boss}")
        return

    if boss_key in respawn_schedule:
        respawn_time = respawn_schedule[boss_key]
        countdown = format_countdown(respawn_time)
        await ctx.send(f"⏳ **{boss_key.capitalize()}** respawns at {format_time(respawn_time)} PH (in {countdown})")
    elif BOSSES[boss_key]["schedule"]:
        schedule = ", ".join(BOSSES[boss_key]["schedule"])
        await ctx.send(f"📅 **{boss_key.capitalize()}** spawns on schedule: {schedule}")
    else:
        await ctx.send(f"❌ No respawn info for **{boss_key.capitalize()}**")

# ==============================
# Dead & DeadAt Commands
# ==============================
@bot.command(name="dead")
async def dead(ctx, *, args: str = None):
    """Mark a boss dead (single)."""
    if not args:
        await ctx.send("❌ Usage: !dead <boss> [time]")
        return

    parts = args.strip().split(maxsplit=1)
    boss_raw = parts[0]
    boss_key = find_boss(boss_raw)
    if not boss_key:
        await ctx.send(f"❌ Unknown boss: {boss_raw}")
        return

    if BOSSES[boss_key].get("schedule"):
        await ctx.send(f"🗓️ **{boss_key.capitalize()}** is fixed-schedule: {', '.join(BOSSES[boss_key]['schedule'])}")
        return

    # Default = now (PH time)
    killed_ph = datetime.now(ph_tz)

    # If time is provided, override today's PH date
    if len(parts) > 1:
        time_str = parts[1]
        time_obj = parse_time_str(time_str)
        if not time_obj:
            await ctx.send(f"⚠️ Could not parse time: {time_str}")
            return
        killed_ph = ph_tz.localize(datetime.combine(date.today(), time_obj))

    # Calculate respawn
    interval = BOSSES[boss_key]["interval"]
    respawn_ph = killed_ph + timedelta(hours=interval)
    respawn_utc = respawn_ph.astimezone(pytz.UTC)

    # Save & schedule
    respawn_schedule[boss_key] = respawn_utc
    save_respawn_data()
    schedule_boss(boss_key, respawn_utc)

    await ctx.send(
        f"✅ {boss_key.capitalize()} dead at {killed_ph.strftime('%I:%M %p PH')} "
        f"→ Respawns at {respawn_ph.strftime('%I:%M %p PH')}"
    )

@bot.command(name="deadat")
async def deadat(ctx, *, args: str = None):
    """Log boss deaths (bulk)."""
    if not args:
        await ctx.send("❌ Usage: !deadat <bulk input>")
        return

    lines = args.strip().splitlines()
    ph_now = datetime.now(ph_tz)
    current_date = ph_now.date()
    results = []

    for line in lines:
        try:
            parsed_date = datetime.strptime(line.title(), "%B %d").date()
            current_date = parsed_date.replace(year=ph_now.year)
            results.append(f"📅 Using date: {current_date}")
            continue
        except ValueError:
            pass

        m = re.match(r"^([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM|am|pm)?)\s*-\s*(.+)$", line)
        if not m:
            results.append(f"⚠️ Could not parse line: {line}")
            continue

        time_str, boss_raw = m.groups()
        boss_key = find_boss(boss_raw)
        if not boss_key:
            results.append(f"❌ Unknown boss: {boss_raw}")
            continue

        if BOSSES[boss_key].get("schedule"):
            results.append(f"🗓️ **{boss_key.capitalize()}** is fixed-schedule: {', '.join(BOSSES[boss_key]['schedule'])}")
            continue

        time_obj = parse_time_str(time_str)
        killed_ph = ph_tz.localize(datetime.combine(current_date, time_obj))
        interval = BOSSES[boss_key]["interval"]
        respawn_ph = killed_ph + timedelta(hours=interval)
        respawn_utc = respawn_ph.astimezone(pytz.UTC)

        respawn_schedule[boss_key] = respawn_utc
        save_respawn_data()
        schedule_boss(boss_key, respawn_utc)

        results.append(f"✅ {boss_key.capitalize()} logged → Respawns at {respawn_ph.strftime('%Y-%m-%d %I:%M %p PH')}")

    await ctx.send("\n".join(results))

# ==============================
# Up Command
# ==============================
@bot.command(name="up")
async def up(ctx, *, args: str = None):
    """Set spawn times (bulk)."""
    if not args:
        await ctx.send("❌ Usage: !up <bulk input>")
        return

    lines = args.strip().splitlines()
    ph_now = datetime.now(ph_tz)
    current_date = ph_now.date()
    results = []

    for line in lines:
        try:
            parsed_date = datetime.strptime(line.title(), "%B %d").date()
            current_date = parsed_date.replace(year=ph_now.year)
            results.append(f"📅 Using date: {current_date}")
            continue
        except ValueError:
            pass

        m = re.match(r"^([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM|am|pm)?)\s*-\s*(.+)$", line)
        if not m:
            results.append(f"⚠️ Could not parse line: {line}")
            continue

        time_str, boss_raw = m.groups()
        boss_key = find_boss(boss_raw)
        if not boss_key:
            results.append(f"❌ Unknown boss: {boss_raw}")
            continue

        time_obj = parse_time_str(time_str)
        ph_dt = ph_tz.localize(datetime.combine(current_date, time_obj))
        respawn_utc = ph_dt.astimezone(pytz.UTC)

        respawn_schedule[boss_key] = respawn_utc
        save_respawn_data()
        schedule_boss(boss_key, respawn_utc)

        results.append(f"✅ {boss_key.capitalize()} set → {ph_dt.strftime('%Y-%m-%d %I:%M %p PH')}")

    await ctx.send("\n".join(results))

# ==============================
# Commands List
# ==============================
@bot.command(name="commands")
async def commands_list(ctx):
    """List all available commands."""
    cmds = [
        ("!boss [soon]", "Show respawn timers (or next 5 with 'soon')"),
        ("!next <boss>", "Show next respawn time for a boss"),
        ("!dead <boss> [time]", "Log a boss death (PH time)"),
        ("!deadat <bulk>", "Log multiple boss deaths (bulk input)"),
        ("!up <bulk>", "Set spawn times manually (bulk input)"),
        ("!commands", "Show this help list")
    ]
    longest = max(len(c[0]) for c in cmds)
    lines = ["**⚔️ LordNine Bot Commands — Usage**\n"]
    for cmd, desc in cmds:
        lines.append(f"`{cmd.ljust(longest)}` - {desc}")
    await ctx.send("\n".join(lines))

# ==============================
# Run Bot
# ==============================
bot.run(TOKEN)
