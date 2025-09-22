# bot.py - LordNine Discord Bot (complete, patched)
# Features:
#  - !up (single-line + bulk with date headers)
#  - !dead, !deadat (bulk) with fuzzy boss matching and PH date handling
#  - !boss / !boss soon (separates fixed vs no-info)
#  - !next, !setprealert
#  - !commands / !help (usage + examples)
#  - Alerts: single pre-alert (PRE_ALERT_MINUTES) + final respawn
#  - Prevent duplicate announce tasks (active_tasks)
#  - Persistence to respawn_data.json
#  - Uses environment variables for TOKEN and CHANNEL

import discord
from discord.ext import commands
import asyncio
import json
import os
import re
import pytz
from datetime import datetime, timedelta, date, timezone

# -------------------------
# Timezone
# -------------------------
ph_tz = pytz.timezone("Asia/Manila")

# -------------------------
# Configuration (env)
# -------------------------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # required (set in Railway / environment)
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "1418187025873375272"))  # change default if necessary
BOSS_FILE = "bosses.json"
SAVE_FILE = "respawn_data.json"
PRE_ALERT_MINUTES = int(os.getenv("PRE_ALERT_MINUTES", "10"))

# -------------------------
# Load bosses
# -------------------------
with open(BOSS_FILE, "r") as f:
    BOSSES = json.load(f)  # dict: name -> { "interval": hours or null, "schedule": [...] or null }

# -------------------------
# Helpers
# -------------------------
def parse_time_str(s: str):
    """Parse a time string supporting 'H:MM', 'H:MM AM/PM' (case-insensitive). Returns datetime.time or None."""
    s_clean = s.strip().upper().replace(".", "")
    try:
        if "AM" in s_clean or "PM" in s_clean:
            return datetime.strptime(s_clean, "%I:%M %p").time()
        return datetime.strptime(s_clean, "%H:%M").time()
    except ValueError:
        return None

def find_boss(name: str):
    """
    Fuzzy find a boss key in BOSSES.
    - Case-insensitive
    - Partial matches accepted ("baron" -> "baron braudmore")
    - Prefer exact match; if multiple partial matches return shortest match
    """
    if not name:
        return None
    name = name.lower().strip()
    # Exact match
    for b in BOSSES.keys():
        if name == b.lower():
            return b
    # Partial matches
    matches = [b for b in BOSSES.keys() if name in b.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # choose the shortest match to avoid "lady" matching many
        return min(matches, key=len)
    return None

def format_time(dt: datetime):
    """Format UTC datetime to PH time string 'Mon DD, HH:MM AM'"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ph_tz).strftime("%b %d, %I:%M %p")

def format_countdown(dt: datetime):
    """Return countdown like '2h 15m' or 'Any moment now!'"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    delta = dt - now
    if delta.total_seconds() <= 0:
        return "⏳ Any moment now!"
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

# -------------------------
# Persistence
# -------------------------
def load_respawn_data():
    """Load respawn_schedule from SAVE_FILE (ISO datetimes)."""
    if os.path.exists(SAVE_FILE):
        with open(SAVE_FILE, "r") as f:
            data = json.load(f)
            out = {}
            for boss, iso in data.items():
                dt = datetime.fromisoformat(iso)
                # ensure timezone-aware in UTC
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                out[boss] = dt
            return out
    return {}

def save_respawn_data():
    """Save respawn_schedule to SAVE_FILE as ISO datetimes."""
    with open(SAVE_FILE, "w") as f:
        json.dump({boss: dt.isoformat() for boss, dt in respawn_schedule.items()}, f, indent=4)

# -------------------------
# Bot + state
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)  # disable default help
respawn_schedule = load_respawn_data()  # boss -> datetime (UTC)
active_tasks = {}  # boss -> asyncio.Task (to prevent duplicate announce tasks)

