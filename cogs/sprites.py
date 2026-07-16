import discord
from discord import app_commands
from discord.ext import commands
import os
import re
import sqlite3
import gspread
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
import io
import base64
import aiohttp
from gspread.exceptions import WorksheetNotFound
from google_auth import get_google_credentials
from task_forum import ensure_task_link_columns, update_task_forum_status, update_task_forum_summary

COMPLETED_TASK_SHEETS = {
    "pokemon": "Completed Pokemon Tasks",
    "character": "Completed Character Tasks",
    "music": "Completed Sound Tasks",
}

COMPLETED_TASK_HEADER = ["Task", "Variant", "Type", "File", "Artist"]
CHARACTER_COMPLETED_HEADER = [
    "Character Name",
    "Character Design",
    "Design Artist",
    "Battler",
    "Battler Artist",
    "Overworld",
    "Overworld Artist",
]
POKEMON_COMPLETED_HEADER = [
    "Dex Number",
    "Pokemon Name",
    "Front Sprite",
    "Frame 2",
    "Back Sprite",
    "Front Sprite Artist",
    "Front 2 Artist",
    "Back Artist",
    "Icons",
    "Icon Artist",
    "Shiny Front",
    "Shiny Front 2",
    "Shiny Back",
    "Anomaly Front",
    "Anomaly Front 2",
    "Anomaly Back",
    "Anomaly Author",
    "Ref",
]
POKEMON_FILE_COLUMNS = {
    ("Base", "Front"): 2,
    ("Base", "Front 2"): 3,
    ("Base", "Back"): 4,
    ("Base", "Icon"): 8,
    ("Shiny", "Front"): 10,
    ("Shiny", "Front 2"): 11,
    ("Shiny", "Back"): 12,
    ("Anomaly", "Front"): 13,
    ("Anomaly", "Front 2"): 14,
    ("Anomaly", "Back"): 15,
}
POKEMON_ARTIST_COLUMNS = {
    ("Base", "Front"): 5,
    ("Base", "Front 2"): 6,
    ("Base", "Back"): 7,
    ("Base", "Icon"): 9,
    ("Anomaly", "Front"): 16,
    ("Anomaly", "Front 2"): 16,
    ("Anomaly", "Back"): 16,
}
CHARACTER_FILE_COLUMNS = {
    "Design": 1,
    "Battler": 3,
    "Overworld": 5,
}
CHARACTER_ARTIST_COLUMNS = {
    "Design": 2,
    "Battler": 4,
    "Overworld": 6,
}

SUBMISSION_SLOT_ALIASES = {
    "base front": ("Base", "Front"),
    "front": ("Base", "Front"),
    "front sprite": ("Base", "Front"),
    "base front sprite": ("Base", "Front"),
    "base front 2": ("Base", "Front 2"),
    "base frame 2": ("Base", "Front 2"),
    "frame 2": ("Base", "Front 2"),
    "front 2": ("Base", "Front 2"),
    "front 2 sprite": ("Base", "Front 2"),
    "base front 2 sprite": ("Base", "Front 2"),
    "base back": ("Base", "Back"),
    "back": ("Base", "Back"),
    "back sprite": ("Base", "Back"),
    "base back sprite": ("Base", "Back"),
    "base icon": ("Base", "Icon"),
    "icon": ("Base", "Icon"),
    "icon sprite": ("Base", "Icon"),
    "shiny front": ("Shiny", "Front"),
    "shiny front sprite": ("Shiny", "Front"),
    "shiny front 2": ("Shiny", "Front 2"),
    "shiny front 2 sprite": ("Shiny", "Front 2"),
    "shiny frame 2": ("Shiny", "Front 2"),
    "shiny back": ("Shiny", "Back"),
    "shiny back sprite": ("Shiny", "Back"),
    "anomaly front": ("Anomaly", "Front"),
    "anomaly front sprite": ("Anomaly", "Front"),
    "anomaly front 2": ("Anomaly", "Front 2"),
    "anomaly front 2 sprite": ("Anomaly", "Front 2"),
    "anomaly frame 2": ("Anomaly", "Front 2"),
    "anomaly back": ("Anomaly", "Back"),
    "anomaly back sprite": ("Anomaly", "Back"),
    "character design": ("Character", "Design"),
    "design": ("Character", "Design"),
    "character battler": ("Character", "Battler"),
    "battler": ("Character", "Battler"),
    "character overworld": ("Character", "Overworld"),
    "overworld": ("Character", "Overworld"),
}


