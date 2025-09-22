import discord
from discord.ext import commands
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
import pytz

# ==============================
# CONFIGURATION
# ==============================

TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # ✅ Keep your token safe in environment variables
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", 0))  # Channel where announcements will go
DATA_FILE = "respawn_data.json"  # File where respawn timers will be saved
BOSSES_FILE = "bosses.json"      # File containing boss list, intervals, and schedules
PRE_ALERT_MINUTES = 10           # Time before respawn to send a warning
ph_tz = pytz.timezone("Asia/Manila")  # Philippine timezone

# ==============================
# BOT INITIALIZATION
# ==============================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ==============================
# LOAD BOSSES DATA
# ==============================

with open(BOSSES_FILE, "r", encoding="utf-8") as f:
    BOSSES = json.load(f)

# This dict stores active respawn timers
respawn_schedule = {}

# ==============================
# HELPER FUNCTIONS
# ==============================

def save_respawn_data():
    """Save respawn schedule to JSON file so it persists after restart."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {boss: time.isoformat() for boss, time in respawn_schedule.items()},
            f,
            indent=2,
        )

def load_respawn_data():
    """Load respawn schedule from JSON file when bot starts."""
    global respawn_schedule
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            for boss, timestr in data.items():
                try:
                    respawn_schedule[boss] = datetime.fromisoformat(timestr)
                except Exception:
                    print(f"⚠️ Skipping invalid time for {boss}")

def format_countdown(respawn_time):
    """Return human-readable countdown until respawn."""
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    delta = respawn_time - now
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "⏳ Any moment now!"
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    days, hours = divmod(hours, 24)

    if days > 0:
        return f"{days}d {hours}h {mins}m"
    elif hours > 0:
        return f"{hours}h {mins}m"
    else:
        return f"{mins}m"

def resolve_boss_name(query):
    """
    Resolve boss names case-insensitively and allow partial matches.
    Example: 'baron' → 'baron braudmore'
    """
    query = query.lower()
    matches = [boss for boss in BOSSES if query in boss.lower()]
    if len(matches) == 1:
        return matches[0]
    return None  # Ambiguous or not found

# ==============================
# ANNOUNCEMENT HANDLER
# ==============================

async def announce_boss(boss, respawn_time):
    """
    Sends @everyone alerts at:
    - 10 minutes before respawn
    - Actual respawn
    """
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print(f"⚠️ Channel {CHANNEL_ID} not found for boss {boss}")
        return

    # --- Pre-alert (10 minutes before) ---
    alert_time = respawn_time - timedelta(minutes=PRE_ALERT_MINUTES)
    wait_seconds = (alert_time - datetime.utcnow().replace(tzinfo=timezone.utc)).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
        await channel.send(f"⏳ @everyone **{boss.capitalize()} will respawn in {PRE_ALERT_MINUTES} minutes!** Get ready!")

    # --- Final respawn alert ---
    wait_seconds = (respawn_time - datetime.utcnow().replace(tzinfo=timezone.utc)).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)
    await channel.send(f"⚔️ @everyone **{boss.capitalize()} has respawned!** Go hunt!")

    # Remove boss from schedule after respawn
    if boss in respawn_schedule:
        del respawn_schedule[boss]
        save_respawn_data()

# ==============================
# BOT COMMANDS
# ==============================

@bot.event
async def on_ready():
    """Triggered when the bot successfully connects to Discord."""
    print(f"✅ Logged in as {bot.user}")
    load_respawn_data()

# ------------------------------
# !dead COMMAND
# ------------------------------
@bot.command(name="dead")
async def dead(ctx, boss: str, time: str = None):
    """
    Log a boss death.
    Usage: !dead <boss> [time]
    Example: !dead venatus 01:15 am
    """
    boss_name = resolve_boss_name(boss)
    if not boss_name:
        await ctx.send(f"⚠️ Unknown boss: {boss}")
        return

    # Use provided time or current time
    killed_time = datetime.now(ph_tz)
    if time:
        try:
            killed_time = datetime.strptime(time, "%I:%M %p").replace(
                tzinfo=ph_tz,
                year=killed_time.year,
                month=killed_time.month,
                day=killed_time.day,
            )
        except ValueError:
            try:
                killed_time = datetime.strptime(time, "%H:%M").replace(
                    tzinfo=ph_tz,
                    year=killed_time.year,
                    month=killed_time.month,
                    day=killed_time.day,
                )
            except ValueError:
                await ctx.send("⚠️ Invalid time format. Use HH:MM or HH:MM AM/PM.")
                return

    interval = BOSSES[boss_name]["interval"]
    if interval:
        respawn_time = killed_time + timedelta(hours=interval)
        respawn_schedule[boss_name] = respawn_time.astimezone(timezone.utc)
        save_respawn_data()
        await ctx.send(f"✅ {boss_name.capitalize()} set → {respawn_time.strftime('%Y-%m-%d %I:%M %p')} PH")
        bot.loop.create_task(announce_boss(boss_name, respawn_schedule[boss_name]))
    else:
        # Fixed schedule boss
        schedule = BOSSES[boss_name]["schedule"]
        await ctx.send(f"🗓️ {boss_name.capitalize()} is fixed spawn → {', '.join(schedule)}")

# ------------------------------
# !deadat COMMAND
# ------------------------------
@bot.command(name="deadat")
async def deadat(ctx, *, bulk_input: str):
    """
    Log multiple boss deaths in one command.
    Example:
    !deadat
    1:15 am - Gareth
    2:24 am - Ego
    """
    lines = bulk_input.strip().split("\n")
    for line in lines:
        if "-" not in line:
            continue
        time_str, boss = [x.strip() for x in line.split("-", 1)]
        boss_name = resolve_boss_name(boss)
        if not boss_name:
            await ctx.send(f"⚠️ Unknown boss: {boss}")
            continue

        killed_time = datetime.now(ph_tz)
        try:
            killed_time = datetime.strptime(time_str, "%I:%M %p").replace(
                tzinfo=ph_tz,
                year=killed_time.year,
                month=killed_time.month,
                day=killed_time.day,
            )
        except ValueError:
            try:
                killed_time = datetime.strptime(time_str, "%H:%M").replace(
                    tzinfo=ph_tz,
                    year=killed_time.year,
                    month=killed_time.month,
                    day=killed_time.day,
                )
            except ValueError:
                await ctx.send(f"⚠️ Invalid time: {time_str}")
                continue

        interval = BOSSES[boss_name]["interval"]
        if interval:
            respawn_time = killed_time + timedelta(hours=interval)
            respawn_schedule[boss_name] = respawn_time.astimezone(timezone.utc)
            save_respawn_data()
            await ctx.send(f"✅ {boss_name.capitalize()} set → {respawn_time.strftime('%Y-%m-%d %I:%M %p')} PH")
            bot.loop.create_task(announce_boss(boss_name, respawn_schedule[boss_name]))
        else:
            schedule = BOSSES[boss_name]["schedule"]
            await ctx.send(f"🗓️ {boss_name.capitalize()} is fixed spawn → {', '.join(schedule)}")

# ------------------------------
# !up COMMAND
# ------------------------------
@bot.command(name="up")
async def up(ctx, *, bulk_input: str):
    """
    Manually set respawn times for bosses.
    Usage:
    !up
    5:00 pm - Undomiel
    6:00 pm - Livera
    """
    lines = bulk_input.strip().split("\n")
    for line in lines:
        if "-" not in line:
            continue
        time_str, boss = [x.strip() for x in line.split("-", 1)]
        boss_name = resolve_boss_name(boss)
        if not boss_name:
            await ctx.send(f"⚠️ Unknown boss: {boss}")
            continue

        try:
            respawn_time = datetime.strptime(time_str, "%I:%M %p").replace(
                tzinfo=ph_tz,
                year=datetime.now(ph_tz).year,
                month=datetime.now(ph_tz).month,
                day=datetime.now(ph_tz).day,
            )
        except ValueError:
            try:
                respawn_time = datetime.strptime(time_str, "%H:%M").replace(
                    tzinfo=ph_tz,
                    year=datetime.now(ph_tz).year,
                    month=datetime.now(ph_tz).month,
                    day=datetime.now(ph_tz).day,
                )
            except ValueError:
                await ctx.send(f"⚠️ Invalid time: {time_str}")
                continue

        respawn_schedule[boss_name] = respawn_time.astimezone(timezone.utc)
        save_respawn_data()
        await ctx.send(f"✅ {boss_name.capitalize()} set → {respawn_time.strftime('%Y-%m-%d %I:%M %p')} PH")
        bot.loop.create_task(announce_boss(boss_name, respawn_schedule[boss_name]))

# ------------------------------
# !boss COMMAND
# ------------------------------
@bot.command(name="boss")
async def boss(ctx, option: str = None):
    """
    Show respawn timers for bosses.
    Usage:
    !boss        → Show all bosses
    !boss soon   → Show next 5 respawns
    """
    with_timers = []
    fixed_bosses = []
    no_info_bosses = []

    now = datetime.utcnow().replace(tzinfo=timezone.utc)

    # Separate bosses by type
    for boss, data in BOSSES.items():
        if boss in respawn_schedule:
            respawn_time = respawn_schedule[boss]
            if respawn_time > now:
                with_timers.append((boss, respawn_time))
        elif data["schedule"]:
            fixed_bosses.append((boss, data["schedule"]))
        else:
            no_info_bosses.append(boss)

    # Sort upcoming spawns
    with_timers.sort(key=lambda x: x[1])

    # Limit to 5 if "soon"
    if option and option.lower() == "soon":
        with_timers = with_timers[:5]

    today_str = datetime.now(ph_tz).strftime("%B %d (%A)")
    lines = [f"**⚔️ Boss Respawn Timers — {today_str}**\n"]

    for boss, respawn_time in with_timers:
        ph_time = respawn_time.astimezone(ph_tz)
        countdown = format_countdown(respawn_time)
        lines.append(f"**{ph_time.strftime('%I:%M %p').lstrip('0').lower()}** — {boss.capitalize()} *(in {countdown})*")

    if not option or option.lower() != "soon":
        lines.append("\n**📌 Fixed Respawn Bosses:**")
        for boss, schedule in fixed_bosses:
            lines.append(f"🗓️ {boss.capitalize()} — Fixed: {', '.join(schedule)}")

        lines.append("\n**❌ No Info Bosses:**")
        for boss in no_info_bosses:
            lines.append(f"❌ {boss.capitalize()} — No respawn data")

    await ctx.send("\n".join(lines))

# ------------------------------
# !commands COMMAND
# ------------------------------
@bot.command(name="commands")
async def commands_list(ctx):
    """
    Show all available commands for the bot.
    """
    cmds = [
        ("!dead <boss> [time]", "Mark a boss as dead (time optional: HH:MM or HH:MM AM/PM)."),
        ("!deadat <bulk>", "Log multiple deaths with format 'time - boss'."),
        ("!up <bulk>", "Manually set respawn times."),
        ("!boss [soon]", "Show respawn timers. Use 'soon' to show next 5."),
        ("!commands", "Show this command list."),
    ]

    longest = max(len(c[0]) for c in cmds)
    lines = ["**📜 LordNine Bot Commands**\n"]
    for cmd, desc in cmds:
        lines.append(f"`{cmd.ljust(longest)}` - {desc}")
    lines.append("\n📌 Notes:")
    lines.append(f"- Times are in **PH timezone**.")
    lines.append(f"- The bot alerts **@everyone 10 minutes before** and **at respawn**.")
    await ctx.send("\n".join(lines))

# ==============================
# START BOT
# ==============================
bot.run(TOKEN)
