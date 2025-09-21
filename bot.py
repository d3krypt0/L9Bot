# Imports
import discord
from discord.ext import commands
import asyncio
import json
import os
import re
import pytz
from discord import Embed
from datetime import datetime, timedelta, date, timezone
from zoneinfo import ZoneInfo

ph_tz = pytz.timezone("Asia/Manila")

# ==============================
# Helper Functions
# ==============================
def get_color(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
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
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ph_time = dt.astimezone(ph_tz)
    return ph_time.strftime("%b %d, %I:%M %p")

def format_countdown(dt):
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

# ==============================
# Config
# ==============================
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL", "1418187025873375272"))
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
bot = commands.Bot(command_prefix="!", intents=intents)

respawn_schedule = load_respawn_data()
active_tasks = {}

# ==============================
# Announce Boss
# ==============================
async def announce_boss(boss, respawn_time):
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print(f"⚠️ Channel {CHANNEL_ID} not found for boss {boss}")
        return

    # 10-minute pre-alert
    alert_time = respawn_time - timedelta(minutes=10)
    wait_seconds = (alert_time - datetime.utcnow().replace(tzinfo=timezone.utc)).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
        await channel.send(f"⏳ @everyone **{boss.capitalize()} will respawn in 10 minutes!** Get ready!")

    # Final respawn alert
    wait_seconds = (respawn_time - datetime.utcnow().replace(tzinfo=timezone.utc)).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)

    await channel.send(f"⚔️ @everyone **{boss.capitalize()} has respawned!** Go hunt!")

    if boss in respawn_schedule:
        del respawn_schedule[boss]
        save_respawn_data()

    if boss in active_tasks:
        del active_tasks[boss]

def schedule_boss(boss, respawn_time):
    if boss in active_tasks:
        task = active_tasks[boss]
        if not task.done():
            task.cancel()
    task = asyncio.create_task(announce_boss(boss, respawn_time))
    active_tasks[boss] = task

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
# Commands
# ==============================
@bot.command(name="dead")
async def dead(ctx, *, args: str = None):
    if not args:
        await ctx.send("❌ Usage: !dead <boss name> [time]")
        return

    parts = args.strip().split()
    boss = " ".join(parts[:-1]).lower() if len(parts) > 1 else parts[0].lower()
    if boss not in BOSSES:
        await ctx.send(f"❌ Unknown boss: {boss}")
        return

    interval = BOSSES[boss]["interval"]
    if not interval:
        await ctx.send(f"📅 **{boss.capitalize()}** is on a fixed schedule: {BOSSES[boss]['schedule']}")
        return

    killed_time = datetime.now(timezone.utc)
    respawn_time = killed_time + timedelta(hours=interval)
    respawn_schedule[boss] = respawn_time
    save_respawn_data()
    schedule_boss(boss, respawn_time)

    await ctx.send(
        f"✅ {boss.capitalize()} marked dead at {format_time(killed_time)} PH "
        f"→ Respawns at {format_time(respawn_time)} PH"
    )

@bot.command(name="deadat")
async def deadat(ctx, *, args: str = None):
    if not args:
        await ctx.send("❌ Usage: !deadat <boss name> [time]")
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
        match = re.match(r"^([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM|am|pm)?)\s*-\s*(.+)$", line)
        if not match:
            results.append(f"⚠️ Could not parse line: `{line}`")
            continue

        time_str = match.group(1).strip()
        boss_raw = match.group(2).split("(")[0].strip().lower()

        if boss_raw not in BOSSES:
            results.append(f"❌ Unknown boss: {boss_raw}")
            continue

        if not BOSSES[boss_raw]["interval"]:
            results.append(f"📅 **{boss_raw.capitalize()}** is on a fixed schedule: {BOSSES[boss_raw]['schedule']}")
            continue

        time_obj = parse_time_str(time_str)
        if not time_obj:
            results.append(f"❌ Invalid time: `{time_str}`")
            continue

        killed_time = ph_tz.localize(datetime.combine(current_date, time_obj))
        respawn_time = killed_time + timedelta(hours=BOSSES[boss_raw]["interval"])
        respawn_schedule[boss_raw] = respawn_time
        save_respawn_data()
        schedule_boss(boss_raw, respawn_time)

        results.append(f"✅ {boss_raw.capitalize()} logged → Respawns at {respawn_time.strftime('%Y-%m-%d %I:%M %p PH')}")

    await ctx.send("\n".join(results) if results else "❌ No valid entries found.")