def split_pokemon_identifier(identifier: str):
    identifier = str(identifier)
    if " - " not in identifier:
        return "", identifier.strip()

    dex_number, pokemon_name = identifier.split(" - ", 1)
    return dex_number.strip(), pokemon_name.strip()


def sheet_text(value):
    return "" if value is None else str(value).strip()


def normalize_submission_label(value: str):
    val = re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()
    val = re.sub(r"\bsprites\b", "sprite", val)
    return val


def parse_submission_slot_label(value: str):
    return SUBMISSION_SLOT_ALIASES.get(normalize_submission_label(value))


def extract_message_image_url(message: discord.Message):
    for attachment in message.attachments:
        content_type = attachment.content_type or ""
        filename = attachment.filename.casefold()
        if content_type.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            return attachment.url

    for embed in message.embeds:
        image_url = getattr(getattr(embed, "image", None), "url", None)
        if image_url:
            return image_url
        thumbnail_url = getattr(getattr(embed, "thumbnail", None), "url", None)
        if thumbnail_url:
            return thumbnail_url

    return None


async def update_task_bundle_forum_status(bot, db_path: str, thread_id, message=None):
    if not thread_id:
        return

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT status
            FROM tasks
            WHERE forum_thread_id = ?
              AND status IN ('Available', 'Unassigned', 'Assigned', 'Waiting For Feedback', 'Completed', 'Removed')
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
    elif all(status == "Removed" for status in statuses):
        aggregate_status = "Removed"
    else:
        aggregate_status = "Available"

    sent_message = await update_task_forum_status(bot, thread_id, aggregate_status, message)
    await update_task_forum_summary(bot, db_path, thread_id)
    return sent_message


