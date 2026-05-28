import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import sqlite3
import gspread
from gspread.exceptions import APIError
from google_auth import get_google_credentials

class SheetSync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "data/corelet.db"
        
        # --- Google Sheets Authentication ---
        try:
            credentials, auth_type = get_google_credentials()
            if credentials is None:
                raise RuntimeError("No Google credentials found.")

            self.gc = gspread.authorize(credentials)
            spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID")
            if spreadsheet_id:
                self.spreadsheet = self.gc.open_by_key(spreadsheet_id)
            else:
                self.spreadsheet = self.gc.open("Pokemon Void Pokedex Checklist ( MASTER DOCUMENTATION )")
            
            # Connect to the new tabs
            self.profiles_sheet = self.spreadsheet.worksheet("Profiles")
            self.tasks_sheet = self.spreadsheet.worksheet("Active Tasks")
            
            # Start the automatic background sync
            self.auto_sync.start()
            print(f"Successfully connected to Google Sheets for Data Syncing using {auth_type}!")
        except Exception as e:
            print(f"Failed to connect for Data Sync: {e}")
            self.gc = None

    def fetch_query(self, query, params=()):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()

    async def perform_sync(self):
        """The core logic to wipe and rewrite the Google Sheets"""
        if self.gc is None:
            return False, "Google Sheets API is not connected."

        try:
            # --- 1. Sync Profiles ---
            users = self.fetch_query("SELECT user_id, discord_name, pronouns, timezone, level, tasks_completed FROM users")
            
            # Create the header row
            profile_data = [["Discord ID", "Discord Name", "Pronouns", "Timezone", "Level", "Tasks Completed"]]
            # Append all user rows
            for user in users:
                profile_data.append([str(user[0]), user[1], user[2], user[3], user[4], user[5]])
            
            # Clear the old data and write the new data
            self.profiles_sheet.clear()
            self.profiles_sheet.update(values=profile_data, range_name='A1')

            # --- 2. Sync Active Tasks ---
            # We join the tasks table with the users table to get the actual artist name instead of just their ID
            tasks = self.fetch_query("""
                SELECT t.task_id, u.discord_name, t.pokedex_identifier, t.sprite_type, t.variant, t.status, t.assigned_date, t.due_date 
                FROM tasks t
                LEFT JOIN users u ON t.user_id = u.user_id
                WHERE t.status IN ('Assigned', 'Waiting For Feedback')
                ORDER BY t.due_date ASC
            """)

            task_data = [["Task ID", "Assigned Artist", "Pokemon", "Type", "Variant", "Status", "Assigned Date", "Due Date"]]
            for task in tasks:
                artist_name = task[1] if task[1] else "Unknown User"
                task_data.append([str(task[0]), artist_name, task[2], task[3], task[4], task[5], task[6], task[7]])

            self.tasks_sheet.clear()
            self.tasks_sheet.update(values=task_data, range_name='A1')

            return True, "Sync Successful"
            
        except APIError as e:
            if "[403]" in str(e):
                return False, (
                    "Google Sheets permission denied. The Google account used for OAuth must have Editor access "
                    "to the spreadsheet, and GOOGLE_SPREADSHEET_ID should point to that exact sheet."
                )
            return False, str(e)
        except Exception as e:
            return False, str(e)

    # --- Manual Command for Directors ---
    @app_commands.command(name="syncdata", description="Forcefully sync the database to Google Sheets")
    @app_commands.checks.has_role("Directors")
    async def syncdata(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True) # Hide this message from the public chat
        
        success, message = await self.perform_sync()
        
        if success:
            await interaction.followup.send("✅ Successfully synced all Profiles and Active Tasks to Google Sheets!")
        else:
            await interaction.followup.send(f"❌ Sync failed: {message}")

    # --- Automatic Background Loop ---
    @tasks.loop(hours=12)
    async def auto_sync(self):
        print("Running automatic database-to-sheets sync...")
        success, message = await self.perform_sync()
        if not success:
            print(f"Auto-sync failed: {message}")

    @auto_sync.before_loop
    async def before_auto_sync(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(SheetSync(bot))
