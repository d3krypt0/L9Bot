# bot.py — Patched full version
# - PH timezone-aware
# - Multi-server / multi-channel support
# - !up requires a date header
# - All requested commands present
# - Uses UTC internally for storage & scheduling

import os
import re
import json
import asyncio
import pytz
from datetime import datetime, timedelta, timezone
import discord
from discord.ext import commands
import calendar


# ------------------------------
# Configuration
# ------------------------------

# Discord token (set on Railway as DISCORD_BOT_TOKEN)
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Multi-channel support: set DISCORD_CHANNEL_IDS in Railway as CSV of channel IDs
# Example: 1418170908039712810,1418170908039712820
RAW_CHANNEL_IDS = os.getenv("DISCORD_CHANNEL_IDS", "")
CHANNEL_IDS = [int(x.strip()) for x in RAW_CHANNEL_IDS.split(",") if x.strip().isdigit()]

# Active channels set (configured + auto-discovered)
active_channels = set(CHANNEL_IDS)

# Philippines timezone
ph_tz = pytz.timezone("Asia/Manila")

# Files
BOSSES_FILE = "bosses.json"
DATA_FILE = "respawns.json"

# Default pre-alert (minutes before respawn)
PRE_ALERT_MINUTES = 10

# ------------------------------
# Load boss definitions
# ------------------------------
if not os.path.exists(BOSSES_FILE):
    raise FileNotFoundError(f"{BOSSES_FILE} not found — please ensure your bosses.json is present.")

with open(BOSSES_FILE, "r", encoding="utf-8") as f:
    BOSSES = json.load(f)

# ------------------------------
# In-memory respawn schedule (boss -> UTC datetime)
# ------------------------------
respawn_schedule = {}

# ------------------------------
# Helpers
# ------------------------------