class Sprites(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "data/corelet.db"
        ensure_task_link_columns(self.db_path)
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
                self.spreadsheet = self.gc.open("Pokemon Void : Profiles, Tasks")
            
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

    def get_drive_task_folder_name(self, identifier: str):
        task_name = identifier
        if " - " in task_name:
            task_name = task_name.split(" - ", 1)[1]

        return task_name.strip().upper().replace("/", "-")

    def get_task_sheet_group(self, variant: str):
        if variant == "Audio":
            return "music"
        if variant == "Character":
            return "character"
        return "pokemon"

    def find_forum_task_by_slot(self, thread_id: int, variant: str, sprite_type: str, required_statuses=("Waiting For Feedback", "Assigned")):
        placeholders = ",".join("?" for _ in required_statuses)
        rows = self.fetch_query(f"""
            SELECT pokedex_identifier, user_id, task_id
            FROM tasks
            WHERE forum_thread_id = ?
              AND variant = ?
              AND sprite_type = ?
              AND status IN ({placeholders})
            ORDER BY task_id ASC
            LIMIT 1
        """, (thread_id, variant, sprite_type, *required_statuses))
        return rows[0] if rows else None

    def get_or_create_completed_worksheet(self, variant: str):
        title = COMPLETED_TASK_SHEETS[self.get_task_sheet_group(variant)]
        sheet_group = self.get_task_sheet_group(variant)
        if sheet_group == "pokemon":
            header = POKEMON_COMPLETED_HEADER
        elif sheet_group == "character":
            header = CHARACTER_COMPLETED_HEADER
        else:
            header = COMPLETED_TASK_HEADER
        try:
            worksheet = self.spreadsheet.worksheet(title)
        except WorksheetNotFound:
            worksheet = self.spreadsheet.add_worksheet(title=title, rows=100, cols=len(header))

        if not worksheet.get_all_values():
            worksheet.update(values=[header], range_name="A1")

        return worksheet

    def get_worksheet_values(self, worksheet):
        try:
            return worksheet.get_all_values(value_render_option="FORMULA")
        except TypeError:
            return worksheet.get_all_values()

    def update_completed_pokemon_sheet(self, worksheet, identifier: str, variant: str, sprite_type: str, file_value: str, artist_name: str):
        file_column = POKEMON_FILE_COLUMNS.get((variant, sprite_type))
        if file_column is None:
            worksheet.append_row([identifier, variant, sprite_type, file_value, artist_name], value_input_option="USER_ENTERED")
            return

        dex_number, pokemon_name = split_pokemon_identifier(identifier)
        values = self.get_worksheet_values(worksheet)
        if not values:
            values = [POKEMON_COMPLETED_HEADER]

        header = values[0]
        if header[:len(POKEMON_COMPLETED_HEADER)] != POKEMON_COMPLETED_HEADER:
            migrated_rows = {}
            for row in values[1:]:
                if len(row) < 5:
                    continue

                old_identifier, old_variant, old_sprite_type, old_file_value, old_artist_name = row[:5]
                old_dex_number, old_pokemon_name = split_pokemon_identifier(old_identifier)
                pokemon_key = (old_dex_number, old_pokemon_name)
                if pokemon_key not in migrated_rows:
                    migrated_row = [""] * len(POKEMON_COMPLETED_HEADER)
                    migrated_row[0] = old_dex_number
                    migrated_row[1] = old_pokemon_name
                    migrated_rows[pokemon_key] = migrated_row

                migrated_row = migrated_rows[pokemon_key]
                old_file_column = POKEMON_FILE_COLUMNS.get((old_variant, old_sprite_type))
                if old_file_column is not None:
                    migrated_row[old_file_column] = old_file_value

                old_artist_column = POKEMON_ARTIST_COLUMNS.get((old_variant, old_sprite_type))
                if old_artist_column is not None:
                    migrated_row[old_artist_column] = old_artist_name

            worksheet.clear()
            values = [POKEMON_COMPLETED_HEADER, *migrated_rows.values()]
            worksheet.update(values=values, range_name="A1", value_input_option="USER_ENTERED")

        row_number = None
        row_values = None
        for index, row in enumerate(values[1:], start=2):
            padded_row = row + [""] * (len(POKEMON_COMPLETED_HEADER) - len(row))
            if sheet_text(padded_row[0]) == dex_number and sheet_text(padded_row[1]).casefold() == pokemon_name.casefold():
                row_number = index
                row_values = padded_row[:len(POKEMON_COMPLETED_HEADER)]
                break

        if row_values is None:
            row_number = len(values) + 1
            row_values = [""] * len(POKEMON_COMPLETED_HEADER)
            row_values[0] = dex_number
            row_values[1] = pokemon_name

        row_values[file_column] = file_value
        artist_column = POKEMON_ARTIST_COLUMNS.get((variant, sprite_type))
        if artist_column is not None:
            row_values[artist_column] = artist_name

        worksheet.update(
            values=[row_values],
            range_name=f"A{row_number}:R{row_number}",
            value_input_option="USER_ENTERED",
        )

    def update_completed_character_sheet(self, worksheet, identifier: str, sprite_type: str, file_value: str, artist_name: str):
        file_column = CHARACTER_FILE_COLUMNS.get(sprite_type)
        if file_column is None:
            worksheet.append_row([identifier, "Character", sprite_type, file_value, artist_name], value_input_option="USER_ENTERED")
            return

        values = self.get_worksheet_values(worksheet)
        if not values:
            values = [CHARACTER_COMPLETED_HEADER]

        header = values[0]
        if header[:len(CHARACTER_COMPLETED_HEADER)] != CHARACTER_COMPLETED_HEADER:
            migrated_rows = {}
            for row in values[1:]:
                if len(row) < 5:
                    continue

                old_identifier, old_variant, old_sprite_type, old_file_value, old_artist_name = row[:5]
                if old_variant != "Character":
                    continue

                if old_identifier not in migrated_rows:
                    migrated_row = [""] * len(CHARACTER_COMPLETED_HEADER)
                    migrated_row[0] = old_identifier
                    migrated_rows[old_identifier] = migrated_row

                migrated_row = migrated_rows[old_identifier]
                old_file_column = CHARACTER_FILE_COLUMNS.get(old_sprite_type)
                if old_file_column is not None:
                    migrated_row[old_file_column] = old_file_value

                old_artist_column = CHARACTER_ARTIST_COLUMNS.get(old_sprite_type)
                if old_artist_column is not None:
                    migrated_row[old_artist_column] = old_artist_name

            worksheet.clear()
            values = [CHARACTER_COMPLETED_HEADER, *migrated_rows.values()]
            worksheet.update(values=values, range_name="A1", value_input_option="USER_ENTERED")

        row_number = None
        row_values = None
        for index, row in enumerate(values[1:], start=2):
            padded_row = row + [""] * (len(CHARACTER_COMPLETED_HEADER) - len(row))
            if sheet_text(padded_row[0]).casefold() == identifier.casefold():
                row_number = index
                row_values = padded_row[:len(CHARACTER_COMPLETED_HEADER)]
                break

        if row_values is None:
            row_number = len(values) + 1
            row_values = [""] * len(CHARACTER_COMPLETED_HEADER)
            row_values[0] = identifier

        row_values[file_column] = file_value
        artist_column = CHARACTER_ARTIST_COLUMNS.get(sprite_type)
        if artist_column is not None:
            row_values[artist_column] = artist_name

        worksheet.update(
            values=[row_values],
            range_name=f"A{row_number}:G{row_number}",
            value_input_option="USER_ENTERED",
        )

    def get_drive_subfolder_path(self, identifier: str, variant: str, sprite_type: str):
        task_folder = self.get_drive_task_folder_name(identifier)
        if variant == "Audio":
            return [sprite_type]
        if variant == "Character":
            return [sprite_type, task_folder]
        return [variant, sprite_type, task_folder]

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

        safe_name = attachment.filename.replace("/", "-")
        media = MediaIoBaseUpload(
            io.BytesIO(file_bytes),
            mimetype=attachment.content_type or "application/octet-stream",
            resumable=False
        )
        destination_folder_id = self.ensure_drive_folder_path(
            self.get_drive_folder_id(variant),
            self.get_drive_subfolder_path(identifier, variant, sprite_type)
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

    async def upload_attachment_to_drive_folder(self, attachment: discord.Attachment, folder_id: str):
        if self.drive_service is None:
            return None, "Google Drive integration is offline. Check bot logs."
        if not folder_id:
            return None, "GOOGLE_DRIVE_FOLDER_ID is not set in .env."

        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as resp:
                if resp.status != 200:
                    return None, "Failed to download attachment from Discord."
                file_bytes = await resp.read()

        safe_name = attachment.filename.replace("/", "-")
        media = MediaIoBaseUpload(
            io.BytesIO(file_bytes),
            mimetype=attachment.content_type or "application/octet-stream",
            resumable=False
        )
        file_metadata = {
            "name": safe_name,
            "parents": [folder_id],
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

            return uploaded_file["webViewLink"], None
        except HttpError as e:
            return None, str(e)

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
            image_url = extract_message_image_url(message)
            if not image_url:
                await interaction.followup.send("❌ The selected message does not contain an image attachment or embedded image.")
                return

            attachment = next(
                (
                    candidate
                    for candidate in message.attachments
                    if (candidate.content_type or "").startswith("image/")
                    or candidate.filename.casefold().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
                ),
                None,
            )

            if attachment is not None:
                permanent_file_url, upload_error = await self.upload_image_to_imgbb(attachment.url)
                sheet_file_value = f'=IMAGE("{permanent_file_url}")' if permanent_file_url else None
                drive_file_url, drive_upload_error, drive_destination = await self.upload_attachment_to_drive(attachment, identifier, sprite_type, variant)
                upload_destination = f"ImgBB and {drive_destination}" if drive_file_url else "ImgBB"
                if drive_upload_error:
                    print(f"Drive copy failed for image attachment: {drive_upload_error}")
            else:
                permanent_file_url, upload_error = await self.upload_image_to_imgbb(image_url)
                sheet_file_value = f'=IMAGE("{permanent_file_url}")' if permanent_file_url else None
                upload_destination = "ImgBB"

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
            worksheet = self.get_or_create_completed_worksheet(variant)
            if self.get_task_sheet_group(variant) == "pokemon":
                self.update_completed_pokemon_sheet(
                    worksheet,
                    identifier,
                    variant,
                    sprite_type,
                    sheet_file_value,
                    artist_name,
                )
            elif self.get_task_sheet_group(variant) == "character":
                self.update_completed_character_sheet(
                    worksheet,
                    identifier,
                    sprite_type,
                    sheet_file_value,
                    artist_name,
                )
            else:
                worksheet.append_row(row_data, value_input_option="USER_ENTERED")
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to update Google Sheets: {e}")
            return

        try:
            self.execute_query(
                "UPDATE tasks SET status = 'Completed', completion_message_url = ? WHERE task_id = ?",
                (message.jump_url, task_id),
            )
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
    is_verifier = any(role.name in ("Sprite Verifier", "Directors 🌇") for role in interaction.user.roles)
    if not is_verifier:
        await interaction.response.send_message("❌ You do not have the required role to use this action.", ephemeral=True)
        return

    cog = interaction.client.get_cog("Sprites")
    if cog is None:
        await interaction.response.send_message("❌ Submission tools are not loaded.", ephemeral=True)
        return

    thread = message.channel if isinstance(message.channel, discord.Thread) else None
    if thread is not None:
        slot = parse_submission_slot_label(message.content)
        if slot is not None:
            variant, sprite_type = slot
            matched_task = cog.find_forum_task_by_slot(
                thread.id,
                variant,
                sprite_type,
            )
            if matched_task:
                identifier, _user_id, _task_id = matched_task
                await interaction.response.defer(ephemeral=True)
                await cog.finalize_submission(
                    interaction,
                    message,
                    identifier,
                    sprite_type,
                    variant,
                    required_statuses=("Assigned", "Waiting For Feedback",),
                )
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
        await interaction.response.send_message("❌ No tasks are currently waiting for feedback, and this message did not match a labeled forum task.", ephemeral=True)
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


async def push_to_drive_context_menu(interaction: discord.Interaction, message: discord.Message):
    is_director = any(role.name == "Directors 🌇" for role in interaction.user.roles)
    if not is_director:
        await interaction.response.send_message("❌ Only Directors can push images to Drive.", ephemeral=True)
        return

    cog = interaction.client.get_cog("Sprites")
    if cog is None:
        await interaction.response.send_message("❌ Drive tools are not loaded.", ephemeral=True)
        return

    image_attachments = [attachment for attachment in message.attachments if cog.is_image_attachment(attachment)]
    if not image_attachments:
        await interaction.response.send_message("❌ That message has no image attachments to push.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    uploaded_links = []
    failures = []
    for attachment in image_attachments:
        drive_url, error = await cog.upload_attachment_to_drive_folder(attachment, cog.default_drive_folder_id)
        if error:
            failures.append(f"{attachment.filename}: {error}")
        else:
            uploaded_links.append((attachment.filename, drive_url))

    if not uploaded_links and failures:
        await interaction.followup.send("❌ Failed to push images to Drive:\n" + "\n".join(failures[:5]), ephemeral=True)
        return

    lines = [f"✅ **{filename}**\n{drive_url}" for filename, drive_url in uploaded_links]
    if failures:
        lines.append("⚠️ Some images failed:\n" + "\n".join(failures[:5]))

    await interaction.followup.send(
        "Pushed to the general Drive drop-off folder:\n\n" + "\n".join(lines),
        ephemeral=True
    )


async def setup(bot):
    await bot.add_cog(Sprites(bot))
    bot.tree.add_command(app_commands.ContextMenu(
        name="Accept Submission",
        callback=accept_submission_context_menu,
    ))
    bot.tree.add_command(app_commands.ContextMenu(
        name="Push to Drive",
        callback=push_to_drive_context_menu,
    ))