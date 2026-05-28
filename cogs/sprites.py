import discord
from discord import app_commands
from discord.ext import commands
import os
import sqlite3
import gspread
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
import io
import base64
import aiohttp
from google_auth import get_google_credentials
from task_forum import update_task_forum_status


async def update_task_bundle_forum_status(bot, db_path: str, thread_id, message=None):
    if not thread_id:
        return

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT status
            FROM tasks
            WHERE forum_thread_id = ?
              AND status IN ('Available', 'Unassigned', 'Assigned', 'Waiting For Feedback', 'Completed')
        """, (thread_id,))
        statuses = [row[0] for row in cursor.fetchall()]

    if not statuses:
        return

    if any(status == "Waiting For Feedback" for status in statuses):
        aggregate_status = "Waiting For Feedback"
    elif any(status == "Assigned" for status in statuses):
        aggregate_status = "Assigned"
    elif all(status == "Completed" for status in statuses):
        aggregate_status = "Completed"
    else:
        aggregate_status = "Available"

    await update_task_forum_status(bot, thread_id, aggregate_status, message)


class Sprites(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "data/corelet.db"
        self.tasks_per_level = 5
        
        self.default_drive_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
        self.pokemon_drive_folder_id = os.getenv("GOOGLE_DRIVE_POKEMON_FOLDER_ID", self.default_drive_folder_id)
        self.character_drive_folder_id = os.getenv("GOOGLE_DRIVE_CHARACTER_FOLDER_ID", self.default_drive_folder_id)
        self.sounds_drive_folder_id = os.getenv("GOOGLE_DRIVE_SOUNDS_FOLDER_ID", self.default_drive_folder_id)
        self.imgbb_api_key = os.getenv("IMGBB_API_KEY") or os.getenv("IMG_BB")
        
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
            self.worksheet = self.spreadsheet.worksheet("Completed Sprites")
            
            # Initialize the Drive API
            self.drive_service = build('drive', 'v3', credentials=credentials)
            print(f"Successfully connected to Google Sheets & Drive using {auth_type}!")
        except Exception as e:
            print(f"Failed to connect to Google APIs: {e}")
            self.gc = None
            self.drive_service = None

    def execute_query(self, query, params=()):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()

    def fetch_query(self, query, params=()):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()

    def is_image_attachment(self, attachment: discord.Attachment):
        if attachment.content_type:
            return attachment.content_type.startswith("image/")

        return attachment.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))

    def get_drive_folder_id(self, variant: str):
        if variant == "Audio":
            return self.sounds_drive_folder_id
        if variant == "Character":
            return self.character_drive_folder_id
        return self.pokemon_drive_folder_id

    def get_drive_subfolder_path(self, variant: str, sprite_type: str):
        if variant == "Audio":
            return [sprite_type]
        if variant == "Character":
            return [sprite_type]
        return [variant, sprite_type]

    def escape_drive_query_value(self, value: str):
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def find_or_create_drive_folder(self, parent_id: str, folder_name: str):
        escaped_name = self.escape_drive_query_value(folder_name)
        query = (
            "mimeType = 'application/vnd.google-apps.folder' "
            f"and name = '{escaped_name}' "
            f"and '{parent_id}' in parents "
            "and trashed = false"
        )

        result = self.drive_service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        folders = result.get("files", [])
        if folders:
            return folders[0]["id"]

        folder_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        folder = self.drive_service.files().create(
            body=folder_metadata,
            fields="id",
            supportsAllDrives=True,
        ).execute()
        return folder["id"]

    def ensure_drive_folder_path(self, root_folder_id: str, path_parts: list[str]):
        folder_id = root_folder_id
        for part in path_parts:
            folder_id = self.find_or_create_drive_folder(folder_id, part)
        return folder_id

    async def upload_image_to_imgbb(self, image_url: str):
        if not self.imgbb_api_key:
            return None, "IMGBB_API_KEY is not set in .env."

        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                if resp.status != 200:
                    return None, "Failed to download image from Discord."
                image_bytes = await resp.read()

            b64_image = base64.b64encode(image_bytes).decode('utf-8')
            async with session.post(
                "https://api.imgbb.com/1/upload",
                data={"key": self.imgbb_api_key, "image": b64_image}
            ) as imgbb_resp:
                if imgbb_resp.status != 200:
                    return None, f"ImgBB Upload Failed: HTTP {imgbb_resp.status}"

                json_data = await imgbb_resp.json()
                return json_data['data']['url'], None

    async def upload_attachment_to_drive(self, attachment: discord.Attachment, identifier: str, sprite_type: str, variant: str):
        if self.drive_service is None:
            return None, "Google Drive integration is offline. Check bot logs.", None
        if not self.get_drive_folder_id(variant):
            return None, "The matching Google Drive folder ID is not set in .env.", None

        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as resp:
                if resp.status != 200:
                    return None, "Failed to download attachment from Discord.", None
                file_bytes = await resp.read()

        safe_name = f"{variant}_{sprite_type}_{identifier}_{attachment.filename}".replace("/", "-")
        media = MediaIoBaseUpload(
            io.BytesIO(file_bytes),
            mimetype=attachment.content_type or "application/octet-stream",
            resumable=False
        )
        destination_folder_id = self.ensure_drive_folder_path(
            self.get_drive_folder_id(variant),
            self.get_drive_subfolder_path(variant, sprite_type)
        )

        file_metadata = {
            "name": safe_name,
            "parents": [destination_folder_id],
        }

        try:
            uploaded_file = self.drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, webViewLink",
                supportsAllDrives=True,
            ).execute()

            self.drive_service.permissions().create(
                fileId=uploaded_file["id"],
                body={"type": "anyone", "role": "reader"},
                supportsAllDrives=True,
            ).execute()

            return uploaded_file["webViewLink"], None, "Google Drive"
        except HttpError as e:
            if e.resp.status == 403 and "Service Accounts do not have storage quota" in str(e):
                return attachment.url, None, "Discord attachment link (Google Drive quota blocked)"
            return None, str(e), None

    async def finalize_submission(self, interaction: discord.Interaction, message: discord.Message, identifier: str, sprite_type: str, variant: str, required_statuses=("Assigned", "Waiting For Feedback")):
        if self.gc is None:
            await interaction.followup.send("❌ Google integrations are offline. Check bot logs.")
            return

        placeholders = ",".join("?" for _ in required_statuses)
        task = self.fetch_query("""
            SELECT task_id, user_id, forum_thread_id FROM tasks 
            WHERE pokedex_identifier = ? AND sprite_type = ? AND variant = ? AND status IN ({})
        """.format(placeholders), (identifier, sprite_type, variant, *required_statuses))

        if not task:
            await interaction.followup.send(f"❌ Could not find an active task for **{variant} {sprite_type} {identifier}**.")
            return

        task_id, user_id, thread_id = task[0]

        user_data = self.fetch_query("SELECT discord_name FROM users WHERE user_id = ?", (user_id,))
        artist_name = user_data[0][0] if user_data else f"Unknown User ({user_id})"

        try:
            if not message.attachments:
                await interaction.followup.send("❌ The selected message does not contain any attachments.")
                return
            
            attachment = message.attachments[0]
            if self.is_image_attachment(attachment):
                permanent_file_url, upload_error = await self.upload_image_to_imgbb(attachment.url)
                sheet_file_value = f'=IMAGE("{permanent_file_url}")' if permanent_file_url else None
                drive_file_url, drive_upload_error, drive_destination = await self.upload_attachment_to_drive(attachment, identifier, sprite_type, variant)
                upload_destination = f"ImgBB and {drive_destination}" if drive_file_url else "ImgBB"
                if drive_upload_error:
                    print(f"Drive copy failed for image attachment: {drive_upload_error}")
            else:
                permanent_file_url, upload_error, upload_destination = await self.upload_attachment_to_drive(attachment, identifier, sprite_type, variant)
                sheet_file_value = permanent_file_url

            if upload_error:
                await interaction.followup.send(f"❌ {upload_error}")
                return

        except Exception as e:
            await interaction.followup.send(f"❌ Error handling attachment: {e}")
            return

        try:
            row_data = [
                identifier, 
                variant, 
                sprite_type, 
                sheet_file_value,
                artist_name
            ]
            self.worksheet.append_row(row_data, value_input_option="USER_ENTERED")
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to update Google Sheets: {e}")
            return

        try:
            self.execute_query("UPDATE tasks SET status = 'Completed' WHERE task_id = ?", (task_id,))
            self.execute_query("UPDATE users SET tasks_completed = tasks_completed + 1 WHERE user_id = ?", (user_id,))
            await update_task_bundle_forum_status(
                self.bot,
                self.db_path,
                thread_id,
                f"This task was completed and verified by {interaction.user.mention}."
            )

            level_rows = self.fetch_query("SELECT tasks_completed, level FROM users WHERE user_id = ?", (user_id,))
            level_message = ""
            if level_rows:
                tasks_completed, current_level = level_rows[0]
                natural_level = (tasks_completed // self.tasks_per_level) + 1
                if natural_level > current_level:
                    self.execute_query("UPDATE users SET level = ? WHERE user_id = ?", (natural_level, user_id))
                    level_message = f"\n🎉 <@{user_id}> naturally leveled up to **Level {natural_level}**!"
            
            await interaction.followup.send(f"✅ Successfully verified **{variant} {sprite_type} {identifier}**! Uploaded securely to {upload_destination} and credited to **{artist_name}** in the Sheet.{level_message}")
        except Exception as e:
            await interaction.followup.send(f"❌ Database error during finalization: {e}")

    @app_commands.command(name="voidcomplete", description="Finalize submitted work and push it to Sheets")
    @app_commands.describe(
        identifier="Pokemon name or Dex number",
        sprite_type="Front, Back, Music, Sound Effect, Cry, etc.",
        variant="Base, Shiny, Anomaly, Audio, Character, etc.",
        message_id="Message id of the submitted attachment"
    )
    @app_commands.checks.has_any_role("Sprite Verifier", "Directors")
    async def voidcomplete(self, interaction: discord.Interaction, identifier: str, sprite_type: str, variant: str, message_id: str):
        await interaction.response.defer()

        try:
            message = await interaction.channel.fetch_message(int(message_id))
        except Exception as e:
            await interaction.followup.send(f"❌ Could not fetch that message: {e}")
            return

        await self.finalize_submission(interaction, message, identifier, sprite_type, variant)


class WaitingSubmissionDropdown(discord.ui.Select):
    def __init__(self, cog: Sprites, message: discord.Message, waiting_tasks):
        self.cog = cog
        self.message = message
        options = [
            discord.SelectOption(
                label=f"{variant} {sprite_type} - {identifier}"[:100],
                value=str(task_id),
                description=f"By {artist_name}"[:100],
            )
            for task_id, variant, sprite_type, identifier, artist_name in waiting_tasks
        ]
        super().__init__(
            placeholder="Select the waiting task this submission completes...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        task_id = int(self.values[0])
        task = self.cog.fetch_query("""
            SELECT variant, sprite_type, pokedex_identifier
            FROM tasks
            WHERE task_id = ? AND status = 'Waiting For Feedback'
        """, (task_id,))

        if not task:
            await interaction.followup.send("❌ That task is no longer waiting for feedback.", ephemeral=True)
            return

        variant, sprite_type, identifier = task[0]
        await self.cog.finalize_submission(
            interaction,
            self.message,
            identifier,
            sprite_type,
            variant,
            required_statuses=("Waiting For Feedback",),
        )


class WaitingSubmissionView(discord.ui.View):
    def __init__(self, cog: Sprites, message: discord.Message, waiting_tasks):
        super().__init__(timeout=300)
        self.add_item(WaitingSubmissionDropdown(cog, message, waiting_tasks))


async def accept_submission_context_menu(interaction: discord.Interaction, message: discord.Message):
    is_verifier = any(role.name in ("Sprite Verifier", "Directors") for role in interaction.user.roles)
    if not is_verifier:
        await interaction.response.send_message("❌ You do not have the required role to use this action.", ephemeral=True)
        return

    if not message.attachments:
        await interaction.response.send_message("❌ That message has no attachments to accept.", ephemeral=True)
        return

    cog = interaction.client.get_cog("Sprites")
    if cog is None:
        await interaction.response.send_message("❌ Submission tools are not loaded.", ephemeral=True)
        return

    waiting_tasks = cog.fetch_query("""
        SELECT t.task_id, t.variant, t.sprite_type, t.pokedex_identifier, COALESCE(u.discord_name, 'Unknown User')
        FROM tasks t
        LEFT JOIN users u ON t.user_id = u.user_id
        WHERE t.status = 'Waiting For Feedback'
        ORDER BY t.pokedex_identifier COLLATE NOCASE, t.variant, t.sprite_type
        LIMIT 25
    """)

    if not waiting_tasks:
        await interaction.response.send_message("❌ No tasks are currently waiting for feedback.", ephemeral=True)
        return

    embed = discord.Embed(
        title="Accept Submission",
        description="Choose the waiting task this submission completes.",
        color=discord.Color.dark_grey()
    )
    await interaction.response.send_message(
        embed=embed,
        view=WaitingSubmissionView(cog, message, waiting_tasks),
        ephemeral=True
    )

async def setup(bot):
    await bot.add_cog(Sprites(bot))
    bot.tree.add_command(app_commands.ContextMenu(
        name="Accept Submission",
        callback=accept_submission_context_menu,
    ))