# -------------------------
# Announcement task
# -------------------------
async def announce_boss(boss: str, respawn_time: datetime):
    """
    Sends pre-alert (PRE_ALERT_MINUTES) and final respawn alert.
    This coroutine may be cancelled if a new schedule overrides it.
    """
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print(f"⚠️ Channel {CHANNEL_ID} not found. Announcement for {boss} skipped.")
        # cleanup stored task if exists
        active_tasks.pop(boss, None)
        return

    try:
        # pre-alert
        alert_time = respawn_time - timedelta(minutes=PRE_ALERT_MINUTES)
        wait = (alert_time - datetime.utcnow().replace(tzinfo=timezone.utc)).total_seconds()
        if wait > 0:
            await asyncio.sleep(wait)
            await channel.send(f"⏳ @everyone **{boss.capitalize()} will respawn in {PRE_ALERT_MINUTES} minute(s)!** Get ready!")

        # final
        wait = (respawn_time - datetime.utcnow().replace(tzinfo=timezone.utc)).total_seconds()
        if wait > 0:
            await asyncio.sleep(wait)
        await channel.send(f"⚔️ @everyone **{boss.capitalize()} has respawned!** Go hunt!")

    except asyncio.CancelledError:
        # Task got canceled because schedule changed — exit quietly
        return
    finally:
        # cleanup after completion/cancel
        active_tasks.pop(boss, None)
        if boss in respawn_schedule:
            # If we alerted final, remove; if canceled earlier, respawn_schedule might already have been updated
            # We only remove if current stored respawn_time <= now (meaning it's past).
            now = datetime.utcnow().replace(tzinfo=timezone.utc)
            if respawn_schedule[boss] <= now:
                respawn_schedule.pop(boss, None)
                save_respawn_data()

# -------------------------
# Scheduler helper
# -------------------------
def schedule_boss(boss: str, respawn_time: datetime):
    """
    Cancel existing announce task for boss (if any), persist schedule,
    and start a new announce task.
    """
    # persist
    respawn_schedule[boss] = respawn_time
    save_respawn_data()

    # cancel existing task (prevent duplicates)
    existing = active_tasks.get(boss)
    if existing and not existing.done():
        existing.cancel()

    task = asyncio.create_task(announce_boss(boss, respawn_time))
    active_tasks[boss] = task

# -------------------------
# on_ready - resume tasks
# -------------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (id: {bot.user.id})")
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    for boss, dt in list(respawn_schedule.items()):
        if dt > now:
            schedule_boss(boss, dt)
        else:
            # cleanup expired entries
            respawn_schedule.pop(boss, None)
    save_respawn_data()

# -------------------------
# Commands
# -------------------------
@bot.command(name="commands", aliases=["help"])
async def commands_list(ctx):
    """Show available commands with examples."""
    cmds = [
        ("!boss [soon]", "Show respawn timers (or next 5 with 'soon')"),
        ("!next <boss>", "Show next respawn for a boss"),
        ("!dead <boss> [time]", "Log boss death now or at given PH time. Example: `!dead araneo 5:15 pm`"),
        ("!deadat <bulk>", "Log multiple deaths. Example:\n```\n!deadat\nSeptember 22\n5:01 pm - Undomiel\n5:07 pm - Livera\n```"),
        ("!up <bulk or single>", "Set next spawn times. Bulk example:\n```\n!up\nSeptember 22\n1:15 am - Gareth\n2:24 am - Ego\n```\nSingle example: `!up Venatus 11:38 AM 2025-09-19`"),
        ("!setprealert <minutes>", "Set pre-alert minutes before respawn. Example: `!setprealert 15`"),
        ("!commands / !help", "Show this help message")
    ]
    longest = max(len(c[0]) for c in cmds)
    lines = ["**⚔️ LordNine Bot Commands & Examples**\n"]
    for cmd, desc in cmds:
        lines.append(f"`{cmd.ljust(longest)}` - {desc}")
    lines.append("\n**Notes:**")
    lines.append("- Boss names are case-insensitive and accept partial names (e.g., `baron` -> `baron braudmore`).")
    lines.append("- Times are interpreted as PH (Asia/Manila) time unless you provide a full ISO date/time in single-line mode.")
    lines.append(f"- @everyone alerts: {PRE_ALERT_MINUTES} minutes before + at respawn.")
    await ctx.send("\n".join(lines))

