import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import sqlite3
import gspread
from gspread.exceptions import APIError, WorksheetNotFound
from google_auth import get_google_credentials
from task_forum import update_task_forum_summary

TASK_SHEET_CONFIGS = {
    "pokemon": {
        "active": "Active Pokemon Tasks",
        "completed": "Completed Pokemon Tasks",
        "where": "t.variant IN ('Base', 'Shiny', 'Anomaly')",
    },
    "character": {
        "active": "Active Character Tasks",
        "completed": "Completed Character Tasks",
        "where": "t.variant = 'Character'",
    },
    "music": {
        "active": "Active Sound Tasks",
        "completed": "Completed Sound Tasks",
        "where": "t.variant = 'Audio'",
    },
}

ACTIVE_TASK_STATUSES = ("Available", "Unassigned", "Assigned", "Waiting For Feedback")
ACTIVE_TASK_HEADER = [
    "Task ID",
    "Assigned Artist",
    "Task",
    "Type",
    "Variant",
    "Status",
    "Assigned Date",
    "Due Date",
    "Minimum Level",
    "Reference Image",
]
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


def split_pokemon_identifier(identifier: str):
    identifier = str(identifier)
    if " - " not in identifier:
        return "", identifier.strip()

    dex_number, pokemon_name = identifier.split(" - ", 1)
    return dex_number.strip(), pokemon_name.strip()


def sheet_text(value):
    return "" if value is None else str(value).strip()