@bot.command(name="up")
async def up(ctx, *, args: str = None):
    if not args:
        await ctx.send("❌ Usage: single or bulk input.")
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
        match = re.match(r"^([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM|am|pm)?)\s*-\s*(.+)$", line)
        if not match:
            results.append(f"⚠️ Could not parse line: `{line}`")
            continue

        time_str = match.group(1).strip()
        boss_raw = match.group(2).split("(")[0].strip().lower()

        if boss_raw not in BOSSES:
            results.append(f"❌ Unknown boss: {boss_raw}")
            continue

        time_obj = parse_time_str(time_str)
        if not time_obj:
            results.append(f"❌ Invalid time: `{time_str}`")
            continue

        ph_dt = ph_tz.localize(datetime.combine(current_date, time_obj))
        respawn_utc = ph_dt.astimezone(pytz.UTC)

        respawn_schedule[boss_raw] = respawn_utc
        save_respawn_data()
        schedule_boss(boss_raw, respawn_utc)

        results.append(f"✅ {boss_raw.capitalize()} set → {ph_dt.strftime('%Y-%m-%d %I:%M %p PH')}")

    await ctx.send("\n".join(results) if results else "❌ No valid entries found.")

@bot.command(name="boss")
async def boss(ctx, option: str = None):
    with_timers = []
    fixed_bosses = []
    no_info_bosses = []

    now = datetime.utcnow().replace(tzinfo=timezone.utc)

    for boss, data in BOSSES.items():
        if boss in respawn_schedule:
            respawn_time = respawn_schedule[boss]
            if respawn_time.tzinfo is None:
                respawn_time = respawn_time.replace(tzinfo=pytz.UTC)
            if respawn_time > now:
                with_timers.append((boss, respawn_time))
            else:
                del respawn_schedule[boss]
                save_respawn_data()
        else:
            if data["schedule"]:
                fixed_bosses.append((boss, data["schedule"]))
            else:
                no_info_bosses.append(boss)

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
            lines.append("\n**📌 Fixed Bosses:**")
            for boss, schedule in fixed_bosses:
                schedule_str = ", ".join(schedule)
                lines.append(f"🗓️ {boss.capitalize()} — {schedule_str}")
        if no_info_bosses:
            lines.append("\n**❌ No Info Bosses:**")
            for boss in no_info_bosses:
                lines.append(f"❌ {boss.capitalize()} — No respawn data")

    await ctx.send("\n".join(lines))

@bot.command(name="next")
async def next_boss(ctx, *, boss: str = None):
    if not boss:
        await ctx.send("❌ Please provide a boss name.")
        return
    boss = boss.lower()
    if boss not in BOSSES:
        await ctx.send(f"❌ Unknown boss: {boss}")
        return
    if boss in respawn_schedule:
        respawn_time = respawn_schedule[boss]
        countdown = respawn_time - datetime.utcnow().replace(tzinfo=timezone.utc)
        await ctx.send(
            f"⏳ **{boss.capitalize()}** respawns at {format_time(respawn_time)} PH "
            f"(in {str(countdown).split('.')[0]})"
        )
    elif BOSSES[boss]["schedule"]:
        schedule = ", ".join(BOSSES[boss]["schedule"])
        await ctx.send(f"📅 **{boss.capitalize()}** spawns on schedule: {schedule}")
    else:
        await ctx.send(f"❌ No respawn info for **{boss.capitalize()}**")

@bot.command(name="setprealert")
async def setprealert(ctx, minutes: int):
    global PRE_ALERT_MINUTES
    if minutes < 1:
        await ctx.send("❌ Pre-alert must be at least 1 minute.")
        return
    PRE_ALERT_MINUTES = minutes
    await ctx.send(f"✅ Pre-alert time set to **{minutes} minutes**.")

@bot.command(name="helpme")
async def helpme(ctx):
    cmds = [
        ("!dead <boss> [time]", "Log a boss death now or at a given PH time."),
        ("!deadat <bulk input>", "Log one or more boss deaths at specific PH times."),
        ("!up <bulk input>", "Manually set next spawn times."),
        ("!boss [soon]", "Show respawn timers. 'soon' shows next 5 only."),
        ("!next <boss>", "Show next respawn time/countdown."),
        ("!setprealert <minutes>", "Set pre-alert time (default 10m)."),
        ("!helpme", "Show this help message."),
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