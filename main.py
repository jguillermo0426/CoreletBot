import discord
from discord.ext import commands
from discord import app_commands # Added this
import traceback # Added this
from dotenv import load_dotenv
import os

load_dotenv()

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID_VALUE = os.getenv("DISCORD_GUILD_ID")
if not GUILD_ID_VALUE:
    raise RuntimeError("DISCORD_GUILD_ID is not set in .env")

GUILD_ID = discord.Object(id=int(GUILD_ID_VALUE)) 

class CoreletBot(commands.Bot):
    def __init__(self):
        # Intents allow the bot to read messages, see members, etc.
        intents = discord.Intents.default()
        intents.message_content = True 
        intents.members = True

        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Load all extensions (Cogs) from the cogs folder
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py") and not filename.startswith("__"):
                await self.load_extension(f"cogs.{filename[:-3]}")
                print(f"Loaded Cog: {filename}")

        # Sync slash commands to the specific dev server
        self.tree.copy_global_to(guild=GUILD_ID)
        await self.tree.sync(guild=GUILD_ID)
        print("Slash commands synced.")

        self.tree.on_error = self.global_tree_error_handler

    async def global_tree_error_handler(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        # 1. Catch the specific role permission errors
        if isinstance(error, app_commands.MissingRole) or isinstance(error, app_commands.MissingAnyRole):
            # Check if we already replied to avoid crashing
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ You do not have the required role to run this command.", ephemeral=True)
            # Notice we do NOT print anything to the console here, effectively silencing it!

        # 2. Let real bugs still print to the console so you can fix them
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message("⚠️ An unexpected error occurred.", ephemeral=True)
            
            # Print the actual error traceback for debugging
            traceback.print_exception(type(error), error, error.__traceback__)

    async def on_ready(self):
        print(f"Logged in as {self.user.name} (ID: {self.user.id})")
        print("CoreletBot is ready.")

if __name__ == "__main__":
    bot = CoreletBot()
    bot.run(BOT_TOKEN)
