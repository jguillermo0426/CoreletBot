import discord
from discord import app_commands
from discord.ext import commands
import sqlite3

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
    @app_commands.checks.has_role("Directors")
    @app_commands.describe(
        member="The Discord member to register",
        pronouns="The member's preferred pronouns",
        timezone="The member's timezone (e.g., EST, UTC+2)"
    )
    async def register(self, interaction: discord.Interaction, member: discord.Member, pronouns: str, timezone: str):
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
        if isinstance(error, app_commands.MissingRole):
            await interaction.response.send_message(
                "❌ Only Directors can register profiles.",
                ephemeral=True
            )
            return

        raise error

    @app_commands.command(name="forcelevel", description="Force a member's Void Dev level")
    @app_commands.checks.has_role("Directors")
    @app_commands.describe(
        member="The Discord member whose level should be changed",
        level="The level to set"
    )
    async def forcelevel(self, interaction: discord.Interaction, member: discord.Member, level: int):
        if level < 1:
            await interaction.response.send_message("❌ Level must be 1 or higher.", ephemeral=True)
            return

        existing = self.fetch_query("SELECT user_id FROM users WHERE user_id = ?", (member.id,))
        if not existing:
            await interaction.response.send_message(
                f"❌ {member.mention} is not registered yet. Register them first with `/register`.",
                ephemeral=True
            )
            return

        try:
            self.execute_query(
                "UPDATE users SET level = ?, discord_name = ? WHERE user_id = ?",
                (level, member.display_name, member.id)
            )
            await interaction.response.send_message(
                f"✅ Set {member.mention}'s level to **{level}**.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

    @forcelevel.error
    async def forcelevel_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingRole):
            await interaction.response.send_message(
                "❌ Only Directors can force level changes.",
                ephemeral=True
            )
            return

        raise error

async def setup(bot):
    await bot.add_cog(Profiles(bot))