@bot.command(name="boss")
async def boss_cmd(ctx, option: str = None):
    """
    Show respawn timers.
    - !boss → full (timed + fixed + no-info)
    - !boss soon → only next 5 timed + fixed
    """
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    with_timers = []
    fixed_bosses = []
    no_info = []

    # separate bosses
    for b, data in BOSSES.items():
        if b in respawn_schedule:
            dt = respawn_schedule[b]
            if dt > now:
                with_timers.append((b, dt))
            else:
                # expired -> cleanup
                respawn_schedule.pop(b, None)
        else:
            if data.get("schedule"):
                fixed_bosses.append((b, data["schedule"]))
            else:
                no_info.append(b)
    save_respawn_data()

    # sort and trim
    with_timers.sort(key=lambda x: x[1])
    if option and option.lower() == "soon":
        with_timers = with_timers[:5]

    # build message
    today_str = datetime.now(ph_tz).strftime("%B %d (%A)")
    lines = [f"**⚔️ Boss Respawn Timers — {today_str}**\n"]

    if with_timers:
        for b, dt in with_timers:
            ph_time = dt.astimezone(ph_tz)
            lines.append(f"**{ph_time.strftime('%I:%M %p').lstrip('0').lower()}** — {b.capitalize()} *(in {format_countdown(dt)})*")
    else:
        lines.append("✅ No active respawn timers.")

    # Fixed bosses always shown (even in 'soon')
    if fixed_bosses:
        if option and option.lower() == "soon":
            lines.append("\n**⬇️ 📌 FIXED RESPAWN BOSSES BELOW ⬇️**")
        else:
            lines.append("\n**📌 Fixed Bosses:**")
        for b, sched in fixed_bosses:
            lines.append(f"🗓️ {b.capitalize()} — {', '.join(sched)}")

    # No-info only in full view
    if not option or option.lower() != "soon":
        if no_info:
            lines.append("\n**❌ No Info Bosses:**")
            for b in no_info:
                lines.append(f"❌ {b.capitalize()} — No respawn data")

    await ctx.send("\n".join(lines))

@bot.command(name="next")
async def next_cmd(ctx, *, boss: str = None):
    """Show next respawn time and countdown for a boss."""
    if not boss:
        await ctx.send("❌ Please provide a boss name. Example: `!next Venatus`")
        return
    boss_key = find_boss(boss)
    if not boss_key:
        await ctx.send(f"❌ Unknown boss: {boss}")
        return
    if boss_key in respawn_schedule:
        dt = respawn_schedule[boss_key]
        countdown = dt - datetime.utcnow().replace(tzinfo=timezone.utc)
        await ctx.send(f"⏳ **{boss_key.capitalize()}** respawns at {format_time(dt)} PH (in {str(countdown).split('.')[0]})")
    elif BOSSES[boss_key].get("schedule"):
        await ctx.send(f"📅 **{boss_key.capitalize()}** spawns on schedule: {', '.join(BOSSES[boss_key]['schedule'])}")
    else:
        await ctx.send(f"❌ No respawn info for **{boss_key.capitalize()}**")

@bot.command(name="setprealert")
async def setprealert(ctx, minutes: int):
    """Set the pre-alert minutes used by announcements."""
    global PRE_ALERT_MINUTES
    if minutes < 1:
        await ctx.send("❌ Pre-alert must be at least 1 minute.")
        return
    PRE_ALERT_MINUTES = minutes
    await ctx.send(f"✅ Pre-alert set to **{minutes} minute(s)** before respawn. (New tasks will use this value.)")

