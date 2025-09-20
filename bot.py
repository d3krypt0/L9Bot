# Imports
import discord
from discord.ext import commands
import asyncio
import json
import os
import re
import pytz  # pip install pytz
from discord import Embed
from datetime import datetime, timedelta, date, timezone
from zoneinfo import ZoneInfo

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
        return 0x95a5a6  # gray (already due)
    elif delta <= 600:  # 10 minutes
        return 0xe74c3c  # red
    elif delta <= 3600:  # 1 hour
        return 0xf1c40f  # yellow
    else:
        return 0x2ecc71  # green


def format_time(dt):
    """Convert UTC datetime to PH time in 12h format with AM/PM"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ph_time = dt.astimezone(ph_tz)
    return ph_time.strftime("%b %d, %I:%M %p")


def format_countdown(dt):
    """Return countdown like '2h 15m'"""
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
# CONFIG
# ==============================
TOKEN = os.getenv("DISCORD_TOKEN")  # Get from Railway Variables
CHANNEL_ID = 1418187025873375272    # Update if your channel changes
BOSS_FILE = "bosses.json"
SAVE_FILE = "respawn_data.json"

if not TOKEN:
    raise ValueError("❌ DISCORD_TOKEN environment variable not set. Please configure it in Railway.")

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
# Load saved respawn data
# ==============================
respawn_schedule = load_respawn_data()  # Load from respawn_data.json

# ==============================
# Bot setup
# ==============================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ==============================
# On ready event
# ==============================
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    
    # Resume saved timers
    for boss, respawn_time in respawn_schedule.items():
        if respawn_time.tzinfo is None:
            respawn_time = respawn_time.replace(tzinfo=timezone.utc)
        if respawn_time > now:
            asyncio.create_task(announce_boss(boss, respawn_time))

    print(f"✅ Resumed {len(respawn_schedule)} scheduled boss respawn(s).")


# ==============================
# Commands
# ==============================


# ===========================
# LIST BOSS COMMAND
# ===========================
@bot.command(name="boss")
async def list(ctx, option: str = None):
    """
    Show respawn timers in an easy-to-read format.
    Usage:
      !boss          → Show all respawns sorted by time
      !boss soon     → Show next 5 respawns only
    """
    with_timers = []
    without_timers = []

    # Separate bosses with and without timers
    for boss, data in BOSSES.items():
        if boss in respawn_schedule:
            respawn_time = respawn_schedule[boss]
            if respawn_time.tzinfo is None:
                respawn_time = respawn_time.replace(tzinfo=pytz.UTC)
            with_timers.append((boss, respawn_time))
        else:
            without_timers.append((boss, data))

    # Sort by respawn time (nearest first)
    with_timers.sort(key=lambda x: x[1])

    # Trim to 5 if "soon" option
    if option and option.lower() == "soon":
        with_timers = with_timers[:5]

    # Build output
    today_str = datetime.now(ph_tz).strftime("%B %d (%A)")
    lines = [f"**⚔️ Boss Respawn Timers — {today_str}**\n"]

    for boss, respawn_time in with_timers:
        ph_time = respawn_time.astimezone(ph_tz)
        countdown = format_countdown(respawn_time)
        lines.append(
            f"**{ph_time.strftime('%I:%M %p').lstrip('0').lower()}** — {boss.capitalize()} *(in {countdown})*"
        )

    # Show fixed or no-info bosses at the bottom (only in !boss, not !boss soon)
    if not option or option.lower() != "soon":
        lines.append("\n**📌 Fixed / No Info Bosses:**")
        for boss, data in without_timers:
            if data["schedule"]:
                schedule = ", ".join(data["schedule"])
                lines.append(f"🗓️ {boss.capitalize()} — Fixed: {schedule}")
            else:
                lines.append(f"❌ {boss.capitalize()} — No respawn data")

    await ctx.send("\n".join(lines))


# ===========================
# NEXT COMMAND
# ===========================
@bot.command()
async def next(ctx, *, boss: str = None):
    """Show next respawn time and countdown for a boss."""
    if not boss:
        await ctx.send("❌ Please provide a boss name. Example: `!next General Aquleus`")
        return

    boss = boss.lower()
    if boss not in BOSSES:
        await ctx.send(f"❌ Unknown boss: {boss}")
        return

    if boss in respawn_schedule:
        respawn_time = respawn_schedule[boss]
        if respawn_time.tzinfo is None:
            respawn_time = respawn_time.replace(tzinfo=timezone.utc)
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


# ===========================
# DEAD COMMAND
# ===========================
@bot.command(name="dead")
async def dead(ctx, *, args: str = None):
    """
    Log a boss death at the current time.
    Usage:
      !dead <boss name>                (records death as now)
      !dead <boss name> <HH:MM> [AM/PM]  (records death at specific time today)
      !dead <boss name> <YYYY-MM-DD> <HH:MM> [AM/PM]  (records death with date + time)
    """
    if not args:
        await ctx.send("❌ Usage: !dead <boss name> [YYYY-MM-DD] <HH:MM> [AM/PM]")
        return

    parts = args.strip().split()
    if len(parts) < 1:
        await ctx.send("❌ Please provide a boss name.")
        return

    # Try to parse optional date
    killed_date = date.today()
    time_str = None
    boss_name_parts = []

    if len(parts) >= 2:
        # Case: last 2 parts are "HH:MM AM/PM"
        if parts[-1].upper() in ["AM", "PM"]:
            time_str = parts[-2] + " " + parts[-1]
            boss_name_parts = parts[:-2]
        else:
            # Case: last part is "HH:MM"
            try:
                datetime.strptime(parts[-1], "%H:%M")
                time_str = parts[-1]
                boss_name_parts = parts[:-1]
            except ValueError:
                boss_name_parts = parts

    # Check if second-to-last was a date
    if len(parts) >= 3:
        try:
            killed_date = datetime.strptime(parts[-2], "%Y-%m-%d").date()
            if parts[-1].upper() in ["AM", "PM"]:
                time_str = parts[-3] + " " + parts[-1]
                boss_name_parts = parts[:-3]
            else:
                time_str = parts[-1]
                boss_name_parts = parts[:-2]
        except ValueError:
            pass

    boss = " ".join(boss_name_parts).lower()
    if boss not in BOSSES:
        await ctx.send(f"❌ Unknown boss: {boss}")
        return

    # Default: current time if no time provided
    if not time_str:
        killed_time = datetime.now(timezone.utc)
    else:
        try:
            if "AM" in time_str.upper() or "PM" in time_str.upper():
                time_obj = datetime.strptime(time_str.upper(), "%I:%M %p").time()
            else:
                time_obj = datetime.strptime(time_str, "%H:%M").time()
        except ValueError:
            await ctx.send("❌ Invalid time format. Use HH:MM or HH:MM AM/PM.")
            return
        killed_time = datetime.combine(killed_date, time_obj).replace(tzinfo=timezone.utc)

    respawn_time = killed_time + timedelta(hours=BOSSES[boss]["interval"])
    respawn_schedule[boss] = respawn_time
    save_respawn_data()
    asyncio.create_task(announce_boss(boss, respawn_time))

    await ctx.send(
        f"✅ {boss.capitalize()} marked as dead at {format_time(killed_time)} PH "
        f"→ Respawns at {format_time(respawn_time)} PH"
    )


# ===========================
# DEADAT COMMAND (was dead)
# ===========================
@bot.command(name="deadat")
async def deadat(ctx, *, args: str = None):
    if not args:
        await ctx.send("❌ Usage: bulk input or !deadat <boss> <HH:MM> [AM/PM] [YYYY-MM-DD]")
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

    def find_boss(name: str):
        name = name.lower()
        for b in BOSSES.keys():
            if name in b.lower() or b.lower() in name:
                return b
        return None

    for line in lines:
        # Date header
        try:
            parsed_date = datetime.strptime(line.title(), "%B %d").date().replace(year=date.today().year)
            current_date = parsed_date
            continue
        except ValueError:
            pass

        # Match "<time> - <boss>"
        match = re.match(r"^([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM|am|pm)?)\s*-\s*(.+)$", line)
        if not match:
            results.append(f"⚠️ Could not parse line: `{line}`")
            continue

        time_str = match.group(1).strip()
        boss_raw = match.group(2).split("(")[0].strip().lower()
        boss_key = find_boss(boss_raw)
        if not boss_key:
            results.append(f"❌ Unknown boss: {boss_raw}")
            continue

        time_obj = parse_time_str(time_str)
        if not time_obj:
            results.append(f"❌ Invalid time for `{boss_key}`: `{time_str}`")
            continue

        # Localize PH time
        killed_time = ph_tz.localize(datetime.combine(current_date, time_obj))

        interval = BOSSES[boss_key].get("interval")
if interval is None:
    schedule = BOSSES[boss_key].get("schedule")
    if schedule:
        results.append(
            f"🗓️ {boss_key.capitalize()} has a fixed schedule: {', '.join(schedule)}"
        )
    else:
        results.append(
            f"❌ {boss_key.capitalize()} has no respawn interval or fixed schedule configured."
        )
    continue


        respawn_time = killed_time + timedelta(hours=interval)
        respawn_schedule[boss_key] = respawn_time
        save_respawn_data()

        # Call unified announce_boss
        asyncio.create_task(announce_boss(boss_key, respawn_time))

        results.append(f"✅ {boss_key.capitalize()} marked dead → Respawns at {respawn_time.strftime('%Y-%m-%d %I:%M %p PH')}")

    await ctx.send("\n".join(results) if results else "❌ No valid entries found.")


# ===========================
# UP COMMAND
# ===========================
@bot.command(name="up")
async def up(ctx, *, args: str = None):
    """
    Set next spawn time(s). Overwrites any existing stored respawn time.

    Single-line examples:
      !up Venatus 11:38 AM 2025-09-19
      !up Viorent 17:18
      !up Roderick 7:00 pm

    Bulk example:
      !up
      September 19
      1:08 am - Baron Braudmore
      5:04 am - Lady Dalia
      8:13 am - Larba
      11:01 am - Ego
      11:38 am - Titore
      4:54 pm - Undomiel
      4:58 pm - Araneo
      5:01 pm - Livera
      6:27 pm - Wannitas
      7:00 pm - Roderick
      September 20
      10:20 am - Asta
      3:00 pm - Milavy
      5:00 pm - Ringor
    """
    if not args:
        await ctx.send("❌ Usage: single or bulk input. See `!commands` for examples.")
        return

    lines = [ln.strip() for ln in args.splitlines() if ln.strip()]
    results = []

    # --- Helpers ---
    def parse_time_str(s):
        s_clean = s.strip().upper().replace(".", "")
        try:
            if "AM" in s_clean or "PM" in s_clean:
                return datetime.strptime(s_clean, "%I:%M %p").time()
            else:
                return datetime.strptime(s_clean, "%H:%M").time()
        except ValueError:
            return None

    def find_boss(name: str):
        name = name.lower()
        for b in BOSSES.keys():
            if name in b.lower() or b.lower() in name:
                return b
        return None

    # --- Detect if this is single-line mode ---
    if len(lines) == 1 and "-" not in lines[0]:
        parts = lines[0].split()
        respawn_date = None

        # optional YYYY-MM-DD
        if parts and re.match(r'^\d{4}-\d{2}-\d{2}$', parts[-1]):
            try:
                respawn_date = datetime.strptime(parts[-1], "%Y-%m-%d").date()
                parts = parts[:-1]
            except ValueError:
                respawn_date = None

        # detect time token
        time_token = None
        if len(parts) >= 2 and parts[-1].upper() in ("AM", "PM"):
            time_token = parts[-2] + " " + parts[-1]
            boss_parts = parts[:-2]
        elif len(parts) >= 1 and re.match(r'^\d{1,2}:\d{2}$', parts[-1]):
            time_token = parts[-1]
            boss_parts = parts[:-1]
        else:
            await ctx.send("❌ Couldn't find a time. Use `!up <boss> <HH:MM> [AM/PM] [YYYY-MM-DD]` or bulk format.")
            return

        boss_name = " ".join(boss_parts).strip().lower()
        boss_key = find_boss(boss_name)
        if not boss_key:
            await ctx.send(f"❌ Unknown boss: {boss_name}")
            return

        if respawn_date is None:
            respawn_date = date.today()

        time_obj = parse_time_str(time_token)
        if not time_obj:
            await ctx.send(f"❌ Invalid time: `{time_token}`.")
            return

        ph_dt = ph_tz.localize(datetime.combine(respawn_date, time_obj))
        respawn_utc = ph_dt.astimezone(pytz.UTC)

        respawn_schedule[boss_key] = respawn_utc
        save_respawn_data()
        asyncio.create_task(announce_boss(boss_key, respawn_utc))

        await ctx.send(f"✅ Next spawn for **{boss_key.capitalize()}** set to {ph_dt.strftime('%Y-%m-%d %I:%M %p PH')} (overwritten)")
        return

    # --- Bulk mode ---
    current_date = date.today()
    for line in lines:
        # Check for date headers
        parsed_date = None
        try:
            parsed_date = datetime.strptime(line.title(), "%B %d").date().replace(year=date.today().year)
        except ValueError:
            try:
                day_only = int(line)
                today = date.today()
                parsed_date = today.replace(day=day_only)
            except Exception:
                parsed_date = None

        if parsed_date:
            current_date = parsed_date
            continue

        # Match "<time> - <boss>"
        match = re.match(r"^([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM|am|pm)?)\s*-\s*(.+)$", line)
        if not match:
            results.append(f"⚠️ Could not parse line: `{line}`")
            continue

        time_str = match.group(1).strip()
        boss_raw = match.group(2).split("(")[0].strip().lower()

        boss_key = find_boss(boss_raw)
        if not boss_key:
            results.append(f"❌ Unknown boss: {boss_raw}")
            continue

        time_obj = parse_time_str(time_str)
        if not time_obj:
            results.append(f"❌ Invalid time for `{boss_key}`: `{time_str}`")
            continue

        ph_dt = ph_tz.localize(datetime.combine(current_date, time_obj))
        respawn_utc = ph_dt.astimezone(pytz.UTC)

        respawn_schedule[boss_key] = respawn_utc
        save_respawn_data()
        asyncio.create_task(announce_boss(boss_key, respawn_utc))

        results.append(f"✅ {boss_key.capitalize()} set → {ph_dt.strftime('%Y-%m-%d %I:%M %p PH')}")

    await ctx.send("\n".join(results) if results else "❌ No valid entries found.")



# ===========================
# COMMAND HELP
# ===========================
# ==============================
# Updated Commands List
# ==============================
@bot.command(name="cmds")  # Changed from "commands" to "cmds"
async def commands_list(ctx):
    """
    Show all available commands and usage examples for the LordNine bot.
    """
    cmds = [
        ("!dead <boss> [time]", 
         "Log a boss death. Time is optional; formats: HH:MM (24h) or HH:MM AM/PM. Default: now."),
        ("!deadat <bulk input>", 
         "Log one or multiple boss deaths at specific PH times. Supports bulk format:\n"
         "Example:\n"
         "September 19\n"
         "17:01 - Undomiel\n"
         "17:07 - Livera\n"
         "6:51 pm - Wannitas"),
        ("!up <bulk input>", 
         "Set next spawn times for one or multiple bosses. Overwrites existing timers. Supports bulk input."),
        ("!boss [soon]", 
         "Show respawn timers. 'soon' shows next 5 respawns only."),
        ("!next <boss>", 
         "Show next respawn time and countdown for a specific boss."),
        ("!setprealert <minutes>", 
         "Set pre-alert notification time before boss respawns. Default is 10 minutes."),
        ("!cmds", 
         "Show this command list.")
    ]

    # Format the commands neatly
    longest = max(len(c[0]) for c in cmds)
    lines = ["**⚔️ LordNine Bot Commands — Usage**\n"]
    for cmd, desc in cmds:
        desc_lines = desc.split("\n")
        lines.append(f"`{cmd.ljust(longest)}` - {desc_lines[0]}")
        for extra in desc_lines[1:]:
            lines.append(" " * (longest + 5) + extra)

    # Add general notes
    lines.append("\n**📌 Notes:**")
    lines.append("- Times are in PH timezone.")
    lines.append("- @everyone alerts are sent at pre-alert, 10, 5, 1 minute(s), and actual spawn time.")
    lines.append("- Use bulk input for !deadat and !up to log multiple bosses efficiently.")
    lines.append("- The bot automatically resumes saved respawn times after restart.")

    await ctx.send("\n".join(lines))


# ==============================
# Unified Boss Announcement
# ==============================
async def announce_boss(boss, respawn_time):
    """
    Sends @everyone alerts at pre-alert intervals (PRE_ALERT_MINUTES, 10, 5, 1)
    and final respawn.
    """
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print(f"⚠️ Channel {CHANNEL_ID} not found for boss {boss}")
        return

    reminder_minutes = sorted(set([PRE_ALERT_MINUTES, 10, 5, 1]), reverse=True)

    for mins in reminder_minutes:
        alert_time = respawn_time - timedelta(minutes=mins)
        wait_seconds = (alert_time - datetime.utcnow().replace(tzinfo=timezone.utc)).total_seconds()
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
            await channel.send(f"⏳ @everyone **{boss.capitalize()} will respawn in {mins} minute(s)!** Get ready!")

    # Final respawn alert
    wait_seconds = (respawn_time - datetime.utcnow().replace(tzinfo=timezone.utc)).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)

    await channel.send(f"⚔️ @everyone **{boss.capitalize()} has respawned!** Go hunt!")

    # Remove from schedule
    if boss in respawn_schedule:
        del respawn_schedule[boss]
        save_respawn_data()




# ==============================
# ALERT @EVERYONE FOR BOSS
# ==============================

# ==============================
# Alert settings
# ==============================
PRE_ALERT_MINUTES = 10  # default pre-alert
#respawn_schedule = {}   # boss: respawn_time
CHANNEL_ID = 1418187025873375272  # Replace with your Discord channel ID

@bot.command()
async def setprealert(ctx, minutes: int):
    """Set the pre-alert time before a boss respawns."""
    global PRE_ALERT_MINUTES
    if minutes < 1:
        await ctx.send("❌ Pre-alert must be at least 1 minute.")
        return
    PRE_ALERT_MINUTES = minutes
    await ctx.send(f"✅ Pre-alert time set to **{minutes} minutes** before respawn. @everyone will be notified.")


# ==============================
# Public function to schedule a boss
# ==============================
def schedule_boss(boss, respawn_time):
    """Schedule a boss respawn asynchronously."""
    respawn_schedule[boss] = respawn_time
    asyncio.create_task(_announce_boss(boss, respawn_time))

# ==============================
# RUN BOT
# ==============================
bot.run(TOKEN)
