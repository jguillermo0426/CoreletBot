import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
from typing import Optional
from datetime import datetime

def format_due_date(due_date_str: str) -> str:
    if not due_date_str:
        return "No due date"
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(due_date_str, fmt)
            return f"<t:{int(dt.timestamp())}:d> (<t:{int(dt.timestamp())}:R>)"
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(due_date_str)
        return f"<t:{int(dt.timestamp())}:d> (<t:{int(dt.timestamp())}:R>)"
    except ValueError:
        return due_date_str


def has_director_role(user: discord.Member) -> bool:
    return any(role.name == "Directors 🌇" for role in getattr(user, "roles", []))

class Profiles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "data/corelet.db"

    def execute_query(self, query, params=()):
        """Helper function to execute database writes safely."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()

    def fetch_query(self, query, params=()):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()
        
    @app_commands.command(name="register", description="Register a Void Dev profile")
    @app_commands.describe(
        member="Optional: the Discord member to register. If omitted, registers yourself.",
        pronouns="The member's preferred pronouns",
        timezone="The member's timezone (e.g., EST, UTC+2)"
    )
    async def register(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
        pronouns: Optional[str] = None,
        timezone: Optional[str] = None,
    ):
        if member is None:
            member = interaction.user
        elif member.id != interaction.user.id and not has_director_role(interaction.user):
            await interaction.response.send_message(
                "❌ Only Directors can register other members. You can register yourself without the `member` option.",
                ephemeral=True
            )
            return

        pronouns = pronouns.strip() if pronouns else ""
        timezone = timezone.strip() if timezone else ""

        if not pronouns or not timezone:
            await interaction.response.send_message(
                "❌ Please include both pronouns and timezone.",
                ephemeral=True
            )
            return

        user_id = member.id
        discord_name = member.display_name

        try:
            # Insert or update if they already exist.
            self.execute_query("""
                INSERT INTO users (user_id, discord_name, pronouns, timezone) 
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET 
                discord_name=excluded.discord_name, 
                pronouns=excluded.pronouns, 
                timezone=excluded.timezone
            """, (user_id, discord_name, pronouns, timezone))

            await interaction.response.send_message(
                f"✅ Profile registered for {member.mention}. \n"
                f"**Pronouns:** {pronouns} | **Timezone:** {timezone}",
                ephemeral=True # Only the user sees this message
            )
            
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

    @register.error
    async def register_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        raise error

    @app_commands.command(name="profile", description="View a member's profile and active tasks")
    @app_commands.describe(
        member="Optional: The member whose profile you want to view. If omitted, views your own."
    )
    async def profile(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None
    ):
        if member is None:
            member = interaction.user

        # Fetch profile info
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT discord_name, pronouns, timezone, level, tasks_completed
                FROM users WHERE user_id = ?
            """, (member.id,))
            user_data = cursor.fetchone()

            # Fetch active tasks
            cursor.execute("""
                SELECT task_id, variant, sprite_type, pokedex_identifier, status, due_date, forum_thread_id
                FROM tasks
                WHERE user_id = ? AND status != 'Completed'
                ORDER BY due_date ASC, variant, sprite_type
            """, (member.id,))
            active_tasks = cursor.fetchall()

        # If not registered and has no active tasks
        if not user_data and not active_tasks:
            if member.id == interaction.user.id:
                await interaction.response.send_message(
                    "❌ You don't have a registered profile yet. Use `/register` to create one!",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"❌ {member.display_name} does not have a registered profile or any active tasks.",
                    ephemeral=True
                )
            return

        # Prepare user info (with defaults if unregistered but has tasks)
        if user_data:
            _, pronouns, timezone, level, tasks_completed = user_data
        else:
            pronouns, timezone, level, tasks_completed = None, None, 1, 0

        # Construct embed
        embed = discord.Embed(
            title=f"Void Dev Profile — {member.display_name}",
            color=discord.Color.purple()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        
        # User details fields
        embed.add_field(name="Pronouns", value=pronouns or "*Not set*", inline=True)
        embed.add_field(name="Timezone", value=timezone or "*Not set*", inline=True)
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="Tasks Completed", value=str(tasks_completed), inline=True)

        # Build active tasks text
        if active_tasks:
            task_lines = []
            for tid, var, stype, name, status, due_date, thread_id in active_tasks:
                due_str = format_due_date(due_date)
                thread_mention = f" | <#{thread_id}>" if thread_id else ""
                task_lines.append(
                    f"• **#{tid}**: {var} {stype} — {name}\n"
                    f"  Status: `{status}` | Due: {due_str}{thread_mention}"
                )
            embed.add_field(name="Active Tasks", value="\n".join(task_lines), inline=False)
        else:
            embed.add_field(name="Active Tasks", value="*No active tasks.*", inline=False)

        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Profiles(bot))