@bot.command(name="dead")
async def dead_cmd(ctx, *, args: str = None):
    """Mark a boss as dead now or at given PH time. Examples: !dead Venatus, !dead Venatus 1:15 AM"""
    # Accept either "boss" or "boss <time>" or "boss <HH:MM> AM/PM"
    if not args:
        await ctx.send("❌ Usage: `!dead <boss> [HH:MM]` (PH time).")
        return

    parts = args.strip().split()
    # default date = PH today
    killed_date = datetime.now(ph_tz).date()
    time_str = None
    boss_name_parts = []

    # detect time tokens at end: either "HH:MM AM/PM" or "HH:MM"
    if len(parts) >= 2 and parts[-1].upper() in ("AM", "PM"):
        time_str = parts[-2] + " " + parts[-1]
        boss_name_parts = parts[:-2]
    else:
        # try HH:MM at last part
        try:
            datetime.strptime(parts[-1], "%H:%M")
            time_str = parts[-1]
            boss_name_parts = parts[:-1]
        except Exception:
            boss_name_parts = parts

    boss_key = find_boss(" ".join(boss_name_parts))
    if not boss_key:
        await ctx.send(f"❌ Unknown boss: {' '.join(boss_name_parts)}")
        return

    # build killed_time in PH then convert to UTC
    if not time_str:
        killed_ph = datetime.now(ph_tz)
    else:
        time_obj = parse_time_str(time_str)
        if not time_obj:
            await ctx.send("❌ Invalid time format. Use HH:MM or HH:MM AM/PM.")
            return
        killed_ph = ph_tz.localize(datetime.combine(killed_date, time_obj))

    killed_utc = killed_ph.astimezone(pytz.UTC)

    interval = BOSSES[boss_key].get("interval")
    if not interval:
        await ctx.send(f"📅 **{boss_key.capitalize()}** is on a fixed schedule: {BOSSES[boss_key]['schedule']}")
        return

    respawn_time = killed_utc + timedelta(hours=interval)
    schedule_boss(boss_key, respawn_time)

    await ctx.send(f"✅ {boss_key.capitalize()} marked as dead at {killed_ph.strftime('%Y-%m-%d %I:%M %p PH')} → Respawns at {respawn_time.astimezone(ph_tz).strftime('%Y-%m-%d %I:%M %p PH')}")

@bot.command(name="deadat")
async def deadat_cmd(ctx, *, args: str = None):
    """Bulk log boss deaths with optional date headers (same format as !up)."""
    if not args:
        await ctx.send("❌ Usage: `!deadat` followed by lines like:\nSeptember 22\n5:01 pm - Undomiel")
        return

    lines = [ln.strip() for ln in args.splitlines() if ln.strip()]
    results = []
    current_date = datetime.now(ph_tz).date()

    for line in lines:
        # date header detection: "September 22"
        try:
            parsed = datetime.strptime(line.title(), "%B %d").date()
            current_date = parsed.replace(year=datetime.now().year)
            results.append(f"📅 Using date: {current_date.strftime('%B %d, %Y')}")
            continue
        except ValueError:
            pass

        m = re.match(r"^([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM|am|pm)?)\s*-\s*(.+)$", line)
        if not m:
            results.append(f"⚠️ Could not parse line: `{line}`")
            continue

        time_str = m.group(1).strip()
        boss_raw = m.group(2).split("(")[0].strip()
        boss_key = find_boss(boss_raw)
        if not boss_key:
            results.append(f"❌ Unknown boss: {boss_raw}")
            continue

        if not BOSSES[boss_key].get("interval"):
            results.append(f"📅 **{boss_key.capitalize()}** is on a fixed schedule: {BOSSES[boss_key]['schedule']}")
            continue

        time_obj = parse_time_str(time_str)
        if not time_obj:
            results.append(f"❌ Invalid time: `{time_str}`")
            continue

        killed_ph = ph_tz.localize(datetime.combine(current_date, time_obj))
        killed_utc = killed_ph.astimezone(pytz.UTC)
        respawn_time = killed_utc + timedelta(hours=BOSSES[boss_key]["interval"])
        schedule_boss(boss_key, respawn_time)
        results.append(f"✅ {boss_key.capitalize()} logged → Respawns at {respawn_time.astimezone(ph_tz).strftime('%Y-%m-%d %I:%M %p PH')}")

    await ctx.send("\n".join(results) if results else "❌ No valid entries found.")

