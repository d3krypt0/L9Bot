# Imports
import discord
from discord.ext import commands
import asyncio
import json
import os
import re
import pytz
from datetime import datetime, timedelta, date, timezone

# Timezone
ph_tz = pytz.timezone("Asia/Manila")

# ==============================
# Helper Functions
# ==============================
def get_color(dt):
    """Return color based on respawn time proximity."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    delta = (dt - now).total_seconds()
    if delta <= 0:
        return 0x95a5a6  # gray
    elif delta <= 600:
        return 0xe74c3c  # red
    elif delta <= 3600:
        return 0xf1c40f  # yellow
    else:
        return 0x2ecc71  # green

def format_time(dt):
    """Convert UTC datetime to PH time in 12h format with AM/PM."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ph_time = dt.astimezone(ph_tz)
    return ph_time.strftime("%b %d, %I:%M %p")

def format_countdown(dt):
    """Return countdown string like '2h 15m'."""
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

def find_boss(name: str):
    """Fuzzy match boss names (case-insensitive, partial)."""
    name = name.lower()
    for b in BOSSES.keys():
        if name == b.lower():
            return b
    for b in BOSSES.keys():
        if name in b.lower():
            return b
    return None

# ==============================
# Config
# ==============================
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL", "0"))
BOSS_FILE = "bosses.json"
SAVE_FILE = "respawn_data.json"

# ==============================
# Load boss data
# ==============================
with open(BOSS_FILE, "r") as f:
    BOSSES = json.load(f)

# ==============================
# JSON persistence
# ==============================
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
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
respawn_schedule = load_respawn_data()

# ==============================
# Events
# ==============================
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    for boss, respawn_time in respawn_schedule.items():
        if respawn_time > now:
            asyncio.create_task(announce_boss(boss, respawn_time))

# ==============================
# Commands
# ==============================
@bot.command(name="boss")
async def boss(ctx, option: str = None):
    """Show respawn timers in an easy-to-read format."""
    with_timers, fixed_bosses, noinfo_bosses = [], [], []
    now = datetime.utcnow().replace(tzinfo=timezone.utc)

    for boss, data in BOSSES.items():
        if boss in respawn_schedule:
            respawn_time = respawn_schedule[boss]
            if respawn_time > now:
                with_timers.append((boss, respawn_time))
            else:
                del respawn_schedule[boss]
                save_respawn_data()
        elif data["schedule"]:
            fixed_bosses.append((boss, data["schedule"]))
        else:
            noinfo_bosses.append(boss)

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
        if fixed_bosses:
            lines.append("\n**📌 Fixed Schedule Bosses:**")
            for boss, sched in fixed_bosses:
                lines.append(f"🗓️ {boss.capitalize()} — {', '.join(sched)}")
        if noinfo_bosses:
            lines.append("\n**❌ No Info Bosses:**")
            for boss in noinfo_bosses:
                lines.append(f"❌ {boss.capitalize()} — No respawn data")

    await ctx.send("\n".join(lines))

@bot.command(name="next")
async def next_spawn(ctx, *, boss: str = None):
    """Show next respawn for a boss."""
    if not boss:
        await ctx.send("❌ Please provide a boss name.")
        return

    boss_key = find_boss(boss)
    if not boss_key:
        await ctx.send(f"❌ Unknown boss: {boss}")
        return

    data = BOSSES[boss_key]
    if boss_key in respawn_schedule:
        respawn_time = respawn_schedule[boss_key]
        countdown = format_countdown(respawn_time)
        await ctx.send(f"⏳ **{boss_key.capitalize()}** respawns at {format_time(respawn_time)} PH *(in {countdown})*")
    elif data["schedule"]:
        await ctx.send(f"📅 **{boss_key.capitalize()}** spawns on schedule: {', '.join(data['schedule'])}")
    else:
        await ctx.send(f"❌ No respawn info for **{boss_key.capitalize()}**")

@bot.command(name="dead")
async def dead(ctx, *, args: str = None):
    """Log a boss death at current or specific time."""
    if not args:
        await ctx.send("❌ Usage: !dead <boss> [time]")
        return

    parts = args.strip().split()
    boss_key = find_boss(" ".join(parts[:-1]) if len(parts) > 1 else parts[0])
    if not boss_key:
        await ctx.send(f"❌ Unknown boss: {' '.join(parts)}")
        return

    if BOSSES[boss_key]["schedule"]:
        await ctx.send(f"📅 **{boss_key.capitalize()}** is fixed-schedule: {', '.join(BOSSES[boss_key]['schedule'])}")
        return

    killed_time = datetime.now(timezone.utc)
    if len(parts) > 1:
        try:
            time_str = parts[-1].upper()
            if "AM" in time_str or "PM" in time_str:
                time_obj = datetime.strptime(parts[-2] + " " + parts[-1], "%I:%M %p").time()
            else:
                time_obj = datetime.strptime(parts[-1], "%H:%M").time()
            killed_time = datetime.combine(date.today(), time_obj).replace(tzinfo=ph_tz).astimezone(timezone.utc)
        except Exception:
            pass

    interval = BOSSES[boss_key]["interval"]
    respawn_time = killed_time + timedelta(hours=interval)
    respawn_schedule[boss_key] = respawn_time
    save_respawn_data()
    asyncio.create_task(announce_boss(boss_key, respawn_time))

    await ctx.send(f"✅ {boss_key.capitalize()} dead at {format_time(killed_time)} → Respawns at {format_time(respawn_time)}")