def save_respawn_data():
    """Persist respawn_schedule to disk (ISO format)."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({boss: dt.isoformat() for boss, dt in respawn_schedule.items()}, f)

def load_respawn_data():
    """Load persisted respawn schedule (if present) and normalize to UTC-aware datetimes."""
    if not os.path.exists(DATA_FILE):
        return
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    for boss, iso in raw.items():
        try:
            dt = datetime.fromisoformat(iso)
            # If it's naive (no tz), assume UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.UTC)
            # normalize to pytz.UTC
            dt = dt.astimezone(pytz.UTC)
            respawn_schedule[boss] = dt
        except Exception:
            # skip broken entries
            print(f"⚠️ Could not parse saved time for {boss}: {iso}")

def format_countdown(respawn_time):
    """Return countdown like '2h 15m' or '⏳ Any moment now!'."""
    now = datetime.utcnow().replace(tzinfo=pytz.UTC)
    remaining = (respawn_time - now).total_seconds()
    if remaining <= 0:
        return "⏳ Any moment now!"
    minutes = int(remaining // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days: parts.append(f"{days}d")
    if hours: parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "0m"

def find_boss(query: str):
    """
    Find a boss by partial/case-insensitive match.
    Returns the boss key from BOSSES (lowercase as in JSON) or None.
    """
    if not query:
        return None
    q = query.strip().lower()
    # exact
    if q in BOSSES:
        return q
    matches = [b for b in BOSSES.keys() if q in b.lower()]
    if len(matches) == 1:
        return matches[0]
    # no unique match
    return None

def parse_date_header(line: str):
    """
    Parse date header like "September 22" and return a date() with the current year applied.
    Returns None if it doesn't match.
    """
    try:
        parsed = datetime.strptime(line.strip(), "%B %d").date()  # year is 1900 initially
        # attach current PH year (user likely means this year)
        current_year = datetime.now(ph_tz).year
        return parsed.replace(year=current_year)
    except Exception:
        return None

def parse_time_to_utc(time_str: str, base_date):
    """
    Parse a time string like "5:04 am", "17:07", "6:51 pm" with given base_date (a date object),
    return a timezone-aware UTC datetime (pytz.UTC) or None on failure.
    """
    if not time_str or not base_date:
        return None
    s = time_str.strip().lower()
    # Regex: hh:mm optional am/pm
    m = re.match(r"^(\d{1,2}):(\d{2})(?:\s*(am|pm))?$", s, re.I)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2))
    ampm = (m.group(3) or "").lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    # build naive local dt using base_date
    try:
        naive = datetime(year=base_date.year, month=base_date.month, day=base_date.day,
                         hour=hour, minute=minute)
    except Exception:
        return None
    # localize properly using pytz
    try:
        local_dt = ph_tz.localize(naive)
    except Exception:
        # fallback (shouldn't usually happen)
        local_dt = naive.replace(tzinfo=ph_tz)
    # convert to UTC for storage/comparison
    return local_dt.astimezone(pytz.UTC)

def get_active_channel_objs():
    """
    Resolve active_channels IDs to channel objects (bot.get_channel).
    Returns list of channel objects (may be empty).
    """
    chans = []
    for cid in list(active_channels):
        ch = bot.get_channel(cid)
        if ch is not None:
            chans.append(ch)
        else:
            # leave the id in the set but it's unresolved for now
            print(f"⚠️ active channel id {cid} not resolvable right now (bot.get_channel returned None).")
    return chans

def get_next_fixed_schedule(boss, schedules):
    """Return the next datetime for a fixed-schedule boss in PH timezone."""
    now = datetime.now(ph_tz)
    upcoming_times = []

    for sched in schedules:
        # Example: "Monday 11:30"
        day_str, time_str = sched.split()
        target_day = list(calendar.day_name).index(day_str)
        target_time = datetime.strptime(time_str, "%H:%M").time()

        # Start from today
        candidate = now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)

        # Roll forward to the correct weekday
        days_ahead = (target_day - candidate.weekday()) % 7
        candidate = candidate + timedelta(days=days_ahead)

        # If time already passed today, push to next week
        if candidate <= now:
            candidate = candidate + timedelta(days=7)

        upcoming_times.append(candidate)

    # Return the soonest upcoming schedule
    return min(upcoming_times).astimezone(pytz.UTC)

# ------------------------------
# Bot initialization
# ------------------------------

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ------------------------------
# Scheduler + Announcer
# ------------------------------

def schedule_boss(boss: str, respawn_utc: datetime):
    """
    Wrapper that schedules announce_boss to run in the background.
    Expects respawn_utc to be an aware datetime in UTC (pytz.UTC).
    """
    # ensure stored
    respawn_schedule[boss] = respawn_utc
    save_respawn_data()
    # create the background task
    asyncio.create_task(announce_boss(boss, respawn_utc))

async def announce_boss(boss: str, respawn_utc: datetime):
    """
    Send pre-alert and final respawn messages to all active channels.
    Pre-alert only (single interval configured by PRE_ALERT_MINUTES).
    """
    # ensure time is UTC-aware
    if respawn_utc.tzinfo is None:
        respawn_utc = respawn_utc.replace(tzinfo=pytz.UTC)
    now = datetime.utcnow().replace(tzinfo=pytz.UTC)

    # pre-alert
    alert_time = respawn_utc - timedelta(minutes=PRE_ALERT_MINUTES)
    wait = (alert_time - now).total_seconds()
    if wait > 0:
        await asyncio.sleep(wait)
        for ch in get_active_channel_objs():
            try:
                await ch.send(f"⏳ @everyone **{boss.capitalize()} will respawn in {PRE_ALERT_MINUTES} minutes!** Get ready!")
            except Exception as e:
                print(f"⚠️ Failed to send pre-alert to {ch.id}: {e}")

    # final alert
    now = datetime.utcnow().replace(tzinfo=pytz.UTC)
    wait = (respawn_utc - now).total_seconds()
    if wait > 0:
        await asyncio.sleep(wait)
    for ch in get_active_channel_objs():
        try:
            await ch.send(f"⚔️ @everyone **{boss.capitalize()} has respawned!** Go hunt!")
        except Exception as e:
            print(f"⚠️ Failed to send final alert to {ch.id}: {e}")

    # cleanup saved schedule (boss no longer pending)
    if boss in respawn_schedule:
        del respawn_schedule[boss]
        save_respawn_data()

# ------------------------------
# Events
# ------------------------------

@bot.event
async def on_ready():
    """
    Called when bot connects. Discover channels if none configured and resume saved schedules.
    """
    # Print identity
    print(f"✅ Logged in as {bot.user} (id={bot.user.id})")

    # Auto-discover a default channel per guild if user did not configure CHANNEL_IDS
    if not CHANNEL_IDS:
        for guild in bot.guilds:
            # pick first text channel where the bot can send messages
            for ch in guild.text_channels:
                perms = ch.permissions_for(guild.me)
                if perms.send_messages:
                    active_channels.add(ch.id)
                    print(f"📡 Auto-added channel {ch.name} ({ch.id}) from guild {guild.name}")
                    break

    # Resume tasks for saved respawns (future only)
    now = datetime.utcnow().replace(tzinfo=pytz.UTC)
    for boss, dt in list(respawn_schedule.items()):
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.UTC)
        if dt > now:
            # call wrapper that schedules announce_boss
            schedule_boss(boss, dt)
        else:
            # expired entry — clean up
            del respawn_schedule[boss]
            save_respawn_data()

# ------------------------------
# Commands (all required present)
# ------------------------------

# 1) !up — require date header, then multiple "time - Boss" lines
@bot.command(name="up")
async def up(ctx, *, bulk: str = None):
    """
    Set next spawn time(s). **First line MUST be a date header** like: 'September 22'
    Following lines: '5:04 am - Baron Braudmore'
    Example:
      !up
      September 22
      1:08 am - Baron Braudmore
      5:04 am - Lady Dalia
    """
    if not bulk:
        await ctx.send("❌ Usage: paste bulk text with a date header on the first line. Example:\n`September 22` then lines `1:08 am - Boss`")
        return

    lines = [ln.strip() for ln in bulk.splitlines() if ln.strip()]
    if not lines:
        await ctx.send("❌ No lines found in input.")
        return

    # parse date header (first non-empty line)
    base_date = parse_date_header(lines[0])
    if not base_date:
        await ctx.send("❌ First line must be a date like `September 22` (no year).")
        return

    results = []
    for line in lines[1:]:
        if "-" not in line:
            results.append(f"⚠️ Could not parse line: `{line}`")
            continue
        time_part, boss_part = [p.strip() for p in line.split("-", 1)]
        boss_key = find_boss(boss_part)
        if not boss_key:
            results.append(f"❌ Unknown boss: `{boss_part}`")
            continue

        # parse time + produce UTC datetime (the given time is expected to be PH local time)
        respawn_utc = parse_time_to_utc(time_part, base_date)
        if not respawn_utc:
            results.append(f"❌ Invalid time: `{time_part}`")
            continue

        # If boss is fixed schedule (no interval), inform user and skip
        if BOSSES.get(boss_key, {}).get("schedule"):
            results.append(f"🗓️ {boss_key.capitalize()} is fixed-schedule: {', '.join(BOSSES[boss_key]['schedule'])}")
            continue

        # store & schedule
        respawn_schedule[boss_key] = respawn_utc
        save_respawn_data()
        schedule_boss(boss_key, respawn_utc)
        ph_str = respawn_utc.astimezone(ph_tz).strftime("%Y-%m-%d %I:%M %p PH")
        results.append(f"✅ {boss_key.capitalize()} set → {ph_str}")

    await ctx.send("\n".join(results) if results else "❌ No valid entries found.")

# 2) !dead — single boss dead (optional time). If time omitted, use now PH
@bot.command(name="dead")
async def dead(ctx, *, args: str = None):
    """
    Log a boss death.
    Usage:
      !dead <boss>              -> mark dead at NOW (PH)
      !dead <boss> <HH:MM>      -> mark dead at given PH time today
      !dead <boss> <HH:MM AM/PM>
    Boss name can be partial (e.g. 'baron' -> 'baron braudmore') if unambiguous.
    """
    if not args:
        await ctx.send("❌ Usage: `!dead <boss> [HH:MM]`")
        return

    # split boss name and optional time (split from the end)
    # Accept: "lady dalia 5:30 pm" or "venatus 01:15"
    m = re.match(r"^(.+?)(?:\s+(\d{1,2}:\d{2}(?:\s*(?:am|pm))?))?$", args.strip(), re.I)
    if not m:
        await ctx.send("❌ Couldn't parse input. Use `!dead <boss> [HH:MM]`")
        return
    boss_part = m.group(1).strip()
    time_part = (m.group(2) or "").strip() or None

    boss_key = find_boss(boss_part)
    if not boss_key:
        await ctx.send(f"❌ Unknown boss: `{boss_part}`")
        return

    # If fixed schedule boss, show schedule
    if BOSSES.get(boss_key, {}).get("schedule"):
        await ctx.send(f"🗓️ {boss_key.capitalize()} is fixed-schedule: {', '.join(BOSSES[boss_key]['schedule'])}")
        return

    # Determine killed time in PH (then convert to UTC)
    if time_part:
        killed_utc = parse_time_to_utc(time_part, datetime.now(ph_tz).date())
        if not killed_utc:
            await ctx.send("❌ Invalid time format. Use `HH:MM` or `HH:MM AM/PM`.")
            return
    else:
        # now in PH -> convert to UTC
        killed_utc = datetime.now(ph_tz).astimezone(pytz.UTC)

    interval = BOSSES[boss_key].get("interval")
    if not interval:
        await ctx.send(f"🗓️ {boss_key.capitalize()} is fixed-schedule: {', '.join(BOSSES[boss_key]['schedule'])}")
        return

    respawn_utc = killed_utc + timedelta(hours=interval)
    respawn_schedule[boss_key] = respawn_utc
    save_respawn_data()
    schedule_boss(boss_key, respawn_utc)

    killed_ph_str = killed_utc.astimezone(ph_tz).strftime("%Y-%m-%d %I:%M %p PH")
    respawn_ph_str = respawn_utc.astimezone(ph_tz).strftime("%Y-%m-%d %I:%M %p PH")
    await ctx.send(f"✅ {boss_key.capitalize()} marked dead at {killed_ph_str} → Respawns at {respawn_ph_str}")

# 3) !deadat — bulk dead (can accept a date header optionally)
@bot.command(name="deadat")
async def deadat(ctx, *, bulk: str = None):
    """
    Bulk log boss deaths.
    Accepts optional date header on top:
      September 22
      17:01 - Undomiel
      17:07 - Livera
    If no date header, defaults to today (PH).
    """
    if not bulk:
        await ctx.send("❌ Usage: paste bulk text. First line may be a date header (e.g. 'September 22').")
        return

    lines = [ln.strip() for ln in bulk.splitlines() if ln.strip()]
    if not lines:
        await ctx.send("❌ No lines found.")
        return

    base_date = None
    # If first line is date header, parse it
    d = parse_date_header(lines[0])
    if d:
        base_date = d
        lines = lines[1:]  # remaining lines are times
    else:
        base_date = datetime.now(ph_tz).date()

    results = []
    for line in lines:
        if "-" not in line:
            results.append(f"⚠️ Could not parse line: `{line}`")
            continue
        time_part, boss_part = [p.strip() for p in line.split("-", 1)]
        boss_key = find_boss(boss_part)
        if not boss_key:
            results.append(f"❌ Unknown boss: `{boss_part}`")
            continue

        if BOSSES.get(boss_key, {}).get("schedule"):
            results.append(f"🗓️ {boss_key.capitalize()} is fixed-schedule: {', '.join(BOSSES[boss_key]['schedule'])}")
            continue

        killed_utc = parse_time_to_utc(time_part, base_date)
        if not killed_utc:
            results.append(f"❌ Invalid time: `{time_part}`")
            continue

        interval = BOSSES[boss_key].get("interval")
        if not interval:
            results.append(f"🗓️ {boss_key.capitalize()} is fixed-schedule.")
            continue

        respawn_utc = killed_utc + timedelta(hours=interval)
        respawn_schedule[boss_key] = respawn_utc
        save_respawn_data()
        schedule_boss(boss_key, respawn_utc)
        ph_str = respawn_utc.astimezone(ph_tz).strftime("%Y-%m-%d %I:%M %p PH")
        results.append(f"✅ {boss_key.capitalize()} set → {ph_str}")

    await ctx.send("\n".join(results) if results else "❌ No valid entries found.")

# 4) !boss & !boss soon — list timers (with separation of fixed/no-info)
@bot.command(name="boss")
async def boss_list(ctx, option: str = None):
    """
    Show respawn timers.
      !boss      -> show all (active + fixed/no info)
      !boss soon -> show next 5 upcoming respawns only + separate fixed list
    """
    now = datetime.utcnow().replace(tzinfo=pytz.UTC)
    with_timers = []
    without_timers = []

    for boss, data in BOSSES.items():
        if boss in respawn_schedule:
            rt = respawn_schedule[boss]
            if rt and rt.tzinfo is None:
                rt = rt.replace(tzinfo=pytz.UTC)
            if rt > now:
                with_timers.append((boss, rt))
            else:
                # expired: remove entry
                del respawn_schedule[boss]
                save_respawn_data()
        else:
            without_timers.append((boss, data))

    # sort upcoming
    with_timers.sort(key=lambda x: x[1])
    if option and option.lower() == "soon":
        with_timers = with_timers[:5]

    today_str = datetime.now(ph_tz).strftime("%B %d (%A)")
    lines = [f"**⚔️ Boss Respawn Timers — {today_str}**\n"]

    for boss, rt in with_timers:
        ph_time = rt.astimezone(ph_tz)
        countdown = format_countdown(rt)
        lines.append(f"**{ph_time.strftime('%I:%M %p').lstrip('0').lower()}** — {boss.capitalize()} *(in {countdown})*")

    # fixed/no-info (only when not !boss soon)
    if not option or option.lower() != "soon":
        fixed_lines = []
        noinfo_lines = []
        for boss, data in without_timers:
            sched = data.get("schedule")
            if sched:
                fixed_lines.append(f"🗓️ {boss.capitalize()} — Fixed: {', '.join(sched)}")
            else:
                noinfo_lines.append(f"❌ {boss.capitalize()} — No respawn data")
        if fixed_lines:
            lines.append("\n**📌 Fixed Bosses:**")
            lines.extend(fixed_lines)
        if noinfo_lines:
            lines.append("\n**❓ No Info Bosses:**")
            lines.extend(noinfo_lines)

    await ctx.send("\n".join(lines))

# 5) !next — show next respawn for a boss
@bot.command(name="next")
async def next_cmd(ctx, *, boss_raw: str = None):
    if not boss_raw:
        await ctx.send("❌ Usage: `!next <boss>`")
        return
    boss_key = find_boss(boss_raw)
    if not boss_key:
        await ctx.send(f"❌ Unknown boss: `{boss_raw}`")
        return
    if boss_key not in respawn_schedule:
        if BOSSES[boss_key].get("schedule"):
            await ctx.send(f"📅 {boss_key.capitalize()} is fixed-schedule: {', '.join(BOSSES[boss_key]['schedule'])}")
        else:
            await ctx.send(f"❌ No respawn info for **{boss_key.capitalize()}**")
        return
    rt = respawn_schedule[boss_key]
    ph_str = rt.astimezone(ph_tz).strftime("%Y-%m-%d %I:%M %p PH")
    await ctx.send(f"⏳ **{boss_key.capitalize()}** respawns at {ph_str} (in {format_countdown(rt)})")

# 6) !setprealert — set pre-alert minutes
@bot.command(name="setprealert")
async def setprealert(ctx, minutes: int):
    global PRE_ALERT_MINUTES
    if minutes < 1:
        await ctx.send("❌ Pre-alert must be at least 1 minute.")
        return
    PRE_ALERT_MINUTES = minutes
    await ctx.send(f"✅ Pre-alert set to {minutes} minute(s).")

# 7) !commands — show help
@bot.command(name="commands")
async def commands_list(ctx):
    cmds = [
        ("!boss [soon]", "Show respawn timers. 'soon' shows next 5 respawns only."),
        ("!next <boss>", "Show next respawn time and countdown for a specific boss."),
        ("!up", "Bulk set next spawn times. **First line must be date (e.g. 'September 22')**."),
        ("!dead <boss> [time]", "Mark a boss dead (uses PH time). Time optional; formats: HH:MM or HH:MM AM/PM."),
        ("!deadat <bulk>", "Log one or multiple boss deaths at specific PH times. Supports date header."),
        ("!setprealert <minutes>", "Set pre-alert notification time before boss respawns."),
        ("!testchannels", "Show which channels the bot can send to."),
        ("!commands", "Show this list"),
    ]
    longest = max(len(c[0]) for c in cmds)
    lines = ["**⚔️ LordNine Bot Commands — Usage**\n"]
    for cmd, desc in cmds:
        lines.append(f"`{cmd.ljust(longest)}` - {desc}")
    lines.append("\n**Notes:** Times are PH timezone. Use full month name for !up date header (e.g. `September 22`).")
    await ctx.send("\n".join(lines))

# 8) !testchannels — debug active channels
@bot.command(name="testchannels")
async def testchannels(ctx):
    ok = []
    fail = []
    for cid in list(active_channels):
        ch = bot.get_channel(cid)
        if ch:
            try:
                await ch.send("✅ Test message from LordNine bot")
                ok.append(f"{ch.guild.name} → #{ch.name}")
            except Exception as e:
                fail.append(f"{cid} (send failed: {e})")
        else:
            fail.append(f"{cid} (unresolvable)")
    await ctx.send(f"✅ OK: {', '.join(ok) or 'none'}\n❌ Fail: {', '.join(fail) or 'none'}")

# ------------------------------
# Startup
# ------------------------------

# Load saved schedule then start bot
load_respawn_data()
bot.run(TOKEN)