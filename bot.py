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
# Config
# ==============================
TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # Use Railway/Env for token
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "123456789012345678"))
BOSS_FILE = "bosses.json"
SAVE_FILE = "respawn_data.json"
PRE_ALERT_MINUTES = 10  # default pre-alert

# ==============================
# Load boss data
# ==============================
with open(BOSS_FILE, "r") as f:
    BOSSES = json.load(f)

# ==============================
# Helper Functions
# ==============================
def get_color(dt):
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    delta = (dt - now).total_seconds()
    if delta <= 0:
        return 0x95a5a6
    elif delta <= 600:
        return 0xe74c3c
    elif delta <= 3600:
        return 0xf1c40f
    else:
        return 0x2ecc71

def format_time(dt):
    ph_time = dt.astimezone(ph_tz)
    return ph_time.strftime("%b %d, %I:%M %p")

def format_countdown(dt):
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
    """Case-insensitive + partial boss name match"""
    name = name.lower().strip()
    for boss in BOSSES.keys():
        if name == boss.lower():
            return boss
    matches = [boss for boss in BOSSES.keys() if name in boss.lower()]
    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        return min(matches, key=len)
    return None

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
bot = commands.Bot(command_prefix="!", intents=intents)
respawn_schedule = load_respawn_data()

# ==============================
# Events
# ==============================
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    for boss, respawn_time in list(respawn_schedule.items()):
        if respawn_time > now:
            asyncio.create_task(announce_boss(boss, respawn_time))
        else:
            del respawn_schedule[boss]
    save_respawn_data()

# ==============================
# Commands
# ==============================

@bot.command(name="dead")
async def dead(ctx, *, args: str = None):
    """Log a boss death manually"""
    if not args:
        await ctx.send("❌ Usage: !dead <boss name> [time]")
        return

    parts = args.strip().split()
    killed_date = date.today()
    time_str = None
    boss_name_parts = []

    if len(parts) >= 2 and parts[-1].upper() in ["AM", "PM"]:
        time_str = parts[-2] + " " + parts[-1]
        boss_name_parts = parts[:-2]
    elif len(parts) >= 1:
        try:
            datetime.strptime(parts[-1], "%H:%M")
            time_str = parts[-1]
            boss_name_parts = parts[:-1]
        except ValueError:
            boss_name_parts = parts

    boss_key = find_boss(" ".join(boss_name_parts))
    if not boss_key:
        await ctx.send(f"❌ Unknown boss: {' '.join(boss_name_parts)}")
        return

    if not time_str:
        killed_time = datetime.now(timezone.utc)
    else:
        try:
            if "AM" in time_str.upper() or "PM" in time_str.upper():
                time_obj = datetime.strptime(time_str.upper(), "%I:%M %p").time()
            else:
                time_obj = datetime.strptime(time_str, "%H:%M").time()
        except ValueError:
            await ctx.send("❌ Invalid time format.")
            return
        killed_time = datetime.combine(killed_date, time_obj).replace(tzinfo=timezone.utc)

    interval = BOSSES[boss_key]["interval"]
    if not interval:
        await ctx.send(f"📅 **{boss_key.capitalize()}** is on a fixed schedule: {BOSSES[boss_key]['schedule']}")
        return

    respawn_time = killed_time + timedelta(hours=interval)
    respawn_schedule[boss_key] = respawn_time
    save_respawn_data()
    schedule_boss(boss_key, respawn_time)

    await ctx.send(
        f"✅ {boss_key.capitalize()} marked dead at {format_time(killed_time)} PH → Respawns at {format_time(respawn_time)} PH"
    )

@bot.command(name="deadat")
async def deadat(ctx, *, args: str = None):
    """Log boss deaths (bulk format supported with date headers)"""
    if not args:
        await ctx.send("❌ Usage: !deadat <bulk input>")
        return

    lines = [ln.strip() for ln in args.splitlines() if ln.strip()]
    results = []
    current_date = date.today()

    def parse_time_str(s):
        s_clean = s.strip().upper().replace(".", "")
        try:
            if "AM" in s_clean or "PM" in s_clean:
                return datetime.strptime(s_clean, "%I:%M %p").time()
            else:
                return datetime.strptime(s_clean, "%H:%M").time()
        except ValueError:
            return None

    for line in lines:
        try:
            parsed_date = datetime.strptime(line.title(), "%B %d").date()
            current_date = parsed_date.replace(year=date.today().year)
            results.append(f"📅 Using date: {current_date.strftime('%B %d, %Y')}")
            continue
        except ValueError:
            pass

        match = re.match(r"^([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM|am|pm)?)\s*-\s*(.+)$", line)
        if not match:
            results.append(f"⚠️ Could not parse line: `{line}`")
            continue

        time_str = match.group(1).strip()
        boss_raw = match.group(2).split("(")[0].strip()
        boss_key = find_boss(boss_raw)
        if not boss_key:
            results.append(f"❌ Unknown boss: {boss_raw}")
            continue

        if not BOSSES[boss_key]["interval"]:
            results.append(f"📅 **{boss_key.capitalize()}** is on a fixed schedule: {BOSSES[boss_key]['schedule']}")
            continue

        time_obj = parse_time_str(time_str)
        if not time_obj:
            results.append(f"❌ Invalid time: `{time_str}`")
            continue

        killed_time = ph_tz.localize(datetime.combine(current_date, time_obj))
        respawn_time = killed_time + timedelta(hours=BOSSES[boss_key]["interval"])
        respawn_schedule[boss_key] = respawn_time
        save_respawn_data()
        schedule_boss(boss_key, respawn_time)

        results.append(f"✅ {boss_key.capitalize()} logged → Respawns at {respawn_time.strftime('%Y-%m-%d %I:%M %p PH')}")

    await ctx.send("\n".join(results) if results else "❌ No valid entries found.")

@bot.command(name="commands")
async def commands_list(ctx):
    """Show available commands"""
    cmds = [
        ("!boss [soon]", "Show all respawn timers (or next 5 with 'soon')"),
        ("!next <boss>", "Show next respawn for a boss"),
        ("!dead <boss> [time]", "Mark boss dead (PH time, optional time argument)"),
        ("!deadat <bulk>", "Log multiple boss deaths with optional date headers"),
        ("!up <bulk>", "Set next spawn times (bulk format, overwrites existing)"),
        ("!setprealert <minutes>", "Set pre-alert before respawn (default 10)"),
        ("!commands", "Show this list")
    ]
    longest = max(len(c[0]) for c in cmds)
    lines = ["**⚔️ LordNine Bot Commands**\n"]
    for cmd, desc in cmds:
        lines.append(f"`{cmd.ljust(longest)}` - {desc}")
    await ctx.send("\n".join(lines))

# ==============================
# Respawn Announcer
# ==============================
async def announce_boss(boss, respawn_time):
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return
    alert_time = respawn_time - timedelta(minutes=PRE_ALERT_MINUTES)
    wait_seconds = (alert_time - datetime.utcnow().replace(tzinfo=timezone.utc)).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
        await channel.send(f"⏳ @everyone **{boss.capitalize()} will respawn in {PRE_ALERT_MINUTES} minute(s)!** Get ready!")
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
# Run Bot
# ==============================
bot.run(TOKEN)