@bot.command(name="deadat")
async def deadat(ctx, *, args: str = None):
    """Bulk or single boss death logging."""
    if not args:
        await ctx.send("❌ Usage: !deadat <bulk input>")
        return

    lines = [ln.strip() for ln in args.splitlines() if ln.strip()]
    results = []
    current_date = date.today()

    for line in lines:
        # Check if date header
        try:
            parsed_date = datetime.strptime(line.title(), "%B %d").date().replace(year=date.today().year)
            current_date = parsed_date
            continue
        except ValueError:
            pass

        match = re.match(r"^([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM|am|pm)?)\s*-\s*(.+)$", line)
        if not match:
            results.append(f"⚠️ Could not parse line: `{line}`")
            continue

        time_str, boss_raw = match.groups()
        boss_key = find_boss(boss_raw)
        if not boss_key:
            results.append(f"❌ Unknown boss: {boss_raw}")
            continue

        if BOSSES[boss_key]["schedule"]:
            results.append(f"📅 **{boss_key.capitalize()}** is fixed-schedule: {', '.join(BOSSES[boss_key]['schedule'])}")
            continue

        try:
            time_obj = datetime.strptime(time_str.upper(), "%I:%M %p").time() if "AM" in time_str.upper() or "PM" in time_str.upper() else datetime.strptime(time_str, "%H:%M").time()
        except ValueError:
            results.append(f"❌ Invalid time: {time_str}")
            continue

        killed_time = datetime.combine(current_date, time_obj).replace(tzinfo=ph_tz).astimezone(timezone.utc)
        interval = BOSSES[boss_key]["interval"]
        respawn_time = killed_time + timedelta(hours=interval)
        respawn_schedule[boss_key] = respawn_time
        save_respawn_data()
        asyncio.create_task(announce_boss(boss_key, respawn_time))

        results.append(f"✅ {boss_key.capitalize()} dead at {format_time(killed_time)} → Respawns at {format_time(respawn_time)}")

    await ctx.send("\n".join(results))

@bot.command(name="up")
async def up(ctx, *, args: str = None):
    """Set next spawn times (bulk or single)."""
    if not args:
        await ctx.send("❌ Usage: !up <bulk input>")
        return

    lines = [ln.strip() for ln in args.splitlines() if ln.strip()]
    results = []
    current_date = date.today()

    for line in lines:
        # Date header
        try:
            parsed_date = datetime.strptime(line.title(), "%B %d").date().replace(year=date.today().year)
            current_date = parsed_date
            continue
        except ValueError:
            pass

        match = re.match(r"^([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM|am|pm)?)\s*-\s*(.+)$", line)
        if not match:
            results.append(f"⚠️ Could not parse line: `{line}`")
            continue

        time_str, boss_raw = match.groups()
        boss_key = find_boss(boss_raw)
        if not boss_key:
            results.append(f"❌ Unknown boss: {boss_raw}")
            continue

        try:
            time_obj = datetime.strptime(time_str.upper(), "%I:%M %p").time() if "AM" in time_str.upper() or "PM" in time_str.upper() else datetime.strptime(time_str, "%H:%M").time()
        except ValueError:
            results.append(f"❌ Invalid time: {time_str}")
            continue

        ph_dt = ph_tz.localize(datetime.combine(current_date, time_obj))
        respawn_utc = ph_dt.astimezone(timezone.utc)
        respawn_schedule[boss_key] = respawn_utc
        save_respawn_data()
        asyncio.create_task(announce_boss(boss_key, respawn_utc))
        results.append(f"✅ {boss_key.capitalize()} set → {ph_dt.strftime('%Y-%m-%d %I:%M %p PH')}")

    await ctx.send("\n".join(results))

@bot.command(name="setprealert")
async def setprealert(ctx, minutes: int):
    """Set pre-alert notification minutes."""
    global PRE_ALERT_MINUTES
    if minutes < 1:
        await ctx.send("❌ Pre-alert must be >= 1")
        return
    PRE_ALERT_MINUTES = minutes
    await ctx.send(f"✅ Pre-alert set to {minutes} minutes.")

# ==============================
# Alerts
# ==============================
PRE_ALERT_MINUTES = 10

async def announce_boss(boss, respawn_time):
    """Announce only at 10 minutes before and at respawn."""
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return

    # Pre-alert
    alert_time = respawn_time - timedelta(minutes=PRE_ALERT_MINUTES)
    wait_seconds = (alert_time - datetime.utcnow().replace(tzinfo=timezone.utc)).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
        await channel.send(f"⏳ @everyone **{boss.capitalize()} will respawn in {PRE_ALERT_MINUTES} minutes!**")

    # Final alert
    wait_seconds = (respawn_time - datetime.utcnow().replace(tzinfo=timezone.utc)).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
    await channel.send(f"⚔️ @everyone **{boss.capitalize()} has respawned!** Go hunt!")

    if boss in respawn_schedule:
        del respawn_schedule[boss]
        save_respawn_data()

# ==============================
# Help / Commands list
# ==============================
@bot.command(name="commands", aliases=["help"])
async def commands_list(ctx):
    """Summarize commands."""
    cmds = [
        ("!boss [soon]", "Show respawn timers"),
        ("!next <boss>", "Show next respawn"),
        ("!dead <boss> [time]", "Mark boss dead"),
        ("!deadat <bulk>", "Bulk deaths"),
        ("!up <bulk>", "Bulk spawn times"),
        ("!setprealert <minutes>", "Change pre-alert"),
        ("!commands / !help", "Show help"),
    ]
    longest = max(len(c[0]) for c in cmds)
    lines = ["**⚔️ LordNine Bot Commands**\n"]
    for cmd, desc in cmds:
        lines.append(f"`{cmd.ljust(longest)}` - {desc}")
    await ctx.send("\n".join(lines))

# ==============================
# Run bot
# ==============================
bot.run(TOKEN)