@bot.command(name="up")
async def up_cmd(ctx, *, args: str = None):
    """
    Set next spawn time(s).
    Supports:
      - Single-line: !up <Boss Name> <HH:MM> [AM/PM] [YYYY-MM-DD]
      - Bulk: multi-line with optional date headers (e.g. 'September 22')
    """
    if not args:
        await ctx.send("❌ Usage: single or bulk input. See `!commands` for examples.")
        return

    lines = [ln.strip() for ln in args.splitlines() if ln.strip()]
    results = []
    current_date = datetime.now(ph_tz).date()

    # Detect single-line mode: exactly one line and no " - " (dash) => single-line command
    if len(lines) == 1 and "-" not in lines[0]:
        parts = lines[0].split()
        respawn_date = None

        # optional YYYY-MM-DD at the end
        if parts and re.match(r"^\d{4}-\d{2}-\d{2}$", parts[-1]):
            try:
                respawn_date = datetime.strptime(parts[-1], "%Y-%m-%d").date()
                parts = parts[:-1]
            except ValueError:
                respawn_date = None

        # find time token (AM/PM handling)
        time_token = None
        if len(parts) >= 2 and parts[-1].upper() in ("AM", "PM"):
            time_token = parts[-2] + " " + parts[-1]
            boss_parts = parts[:-2]
        elif len(parts) >= 1 and re.match(r"^\d{1,2}:\d{2}$", parts[-1]):
            time_token = parts[-1]
            boss_parts = parts[:-1]
        else:
            await ctx.send("❌ Couldn't find a time. Use `!up <boss> <HH:MM> [AM/PM] [YYYY-MM-DD]` or bulk format.")
            return

        boss_name = " ".join(boss_parts).strip()
        boss_key = find_boss(boss_name)
        if not boss_key:
            await ctx.send(f"❌ Unknown boss: {boss_name}")
            return

        if respawn_date is None:
            respawn_date = datetime.now(ph_tz).date()

        time_obj = parse_time_str(time_token)
        if not time_obj:
            await ctx.send(f"❌ Invalid time: `{time_token}`.")
            return

        ph_dt = ph_tz.localize(datetime.combine(respawn_date, time_obj))
        respawn_utc = ph_dt.astimezone(pytz.UTC)
        schedule_boss(boss_key, respawn_utc)
        await ctx.send(f"✅ Next spawn for **{boss_key.capitalize()}** set to {ph_dt.strftime('%Y-%m-%d %I:%M %p PH')} (overwritten)")
        return

    # Bulk mode
    for line in lines:
        # date header detection
        try:
            parsed_date = datetime.strptime(line.title(), "%B %d").date()
            current_date = parsed_date.replace(year=datetime.now().year)
            results.append(f"📅 Using date: {current_date.strftime('%B %d, %Y')}")
            continue
        except ValueError:
            pass

        m = re.match(r"^([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM|am|pm)?)\s*-\s*(.+)$", line)
        if not m:
            results.append(f"⚠️ Could not parse line: `{line}`")
            continue

        time_str = m.group(1).strip()
        boss_raw = m.group(2).split("(")[0].strip()
        boss_key = find_boss(boss_raw)
        if not boss_key:
            results.append(f"❌ Unknown boss: {boss_raw}")
            continue

        time_obj = parse_time_str(time_str)
        if not time_obj:
            results.append(f"❌ Invalid time: `{time_str}`")
            continue

        ph_dt = ph_tz.localize(datetime.combine(current_date, time_obj))
        respawn_utc = ph_dt.astimezone(pytz.UTC)
        schedule_boss(boss_key, respawn_utc)
        results.append(f"✅ {boss_key.capitalize()} set → {ph_dt.strftime('%Y-%m-%d %I:%M %p PH')}")

    await ctx.send("\n".join(results) if results else "❌ No valid entries found.")

# -------------------------
# Run bot
# -------------------------
if not TOKEN:
    print("ERROR: DISCORD_BOT_TOKEN not set in environment. Exiting.")
else:
    bot.run(TOKEN)