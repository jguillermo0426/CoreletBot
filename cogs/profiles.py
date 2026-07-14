import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
from typing import Optional

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

async def setup(bot):
    await bot.add_cog(Profiles(bot))