class SheetSync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "data/corelet.db"
        self.last_db_mtime = None
        self.pending_sync_task = None
        self._ignore_watcher = False
        
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
                self.spreadsheet = self.gc.open("Pokemon Void : Profiles, Tasks")
            
            # Connect to the profile tab. Task tabs are created/fetched as needed during sync.
            self.profiles_sheet = self.spreadsheet.worksheet("Profiles")
            self.ensure_schema()
            
            # Start the automatic background sync
            self.auto_sync.start()
            print(f"Successfully connected to Google Sheets for Data Syncing using {auth_type}!")
        except Exception as e:
            print(f"Failed to connect for Data Sync: {e}")
            self.gc = None

        # Start watching the SQLite database file for changes to auto-sync
        self.db_file_watcher.start()

    def ensure_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(tasks)")
            columns = {row[1] for row in cursor.fetchall()}
            if "min_level" not in columns:
                cursor.execute("ALTER TABLE tasks ADD COLUMN min_level INTEGER")
            if "reference_image_url" not in columns:
                cursor.execute("ALTER TABLE tasks ADD COLUMN reference_image_url TEXT")
            conn.commit()

    def fetch_query(self, query, params=()):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()

    def get_or_create_worksheet(self, title: str, rows: int = 100, cols: int = 8):
        try:
            return self.spreadsheet.worksheet(title)
        except WorksheetNotFound:
            return self.spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)

    def get_worksheet_values(self, worksheet):
        try:
            return worksheet.get_all_values(value_render_option="FORMULA")
        except TypeError:
            return worksheet.get_all_values()

    def build_task_rows(self, where_clause: str, statuses):
        placeholders = ", ".join("?" for _ in statuses)
        rows = self.fetch_query(f"""
            SELECT t.task_id, u.discord_name, t.pokedex_identifier, t.sprite_type, t.variant,
                   t.status, t.assigned_date, t.due_date, t.min_level, t.reference_image_url
            FROM tasks t
            LEFT JOIN users u ON t.user_id = u.user_id
            WHERE {where_clause}
              AND t.status IN ({placeholders})
            ORDER BY t.status, t.due_date ASC, t.pokedex_identifier COLLATE NOCASE
        """, statuses)

        task_data = [ACTIVE_TASK_HEADER]
        for task_id, artist_name, identifier, sprite_type, variant, status, assigned_date, due_date, min_level, reference_image_url in rows:
            task_data.append([
                str(task_id),
                artist_name or "Unassigned",
                identifier,
                sprite_type,
                variant,
                status,
                assigned_date,
                due_date,
                min_level or "",
                reference_image_url or "",
            ])
        return task_data

    def get_completed_sheet_lookups(self):
        file_lookup = {}
        artist_lookup = {}
        ref_lookup = {}
        sheet_titles = [config["completed"] for config in TASK_SHEET_CONFIGS.values()]
        sheet_titles.append("Completed Tasks")

        for title in sheet_titles:
            try:
                values = self.get_worksheet_values(self.spreadsheet.worksheet(title))
            except WorksheetNotFound:
                continue

            if title == TASK_SHEET_CONFIGS["pokemon"]["completed"] and values and values[0] == POKEMON_COMPLETED_HEADER:
                for row in values[1:]:
                    if len(row) < 2:
                        continue

                    padded_row = row + [""] * (len(POKEMON_COMPLETED_HEADER) - len(row))
                    dex_number, pokemon_name = sheet_text(padded_row[0]), sheet_text(padded_row[1])
                    if not dex_number and not pokemon_name:
                        continue

                    identifier = f"{dex_number} - {pokemon_name}" if dex_number else pokemon_name
                    ref_lookup[(dex_number, pokemon_name)] = padded_row[17]

                    for key, column_index in POKEMON_FILE_COLUMNS.items():
                        file_value = padded_row[column_index]
                        if file_value:
                            variant, sprite_type = key
                            file_lookup[(identifier, variant, sprite_type)] = file_value

                    for key, column_index in POKEMON_ARTIST_COLUMNS.items():
                        artist_name = padded_row[column_index]
                        if artist_name:
                            variant, sprite_type = key
                            artist_lookup[(identifier, variant, sprite_type)] = artist_name
                continue

            if title == TASK_SHEET_CONFIGS["character"]["completed"] and values and values[0] == CHARACTER_COMPLETED_HEADER:
                for row in values[1:]:
                    if not row:
                        continue

                    padded_row = row + [""] * (len(CHARACTER_COMPLETED_HEADER) - len(row))
                    character_name = sheet_text(padded_row[0])
                    if not character_name:
                        continue

                    for sprite_type, column_index in CHARACTER_FILE_COLUMNS.items():
                        file_value = padded_row[column_index]
                        if file_value:
                            file_lookup[(character_name, "Character", sprite_type)] = file_value

                    for sprite_type, column_index in CHARACTER_ARTIST_COLUMNS.items():
                        artist_name = padded_row[column_index]
                        if artist_name:
                            artist_lookup[(character_name, "Character", sprite_type)] = artist_name
                continue

            for row in values[1:]:
                if len(row) < 5:
                    continue

                identifier, variant, sprite_type, file_value, artist_name = row[:5]
                key = (identifier, variant, sprite_type)
                if file_value and key not in file_lookup:
                    file_lookup[key] = file_value
                if artist_name and key not in artist_lookup:
                    artist_lookup[key] = artist_name

        return file_lookup, artist_lookup, ref_lookup

    def build_completed_task_rows(self, where_clause: str, file_lookup):
        rows = self.fetch_query(f"""
            SELECT t.pokedex_identifier, t.variant, t.sprite_type,
                   COALESCE(u.discord_name, 'Unknown User'), t.reference_image_url
            FROM tasks t
            LEFT JOIN users u ON t.user_id = u.user_id
            WHERE {where_clause}
              AND t.status = 'Completed'
            ORDER BY t.pokedex_identifier COLLATE NOCASE, t.variant, t.sprite_type
        """)

        task_data = [COMPLETED_TASK_HEADER]
        for identifier, variant, sprite_type, artist_name, reference_image_url in rows:
            file_value = file_lookup.get((identifier, variant, sprite_type), "")
            task_data.append([identifier, variant, sprite_type, file_value, artist_name])
        return task_data

    def build_completed_pokemon_rows(self, file_lookup, artist_lookup, ref_lookup):
        rows = self.fetch_query("""
            SELECT t.pokedex_identifier, t.variant, t.sprite_type,
                   COALESCE(u.discord_name, 'Unknown User'), t.reference_image_url
            FROM tasks t
            LEFT JOIN users u ON t.user_id = u.user_id
            WHERE t.variant IN ('Base', 'Shiny', 'Anomaly')
              AND t.status = 'Completed'
            ORDER BY t.pokedex_identifier COLLATE NOCASE, t.variant, t.sprite_type
        """)

        pokemon_rows = {}
        for identifier, variant, sprite_type, artist_name, reference_image_url in rows:
            dex_number, pokemon_name = split_pokemon_identifier(identifier)
            pokemon_key = (dex_number, pokemon_name)
            if pokemon_key not in pokemon_rows:
                row = [""] * len(POKEMON_COMPLETED_HEADER)
                row[0] = dex_number
                row[1] = pokemon_name
                row[17] = reference_image_url or ref_lookup.get(pokemon_key, "")
                pokemon_rows[pokemon_key] = row
            elif reference_image_url and not pokemon_rows[pokemon_key][17]:
                pokemon_rows[pokemon_key][17] = reference_image_url

            row = pokemon_rows[pokemon_key]
            task_key = (identifier, variant, sprite_type)
            file_column = POKEMON_FILE_COLUMNS.get((variant, sprite_type))
            if file_column is not None:
                row[file_column] = file_lookup.get(task_key, row[file_column])

            artist_column = POKEMON_ARTIST_COLUMNS.get((variant, sprite_type))
            if artist_column is not None:
                row[artist_column] = artist_name or artist_lookup.get(task_key, row[artist_column])

        def sort_key(item):
            (dex_number, pokemon_name), _row = item
            if dex_number.isdigit():
                return (0, int(dex_number), pokemon_name.casefold())
            return (1, dex_number.casefold(), pokemon_name.casefold())

        return [POKEMON_COMPLETED_HEADER] + [
            row for _pokemon_key, row in sorted(pokemon_rows.items(), key=sort_key)
        ]

    def build_completed_character_rows(self, file_lookup, artist_lookup):
        rows = self.fetch_query("""
            SELECT t.pokedex_identifier, t.sprite_type, COALESCE(u.discord_name, 'Unknown User')
            FROM tasks t
            LEFT JOIN users u ON t.user_id = u.user_id
            WHERE t.variant = 'Character'
              AND t.status = 'Completed'
            ORDER BY t.pokedex_identifier COLLATE NOCASE, t.sprite_type
        """)

        character_rows = {}
        for character_name, sprite_type, artist_name in rows:
            if character_name not in character_rows:
                row = [""] * len(CHARACTER_COMPLETED_HEADER)
                row[0] = character_name
                character_rows[character_name] = row

            row = character_rows[character_name]
            task_key = (character_name, "Character", sprite_type)
            file_column = CHARACTER_FILE_COLUMNS.get(sprite_type)
            if file_column is not None:
                row[file_column] = file_lookup.get(task_key, row[file_column])

            artist_column = CHARACTER_ARTIST_COLUMNS.get(sprite_type)
            if artist_column is not None:
                row[artist_column] = artist_name or artist_lookup.get(task_key, row[artist_column])

        return [CHARACTER_COMPLETED_HEADER] + [
            row for _character_name, row in sorted(character_rows.items(), key=lambda item: item[0].casefold())
        ]

    def sync_active_task_sheets(self):
        for config in TASK_SHEET_CONFIGS.values():
            worksheet = self.get_or_create_worksheet(config["active"], cols=len(ACTIVE_TASK_HEADER))
            worksheet.clear()
            worksheet.update(
                values=self.build_task_rows(config["where"], ACTIVE_TASK_STATUSES),
                range_name="A1"
            )

    def sync_completed_task_sheets(self):
        file_lookup, artist_lookup, ref_lookup = self.get_completed_sheet_lookups()
        for sheet_key, config in TASK_SHEET_CONFIGS.items():
            if sheet_key == "pokemon":
                header = POKEMON_COMPLETED_HEADER
            elif sheet_key == "character":
                header = CHARACTER_COMPLETED_HEADER
            else:
                header = COMPLETED_TASK_HEADER
            worksheet = self.get_or_create_worksheet(config["completed"], cols=len(header))
            worksheet.clear()
            if sheet_key == "pokemon":
                values = self.build_completed_pokemon_rows(file_lookup, artist_lookup, ref_lookup)
            elif sheet_key == "character":
                values = self.build_completed_character_rows(file_lookup, artist_lookup)
            else:
                values = self.build_completed_task_rows(config["where"], file_lookup)
            worksheet.update(
                values=values,
                range_name="A1",
                value_input_option="USER_ENTERED",
            )

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

            # --- 2. Sync Task Sheets ---
            self.sync_active_task_sheets()
            self.sync_completed_task_sheets()

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

    @app_commands.command(name="dbtosheets", description="Forcefully sync the database to Google Sheets (overwrites Sheets)")
    @app_commands.checks.has_role("Directors 🌇")
    async def dbtosheets(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        success, message = await self.perform_sync()
        if success:
            await interaction.followup.send("✅ Successfully synced database to Google Sheets!")
        else:
            await interaction.followup.send(f"❌ Sync failed: {message}")

    @app_commands.command(name="sheetstodb", description="Forcefully sync Google Sheets profiles and tasks to the database (overwrites DB)")
    @app_commands.checks.has_role("Directors 🌇")
    async def sheetstodb(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        success, message = await self.perform_import()
        if success:
            await interaction.followup.send(f"✅ {message}")
        else:
            await interaction.followup.send(f"❌ Sync failed: {message}")

    @commands.command(name="dbtosheets", aliases=["db2sheets", "syncsheets", "syncdata", "sync"])
    async def prefix_dbtosheets(self, ctx: commands.Context):
        is_director = False
        if isinstance(ctx.author, discord.Member):
            is_director = any(role.name == "Directors 🌇" for role in ctx.author.roles)
        
        if not is_director:
            await ctx.reply("❌ You do not have the required role to run this command.")
            return

        msg = await ctx.reply("⏳ Syncing database to Google Sheets...")
        success, message = await self.perform_sync()
        if success:
            await msg.edit(content="✅ Successfully synced database to Google Sheets!")
        else:
            await msg.edit(content=f"❌ Sync failed: {message}")

    @commands.command(name="sheetstodb", aliases=["sheets2db", "importprofiles", "importsheets"])
    async def prefix_sheetstodb(self, ctx: commands.Context):
        is_director = False
        if isinstance(ctx.author, discord.Member):
            is_director = any(role.name == "Directors 🌇" for role in ctx.author.roles)
        
        if not is_director:
            await ctx.reply("❌ You do not have the required role to run this command.")
            return

        msg = await ctx.reply("⏳ Syncing Google Sheets profiles and tasks to the database...")
        success, message = await self.perform_import()
        if success:
            await msg.edit(content=f"✅ {message}")
        else:
            await msg.edit(content=f"❌ Sync failed: {message}")

    async def perform_import(self) -> tuple[bool, str]:
        if self.gc is None:
            return False, "Google Sheets API is not connected."

        self._ignore_watcher = True
        try:
            # --- 1. Import Profiles ---
            profile_records = self.profiles_sheet.get_all_records()
            updated_threads = set()

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Import Profiles
                updated_profiles_count = 0
                for row in profile_records:
                    user_id_str = str(row.get("Discord ID", ""))
                    if not user_id_str.isdigit():
                        continue

                    try:
                        level = int(row.get("Level"))
                    except (ValueError, TypeError):
                        level = 1

                    try:
                        tasks_completed = int(row.get("Tasks Completed"))
                    except (ValueError, TypeError):
                        tasks_completed = 0

                    cursor.execute("""
                        INSERT INTO users (user_id, discord_name, pronouns, timezone, level, tasks_completed)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET
                            discord_name = excluded.discord_name,
                            pronouns = excluded.pronouns,
                            timezone = excluded.timezone,
                            level = excluded.level,
                            tasks_completed = excluded.tasks_completed
                    """, (
                        int(user_id_str),
                        row.get("Discord Name"),
                        row.get("Pronouns"),
                        row.get("Timezone"),
                        level,
                        tasks_completed
                    ))
                    updated_profiles_count += cursor.rowcount

                # Commit profiles import so we can lookup users correctly by name for tasks
                conn.commit()

                # Cache of users to map discord_name to user_id
                cursor.execute("SELECT user_id, discord_name FROM users")
                user_cache = {r[1].strip().lower(): r[0] for r in cursor.fetchall() if r[1]}

                # --- 2. Import Active Tasks ---
                imported_tasks_count = 0
                skipped_tasks_count = 0
                
                for sheet_key, config in TASK_SHEET_CONFIGS.items():
                    active_sheet_title = config["active"]
                    try:
                        worksheet = self.spreadsheet.worksheet(active_sheet_title)
                        task_records = worksheet.get_all_records()
                    except WorksheetNotFound:
                        # If worksheet doesn't exist, we skip it
                        continue
                    
                    for row in task_records:
                        task_id_str = str(row.get("Task ID", "")).strip()
                        artist_name = str(row.get("Assigned Artist", "")).strip()
                        identifier = str(row.get("Task", "")).strip()
                        sprite_type = str(row.get("Type", "")).strip()
                        variant = str(row.get("Variant", "")).strip()
                        status = str(row.get("Status", "")).strip()
                        assigned_date = str(row.get("Assigned Date", "")).strip()
                        due_date = str(row.get("Due Date", "")).strip()
                        min_level_val = row.get("Minimum Level")
                        reference_image_url = str(row.get("Reference Image", "")).strip()

                        # We need at least identifier, type, and variant to have a valid task
                        if not identifier or not sprite_type or not variant:
                            skipped_tasks_count += 1
                            continue

                        # Resolve artist to user_id
                        user_id = None
                        if artist_name and artist_name.lower() != "unassigned":
                            user_id = user_cache.get(artist_name.lower())

                        # Parse min_level
                        min_level = None
                        if min_level_val not in (None, ""):
                            try:
                                min_level = int(min_level_val)
                            except (ValueError, TypeError):
                                pass

                        # Standardize dates
                        assigned_date_val = assigned_date if assigned_date else None
                        due_date_val = due_date if due_date else None
                        ref_image_val = reference_image_url if reference_image_url else None
                        status_val = status if status else "Assigned"

                        # Check if task_id is specified
                        if task_id_str.isdigit():
                            task_id = int(task_id_str)
                            # Check if task already exists
                            cursor.execute("SELECT pokedex_identifier, forum_thread_id FROM tasks WHERE task_id = ?", (task_id,))
                            row_db = cursor.fetchone()
                            
                            if row_db:
                                old_identifier, thread_id = row_db
                                # If the name changed, rename the thread on Discord
                                if old_identifier != identifier and thread_id and thread_id not in updated_threads:
                                    try:
                                        thread = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
                                        if isinstance(thread, discord.Thread) and thread.name != identifier:
                                            await thread.edit(name=identifier[:100])
                                            await update_task_forum_summary(self.bot, self.db_path, thread_id)
                                        updated_threads.add(thread_id)
                                    except Exception as e:
                                        print(f"Failed to rename thread {thread_id} to {identifier}: {e}")

                                cursor.execute("""
                                    UPDATE tasks SET
                                        user_id = ?,
                                        sprite_type = ?,
                                        variant = ?,
                                        pokedex_identifier = ?,
                                        status = ?,
                                        assigned_date = ?,
                                        due_date = ?,
                                        min_level = ?,
                                        reference_image_url = ?
                                    WHERE task_id = ?
                                """, (user_id, sprite_type, variant, identifier, status_val, assigned_date_val, due_date_val, min_level, ref_image_val, task_id))
                            else:
                                cursor.execute("""
                                    INSERT INTO tasks (task_id, user_id, sprite_type, variant, pokedex_identifier, status, assigned_date, due_date, min_level, reference_image_url)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (task_id, user_id, sprite_type, variant, identifier, status_val, assigned_date_val, due_date_val, min_level, ref_image_val))
                        else:
                            # Insert as a new task
                            cursor.execute("""
                                INSERT INTO tasks (user_id, sprite_type, variant, pokedex_identifier, status, assigned_date, due_date, min_level, reference_image_url)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (user_id, sprite_type, variant, identifier, status_val, assigned_date_val, due_date_val, min_level, ref_image_val))
                        
                        imported_tasks_count += 1
                
                conn.commit()

            # Immediately update the last_db_mtime so the watcher doesn't catch it when we reset the ignore flag
            try:
                self.last_db_mtime = os.path.getmtime(self.db_path)
            except Exception:
                pass

            msg = f"Successfully updated/inserted {updated_profiles_count} profiles and {imported_tasks_count} tasks from Google Sheets!"
            if skipped_tasks_count > 0:
                msg += f" (Skipped {skipped_tasks_count} invalid rows)"
            return True, msg
        except Exception as e:
            return False, str(e)
        finally:
            self._ignore_watcher = False

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

    # --- Automatic File Watcher Loop ---
    @tasks.loop(seconds=5)
    async def db_file_watcher(self):
        if self.gc is None:
            return
        if not os.path.exists(self.db_path):
            return
        
        if getattr(self, "_ignore_watcher", False):
            # If we are manually importing, update last_db_mtime so we don't trigger next run either
            try:
                self.last_db_mtime = os.path.getmtime(self.db_path)
            except Exception:
                pass
            return

        try:
            mtime = os.path.getmtime(self.db_path)
        except Exception:
            return
        
        # If this is the first run, initialize self.last_db_mtime
        if self.last_db_mtime is None:
            self.last_db_mtime = mtime
            return
        
        # If mtime has changed, trigger a debounced sync
        if mtime != self.last_db_mtime:
            print(f"Database modification detected (mtime changed from {self.last_db_mtime} to {mtime}). Scheduling sync...")
            self.last_db_mtime = mtime
            self.schedule_sync()

    @db_file_watcher.before_loop
    async def before_db_file_watcher(self):
        await self.bot.wait_until_ready()

    def schedule_sync(self):
        """Schedule a database-to-sheets sync to run after a brief delay, debouncing multiple requests."""
        import asyncio
        if self.pending_sync_task:
            self.pending_sync_task.cancel()
        
        async def _delayed_sync():
            await asyncio.sleep(5)  # wait 5 seconds before running sync to let multiple updates settle
            print("Running scheduled database-to-sheets sync...")
            success, message = await self.perform_sync()
            if success:
                print("Scheduled database-to-sheets sync completed successfully.")
            else:
                print(f"Scheduled database-to-sheets sync failed: {message}")
            self.pending_sync_task = None
        
        self.pending_sync_task = asyncio.create_task(_delayed_sync())

    def cog_unload(self):
        self.auto_sync.cancel()
        self.db_file_watcher.cancel()
        if self.pending_sync_task:
            self.pending_sync_task.cancel()

async def setup(bot):
    await bot.add_cog(SheetSync(bot))
