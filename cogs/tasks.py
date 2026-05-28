import discord
from discord import app_commands
from discord.ext import commands, tasks
import sqlite3
from datetime import datetime, timedelta, timezone
from task_forum import create_task_forum_post, update_task_forum_status

AVAILABLE_TASK_STATUSES = ("Available", "Unassigned")
ACTIVE_TASK_STATUSES = ("Available", "Unassigned", "Assigned", "Waiting For Feedback")

TASK_CATEGORY_OPTIONS = {
    "pokemon_sprite": {
        "label": "Pokemon Sprite",
        "description": "Fronts, backs, shinies, anomalies, and icons",
        "emoji": "🟩",
        "placeholder": "Select a Pokemon sprite task...",
        "options": [
            discord.SelectOption(label="Base Front", value="Base_Front", emoji="🟩"),
            discord.SelectOption(label="Base Front 2", value="Base_Front 2", emoji="🟩"),
            discord.SelectOption(label="Base Back", value="Base_Back", emoji="🟩"),
            discord.SelectOption(label="Shiny Front", value="Shiny_Front", emoji="✨"),
            discord.SelectOption(label="Shiny Front 2", value="Shiny_Front 2", emoji="✨"),
            discord.SelectOption(label="Shiny Back", value="Shiny_Back", emoji="✨"),
            discord.SelectOption(label="Anomaly Front", value="Anomaly_Front", emoji="🌌"),
            discord.SelectOption(label="Anomaly Front 2", value="Anomaly_Front 2", emoji="🌌"),
            discord.SelectOption(label="Anomaly Back", value="Anomaly_Back", emoji="🌌"),
            discord.SelectOption(label="Icon", value="Base_Icon", emoji="🖼️"),
        ],
    },
    "character_sprite": {
        "label": "Character Sprite",
        "description": "Character design, overworld, and battler work",
        "emoji": "🎨",
        "placeholder": "Select a character sprite task...",
        "options": [
            discord.SelectOption(label="Character Design", value="Character_Design", emoji="🎨"),
            discord.SelectOption(label="Character Overworld", value="Character_Overworld", emoji="🧭"),
            discord.SelectOption(label="Character Battler", value="Character_Battler", emoji="⚔️"),
        ],
    },
    "sounds": {
        "label": "Sounds",
        "description": "Music, sound effects, and Pokemon cries",
        "emoji": "🎵",
        "placeholder": "Select a sound task...",
        "options": [
            discord.SelectOption(label="Music", value="Audio_Music", emoji="🎵"),
            discord.SelectOption(label="Sound Effect", value="Audio_Sound Effect", emoji="🔊"),
            discord.SelectOption(label="Pokemon Cry", value="Audio_Cry", emoji="📣"),
        ],
    },
}

POKEMON_TASK_VALUES = [option.value for option in TASK_CATEGORY_OPTIONS["pokemon_sprite"]["options"]]
CHARACTER_TASK_VALUES = [option.value for option in TASK_CATEGORY_OPTIONS["character_sprite"]["options"]]


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


# --- 2. The Dropdown Menus (Selects) ---
class TaskCategoryDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str):
        self.bot = bot
        self.db_path = db_path

        options = [
            discord.SelectOption(
                label=category["label"],
                value=key,
                description=category["description"],
                emoji=category["emoji"],
            )
            for key, category in TASK_CATEGORY_OPTIONS.items()
        ]
        super().__init__(placeholder="Select a task category...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        category_key = self.values[0]
        category = TASK_CATEGORY_OPTIONS[category_key]

        embed = discord.Embed(
            title=category["label"],
            description="Choose the specific task type you want to claim.",
            color=discord.Color.dark_grey()
        )
        view = TaskTypeView(self.bot, self.db_path, category_key)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class TaskTypeDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str, category_key: str):
        self.bot = bot
        self.db_path = db_path
        category = TASK_CATEGORY_OPTIONS[category_key]
        super().__init__(
            placeholder=category["placeholder"],
            min_values=1,
            max_values=1,
            options=category["options"],
        )

    async def callback(self, interaction: discord.Interaction):
        # We split the hidden value (e.g., "Shiny_Front 2") into variant ("Shiny") and sprite_type ("Front 2")
        variant, sprite_type = self.values[0].split('_', 1)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT task_id, pokedex_identifier
                FROM tasks
                WHERE variant = ? AND sprite_type = ? AND status IN ('Available', 'Unassigned')
                ORDER BY pokedex_identifier COLLATE NOCASE
                LIMIT 25
            """, (variant, sprite_type))
            available_tasks = cursor.fetchall()

        if not available_tasks:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title=f"{variant} {sprite_type}",
                    description="No tasks are currently available for this type.",
                    color=discord.Color.red()
                ),
                view=None
            )
            return

        embed = discord.Embed(
            title=f"{variant} {sprite_type}",
            description="Choose one of the available tasks below.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.edit_message(
            embed=embed,
            view=AvailableTaskView(self.bot, self.db_path, variant, sprite_type, available_tasks)
        )


class AvailableTaskDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str, variant: str, sprite_type: str, available_tasks):
        self.bot = bot
        self.db_path = db_path
        self.variant = variant
        self.sprite_type = sprite_type
        options = [
            discord.SelectOption(
                label=identifier[:100],
                value=str(task_id),
                description=f"{variant} {sprite_type}"[:100],
            )
            for task_id, identifier in available_tasks
        ]
        super().__init__(placeholder="Select an available task...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        task_id = int(self.values[0])
        now = datetime.now(timezone.utc)
        due_date = now + timedelta(days=7)
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT pokedex_identifier, status, forum_thread_id
                    FROM tasks
                    WHERE task_id = ? AND variant = ? AND sprite_type = ?
                """, (task_id, self.variant, self.sprite_type))
                task = cursor.fetchone()

                if not task:
                    await interaction.response.edit_message(
                        content="❌ That task no longer exists.",
                        embed=None,
                        view=None
                    )
                    return

                identifier, status, thread_id = task
                if status not in AVAILABLE_TASK_STATUSES:
                    await interaction.response.edit_message(
                        content=f"❌ **{self.variant} {self.sprite_type} — {identifier}** is no longer available.",
                        embed=None,
                        view=None
                    )
                    return

                cursor.execute("""
                    UPDATE tasks
                    SET user_id = ?, status = 'Assigned', assigned_date = ?, due_date = ?
                    WHERE task_id = ?
                """, (interaction.user.id, now.isoformat(), due_date.isoformat(), task_id))
                conn.commit()

            await update_task_bundle_forum_status(
                self.bot,
                self.db_path,
                thread_id,
                f"{interaction.user.mention} claimed this task. Due: {due_date.strftime('%b %d, %Y')}."
            )

            embed = discord.Embed(
                title="Task Accepted!",
                description=(
                    f"Thank you for accepting this task, {interaction.user.mention}!\n\n"
                    f"You will be working on **{self.variant} {self.sprite_type} — {identifier}** until **{due_date.strftime('%b %d, %Y')}**.\n\n"
                    f"*If the deadline is reached, your task will be automatically returned to the available task board.*"
                ),
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)
        except Exception as e:
            await interaction.response.edit_message(content=f"Database error: {e}", embed=None, view=None)


class AddTaskCategoryDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str):
        self.bot = bot
        self.db_path = db_path
        options = [
            discord.SelectOption(
                label=category["label"],
                value=key,
                description=category["description"],
                emoji=category["emoji"],
            )
            for key, category in TASK_CATEGORY_OPTIONS.items()
        ]
        super().__init__(placeholder="Select a task category to add...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        category_key = self.values[0]
        category = TASK_CATEGORY_OPTIONS[category_key]
        if category_key in ("pokemon_sprite", "character_sprite"):
            variant = "Character" if category_key == "character_sprite" else "Pokemon"
            await interaction.response.send_modal(AddTaskBundleModal(self.bot, self.db_path, category_key, variant))
            return

        embed = discord.Embed(
            title=f"Add {category['label']} Task",
            description="Choose the exact task type to make available.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.edit_message(embed=embed, view=AddTaskTypeView(self.bot, self.db_path, category_key))


class AddTaskBundleModal(discord.ui.Modal):
    def __init__(self, bot, db_path: str, category_key: str, bundle_type: str):
        super().__init__(title=f"Add {bundle_type} Tasks")
        self.bot = bot
        self.db_path = db_path
        self.category_key = category_key
        self.bundle_type = bundle_type

        self.identifier_input = discord.ui.TextInput(
            label=f"{bundle_type} Name or Dex Number" if bundle_type == "Pokemon" else "Character Name",
            placeholder="e.g., Corelet, 154..." if bundle_type == "Pokemon" else "e.g., protagonist, rival, shopkeeper...",
            style=discord.TextStyle.short,
            required=True,
        )
        self.add_item(self.identifier_input)

    async def on_submit(self, interaction: discord.Interaction):
        identifier = self.identifier_input.value.strip()
        if not identifier:
            await interaction.response.send_message("❌ Task name cannot be empty.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        task_values = POKEMON_TASK_VALUES if self.category_key == "pokemon_sprite" else CHARACTER_TASK_VALUES
        forum_variant = "Character" if self.category_key == "character_sprite" else "Base"
        forum_sprite_type = "Sprites"
        forum_title = identifier

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                if self.category_key == "character_sprite":
                    cursor.execute("""
                        SELECT forum_thread_id FROM tasks
                        WHERE pokedex_identifier = ? AND variant = 'Character' AND forum_thread_id IS NOT NULL
                          AND status IN ('Available', 'Unassigned', 'Assigned', 'Waiting For Feedback', 'Completed')
                        LIMIT 1
                    """, (identifier,))
                else:
                    cursor.execute("""
                        SELECT forum_thread_id FROM tasks
                        WHERE pokedex_identifier = ? AND variant IN ('Base', 'Shiny', 'Anomaly') AND forum_thread_id IS NOT NULL
                          AND status IN ('Available', 'Unassigned', 'Assigned', 'Waiting For Feedback', 'Completed')
                        LIMIT 1
                    """, (identifier,))
                existing_thread = cursor.fetchone()
                thread_id = existing_thread[0] if existing_thread else None

            if thread_id is None:
                thread = await create_task_forum_post(
                    self.bot,
                    forum_variant,
                    forum_sprite_type,
                    identifier,
                    title=forum_title
                )
                thread_id = thread.id if thread else None

            added_count = 0
            skipped_count = 0
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                for value in task_values:
                    variant, sprite_type = value.split("_", 1)
                    cursor.execute("""
                        SELECT task_id FROM tasks
                        WHERE pokedex_identifier = ? AND sprite_type = ? AND variant = ?
                          AND status IN ('Available', 'Unassigned', 'Assigned', 'Waiting For Feedback', 'Completed')
                    """, (identifier, sprite_type, variant))
                    if cursor.fetchone():
                        skipped_count += 1
                        continue

                    cursor.execute("""
                        INSERT INTO tasks (user_id, sprite_type, variant, pokedex_identifier, status, assigned_date, due_date, forum_thread_id)
                        VALUES (NULL, ?, ?, ?, 'Available', NULL, NULL, ?)
                    """, (sprite_type, variant, identifier, thread_id))
                    added_count += 1
                conn.commit()

            await update_task_bundle_forum_status(self.bot, self.db_path, thread_id)
            await interaction.followup.send(
                f"✅ Added **{added_count}** available {self.bundle_type} tasks for **{identifier}**."
                f"{' Skipped ' + str(skipped_count) + ' existing tasks.' if skipped_count else ''}",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"Database error: {e}", ephemeral=True)


class AddTaskTypeDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str, category_key: str):
        self.bot = bot
        self.db_path = db_path
        category = TASK_CATEGORY_OPTIONS[category_key]
        super().__init__(
            placeholder=category["placeholder"],
            min_values=1,
            max_values=1,
            options=category["options"],
        )

    async def callback(self, interaction: discord.Interaction):
        variant, sprite_type = self.values[0].split("_", 1)
        await interaction.response.send_modal(AddAvailableTaskModal(self.bot, self.db_path, variant, sprite_type))


class AddAvailableTaskModal(discord.ui.Modal):
    def __init__(self, bot, db_path: str, variant: str, sprite_type: str):
        super().__init__(title=f"Add: {variant} {sprite_type}")
        self.bot = bot
        self.db_path = db_path
        self.variant = variant
        self.sprite_type = sprite_type

        label = "Pokemon Name or Dex Number"
        placeholder = "e.g., Corelet, 154..."
        if variant == "Character":
            label = "Character Name"
            placeholder = "e.g., protagonist, rival, shopkeeper..."
        elif variant == "Audio" and sprite_type == "Music":
            label = "Track or Cue Name"
            placeholder = "e.g., Wild Battle, Route 1..."
        elif variant == "Audio" and sprite_type == "Sound Effect":
            label = "Sound Effect Name"
            placeholder = "e.g., menu confirm, door open..."
        elif variant == "Audio" and sprite_type == "Cry":
            label = "Pokemon or Cry Name"
            placeholder = "e.g., Corelet cry..."

        self.identifier_input = discord.ui.TextInput(
            label=label,
            placeholder=placeholder,
            style=discord.TextStyle.short,
            required=True,
        )
        self.add_item(self.identifier_input)

    async def on_submit(self, interaction: discord.Interaction):
        identifier = self.identifier_input.value.strip()
        if not identifier:
            await interaction.response.send_message("❌ Task name cannot be empty.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT task_id, status, user_id FROM tasks
                    WHERE pokedex_identifier = ? AND sprite_type = ? AND variant = ?
                      AND status IN ('Available', 'Unassigned', 'Assigned', 'Waiting For Feedback')
                """, (identifier, self.sprite_type, self.variant))
                existing_task = cursor.fetchone()

                if existing_task:
                    task_id, status, user_id = existing_task
                    assigned_text = f" to <@{user_id}>" if user_id else ""
                    await interaction.followup.send(
                        f"❌ **{self.variant} {self.sprite_type} — {identifier}** already exists as **{status}**{assigned_text}.",
                        ephemeral=True
                    )
                    return

                cursor.execute("""
                    INSERT INTO tasks (user_id, sprite_type, variant, pokedex_identifier, status, assigned_date, due_date, forum_thread_id)
                    VALUES (NULL, ?, ?, ?, 'Available', NULL, NULL, NULL)
                """, (self.sprite_type, self.variant, identifier))
                task_id = cursor.lastrowid
                conn.commit()

            thread = await create_task_forum_post(self.bot, self.variant, self.sprite_type, identifier)
            if thread:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE tasks SET forum_thread_id = ? WHERE task_id = ?", (thread.id, task_id))
                    conn.commit()

            await interaction.followup.send(
                f"✅ Added **{self.variant} {self.sprite_type} — {identifier}** to the available task board.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"Database error: {e}", ephemeral=True)


class RequestFeedbackDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str, assigned_tasks):
        self.bot = bot
        self.db_path = db_path
        options = [
            discord.SelectOption(
                label=f"{variant} {sprite_type} - {identifier}"[:100],
                value=str(task_id),
                description="Mark ready for review",
            )
            for task_id, variant, sprite_type, identifier in assigned_tasks
        ]
        super().__init__(placeholder="Select a task to send for feedback...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        task_id = int(self.values[0])

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT variant, sprite_type, pokedex_identifier, forum_thread_id
                    FROM tasks
                    WHERE task_id = ? AND user_id = ? AND status = 'Assigned'
                """, (task_id, interaction.user.id))
                task = cursor.fetchone()

                if not task:
                    await interaction.response.edit_message(
                        content="❌ That task is no longer assigned to you.",
                        embed=None,
                        view=None
                    )
                    return

                variant, sprite_type, identifier, thread_id = task
                cursor.execute("UPDATE tasks SET status = 'Waiting For Feedback' WHERE task_id = ?", (task_id,))
                conn.commit()

            await update_task_bundle_forum_status(
                self.bot,
                self.db_path,
                thread_id,
                f"{interaction.user.mention} marked this task as Waiting for Feedback."
            )

            embed = discord.Embed(
                title="Ready For Feedback",
                description=f"✅ **{variant} {sprite_type} — {identifier}** has been marked as **Waiting For Feedback**.",
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)
        except Exception as e:
            await interaction.response.edit_message(content=f"Database error: {e}", embed=None, view=None)

class Tasks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "data/corelet.db"
        # Start the background loop when the cog is loaded
        self.check_deadlines.start() 

    def execute_query(self, query, params=()):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.lastrowid

    def fetch_query(self, query, params=()):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()

    async def dm_user(self, user_id: int, message: str):
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            if user:
                await user.send(message)
        except discord.HTTPException:
            print(f"Could not DM user {user_id}.")

    async def send_active_tasks(self, interaction: discord.Interaction):
        try:
            # Fetch available, assigned, and waiting tasks.
            active_tasks = self.fetch_query("""
                SELECT user_id, sprite_type, variant, pokedex_identifier, due_date, status 
                FROM tasks 
                WHERE status IN ('Available', 'Unassigned', 'Assigned', 'Waiting For Feedback')
                ORDER BY status DESC, due_date ASC
            """)

            if not active_tasks:
                await interaction.response.send_message("❌ There are currently no active tasks.", ephemeral=True)
                return

            embed = discord.Embed(
                title="⚔️ Current Sprite Tasks ⚔️",
                description="Here are the tasks currently in progress or awaiting review:",
                color=discord.Color.blue()
            )

            for task in active_tasks[:25]:
                user_id, sprite_type, variant, identifier, due_date_str, status = task
                formatted_date = "Not claimed"
                if due_date_str:
                    due_date = datetime.fromisoformat(due_date_str)
                    formatted_date = due_date.strftime('%b %d, %Y')
                
                # Add an emoji indicator based on the status
                status_icon = "📌"
                if status == "Waiting For Feedback":
                    status_icon = "⏳"
                elif status == "Assigned":
                    status_icon = "🛠️"
                elif status in AVAILABLE_TASK_STATUSES:
                    status_icon = "✅"
                assigned_text = f"<@{user_id}>" if user_id else "Available"

                embed.add_field(
                    name=f"{status_icon} {variant} {sprite_type} — {identifier}",
                    value=f"**Assigned to:** {assigned_text}\n**Status:** {status}\n**Due:** {formatted_date}",
                    inline=False
                )

            if len(active_tasks) > 25:
                embed.set_footer(text=f"Showing 25 of {len(active_tasks)} active tasks.")

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)

    @app_commands.command(name="assigntask", description="Assign a sprite task to a user")
    @app_commands.checks.has_role("Directors")
    @app_commands.describe(
        assignee="The user doing the task",
        identifier="Pokemon name or Dex number",
        sprite_type="Front, Back, Icon, etc.",
        variant="Base, Shiny, Anomaly, etc."
    )
    async def assigntask(self, interaction: discord.Interaction, assignee: discord.Member, identifier: str, sprite_type: str, variant: str):
        # NEW: Check if this specific task is already assigned to someone
        existing_task = self.fetch_query("""
            SELECT user_id FROM tasks 
            WHERE sprite_type = ? AND variant = ? AND pokedex_identifier = ? AND status = 'Assigned'
        """, (sprite_type, variant, identifier))

        if existing_task:
            assigned_user_id = existing_task[0][0]
            await interaction.response.send_message(
                f"❌ **{variant} {sprite_type} {identifier}** is already assigned to <@{assigned_user_id}>!", 
                ephemeral=True
            )
            return

        # Set deadlines: 7 days from now
        now = datetime.now(timezone.utc)
        due_date = now + timedelta(days=7)
        
        thread_id = interaction.channel.id if isinstance(interaction.channel, discord.Thread) else None

        try:
            task_id = self.execute_query("""
                INSERT INTO tasks (user_id, sprite_type, variant, pokedex_identifier, status, assigned_date, due_date, forum_thread_id)
                VALUES (?, ?, ?, ?, 'Assigned', ?, ?, ?)
            """, (assignee.id, sprite_type, variant, identifier, now.isoformat(), due_date.isoformat(), thread_id))

            if thread_id is None:
                thread = await create_task_forum_post(self.bot, variant, sprite_type, identifier)
                if thread:
                    thread_id = thread.id
                    self.execute_query("UPDATE tasks SET forum_thread_id = ? WHERE task_id = ?", (thread_id, task_id))

            await update_task_forum_status(
                self.bot,
                thread_id,
                "Assigned",
                f"{assignee.mention} was assigned this task. Due: {due_date.strftime('%b %d, %Y')}."
            )

            await interaction.response.send_message(f"✅ Assigned **{variant} {sprite_type} {identifier}** to {assignee.mention}. Due by: {due_date.strftime('%Y-%m-%d')}.")
        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)

    @app_commands.command(name="addavailabletask", description="Add a task that members can claim from the task board")
    @app_commands.checks.has_role("Directors")
    @app_commands.describe(
        identifier="Pokemon, character, track, sound effect, or cry name",
        sprite_type="Front, Back, Music, Sound Effect, Cry, Design, etc.",
        variant="Base, Shiny, Anomaly, Audio, Character, etc."
    )
    async def addavailabletask(self, interaction: discord.Interaction, identifier: str, sprite_type: str, variant: str):
        existing_task = self.fetch_query("""
            SELECT task_id, status, user_id FROM tasks
            WHERE pokedex_identifier = ? AND sprite_type = ? AND variant = ?
              AND status IN ('Available', 'Unassigned', 'Assigned', 'Waiting For Feedback')
        """, (identifier, sprite_type, variant))

        if existing_task:
            task_id, status, user_id = existing_task[0]
            assigned_text = f" to <@{user_id}>" if user_id else ""
            await interaction.response.send_message(
                f"❌ **{variant} {sprite_type} — {identifier}** already exists as **{status}**{assigned_text}.",
                ephemeral=True
            )
            return

        try:
            task_id = self.execute_query("""
                INSERT INTO tasks (user_id, sprite_type, variant, pokedex_identifier, status, assigned_date, due_date, forum_thread_id)
                VALUES (NULL, ?, ?, ?, 'Available', NULL, NULL, NULL)
            """, (sprite_type, variant, identifier))
            thread = await create_task_forum_post(self.bot, variant, sprite_type, identifier)
            if thread:
                self.execute_query("UPDATE tasks SET forum_thread_id = ? WHERE task_id = ?", (thread.id, task_id))
            await interaction.response.send_message(
                f"✅ Added **{variant} {sprite_type} — {identifier}** to the available task board.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)

    @app_commands.command(name="addtaskmenu", description="Open a menu for adding available tasks")
    @app_commands.checks.has_role("Directors")
    async def addtaskmenu(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Add Available Task",
            description="Choose the category and type, then enter the task name.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.send_message(
            embed=embed,
            view=AddTaskCategoryView(self.bot, self.db_path),
            ephemeral=True
        )

    @app_commands.command(name="removeavailabletask", description="Remove an unclaimed task from the available task board")
    @app_commands.checks.has_role("Directors")
    @app_commands.describe(
        identifier="Pokemon, character, track, sound effect, or cry name",
        sprite_type="Front, Back, Music, Sound Effect, Cry, Design, etc.",
        variant="Base, Shiny, Anomaly, Audio, Character, etc."
    )
    async def removeavailabletask(self, interaction: discord.Interaction, identifier: str, sprite_type: str, variant: str):
        task = self.fetch_query("""
            SELECT task_id, forum_thread_id FROM tasks
            WHERE pokedex_identifier = ? AND sprite_type = ? AND variant = ?
              AND status IN ('Available', 'Unassigned')
        """, (identifier, sprite_type, variant))

        if not task:
            await interaction.response.send_message(
                f"❌ Could not find an unclaimed available task for **{variant} {sprite_type} — {identifier}**.",
                ephemeral=True
            )
            return

        try:
            task_id, thread_id = task[0]
            self.execute_query("UPDATE tasks SET status = 'Removed' WHERE task_id = ?", (task_id,))
            await update_task_bundle_forum_status(
                self.bot,
                self.db_path,
                thread_id,
                "This task was removed from the available task board."
            )
            await interaction.response.send_message(
                f"✅ Removed **{variant} {sprite_type} — {identifier}** from the available task board.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)

    @app_commands.command(name="availabletasks", description="List tasks currently available to claim")
    @app_commands.checks.has_role("Directors")
    async def availabletasks(self, interaction: discord.Interaction):
        rows = self.fetch_query("""
            SELECT variant, sprite_type, pokedex_identifier, status
            FROM tasks
            WHERE status IN ('Available', 'Unassigned')
            ORDER BY variant, sprite_type, pokedex_identifier COLLATE NOCASE
            LIMIT 50
        """)

        if not rows:
            await interaction.response.send_message("❌ There are no available tasks right now.", ephemeral=True)
            return

        lines = [f"**{variant} {sprite_type}** — {identifier} ({status})" for variant, sprite_type, identifier, status in rows]
        embed = discord.Embed(
            title="Available Tasks",
            description="\n".join(lines),
            color=discord.Color.green()
        )
        if len(rows) == 50:
            embed.set_footer(text="Showing the first 50 available tasks.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="canceltask", description="Cancel an active assignment (Assignee or Director)")
    @app_commands.describe(
        identifier="Pokemon name or Dex number",
        sprite_type="Front, Back, Icon, etc.",
        variant="Base, Shiny, Anomaly, etc."
    )
    async def canceltask(self, interaction: discord.Interaction, identifier: str, sprite_type: str, variant: str):
        # Find the specific task
        task = self.fetch_query("""
            SELECT task_id, user_id, forum_thread_id FROM tasks 
            WHERE pokedex_identifier = ? AND sprite_type = ? AND variant = ? AND status = 'Assigned'
        """, (identifier, sprite_type, variant))

        if not task:
            await interaction.response.send_message(
                f"❌ Could not find an active assignment for **{variant} {sprite_type} {identifier}**.", 
                ephemeral=True
            )
            return

        task_id = task[0][0]
        assigned_user = task[0][1]
        thread_id = task[0][2]

        # --- PERMISSION CHECK ---
        is_assignee = (interaction.user.id == assigned_user)
        # interaction.user.roles is a list of discord.Role objects, we check if any have the name "Director"
        is_director = any(role.name == "Directors" for role in interaction.user.roles)

        if not (is_assignee or is_director):
            await interaction.response.send_message(
                "❌ You do not have permission to cancel this task. Only the assigned artist or a Director can cancel it.", 
                ephemeral=True
            )
            return

        # --- EXECUTE CANCELLATION ---
        try:
            self.execute_query("""
                UPDATE tasks
                SET status = 'Available', user_id = NULL, assigned_date = NULL, due_date = NULL
                WHERE task_id = ?
            """, (task_id,))
            await update_task_bundle_forum_status(
                self.bot,
                self.db_path,
                thread_id,
                f"This task was returned to Missing by {interaction.user.mention}."
            )
            
            # Send a different confirmation message depending on who cancelled it
            if is_director and not is_assignee:
                await interaction.response.send_message(f"✅ **(Director Override)** The task **{variant} {sprite_type} {identifier}** has been removed from <@{assigned_user}>.")
            else:
                await interaction.response.send_message(f"✅ You have successfully cancelled your assignment for **{variant} {sprite_type} {identifier}**.")
                
        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)

    # Background loop that runs every 24 hours
    @tasks.loop(hours=24)
    async def check_deadlines(self):
        print("Running daily deadline check...")
        now = datetime.now(timezone.utc)
        
        # Fetch all tasks that are still "Assigned"
        active_tasks = self.fetch_query("SELECT task_id, user_id, assigned_date, due_date, forum_thread_id FROM tasks WHERE status = 'Assigned'")

        for task in active_tasks:
            task_id, user_id, assigned_date_str, due_date_str, thread_id = task
            assigned_date = datetime.fromisoformat(assigned_date_str)
            due_date = datetime.fromisoformat(due_date_str)

            days_since_assigned = (now - assigned_date).days

            # 1-Week Check: Deadline passed
            if now > due_date:
                # Remove task from assigned status
                self.execute_query("""
                    UPDATE tasks
                    SET status = 'Available', user_id = NULL, assigned_date = NULL, due_date = NULL
                    WHERE task_id = ?
                """, (task_id,))
                await update_task_bundle_forum_status(
                    self.bot,
                    self.db_path,
                    thread_id,
                    f"<@{user_id}> missed the deadline. This task is Missing again."
                )
                await self.dm_user(
                    user_id,
                    "Your task deadline has passed, so the task has been automatically unassigned. "
                    "Please talk to a Director or Verifier if you still need it."
                )
                # If we have the thread ID, send a message there
                if thread_id:
                    thread = self.bot.get_channel(thread_id)
                    if thread:
                        await thread.send(f"<@{user_id}> 7 days have passed with no updates. This task has been automatically unassigned.")
            
            # 3-Day Check: Warning
            elif days_since_assigned == 3:
                await self.dm_user(
                    user_id,
                    "Task reminder: please post an update on your assigned task. "
                    "If you need more time, ask a Verifier or Director to extend your deadline."
                )
                if thread_id:
                    thread = self.bot.get_channel(thread_id)
                    if thread:
                        await thread.send(f"<@{user_id}> Just a 3-day check-in! Please provide an update on your progress. If you need more time, a Verifier can extend your deadline.")

    @check_deadlines.before_loop
    async def before_check_deadlines(self):
        # Wait until the bot is fully logged in before starting the timer loop
        await self.bot.wait_until_ready()

    @app_commands.command(name="showtasks", description="View all currently active sprite tasks")
    async def showtasks(self, interaction: discord.Interaction):
        await self.send_active_tasks(interaction)

    @app_commands.command(name="opentasks", description="Check all currently open Pokemon tasks")
    async def opentasks(self, interaction: discord.Interaction):
        await self.send_active_tasks(interaction)

    @app_commands.command(name="workingon", description="See what everyone is currently working on")
    @app_commands.checks.has_role("Directors")
    async def workingon(self, interaction: discord.Interaction):
        try:
            rows = self.fetch_query("""
                SELECT t.user_id, COALESCE(u.discord_name, 'Unknown User'), t.sprite_type, t.variant,
                       t.pokedex_identifier, t.due_date, t.status
                FROM tasks t
                LEFT JOIN users u ON t.user_id = u.user_id
                WHERE t.status IN ('Assigned', 'Waiting For Feedback')
                ORDER BY u.discord_name COLLATE NOCASE, t.due_date ASC
            """)

            if not rows:
                await interaction.response.send_message("❌ Nobody is currently working on an active task.", ephemeral=True)
                return

            lines = []
            for user_id, name, sprite_type, variant, identifier, due_date_str, status in rows:
                due_text = "No due date"
                if due_date_str:
                    due_text = datetime.fromisoformat(due_date_str).strftime('%b %d, %Y')
                lines.append(f"**{name}** (<@{user_id}>): {variant} {sprite_type} — {identifier} | {status} | Due {due_text}")

            description = "\n".join(lines)
            if len(description) > 3900:
                description = description[:3900] + "\n...and more."

            embed = discord.Embed(
                title="Everyone's Active Work",
                description=description,
                color=discord.Color.dark_teal()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)

    @app_commands.command(name="extenddeadline", description="Extend a task's due date (Verifier/Director)")
    @app_commands.checks.has_any_role("Sprite Verifier", "Directors") # Allows either role
    @app_commands.describe(
        identifier="Pokemon name or Dex number",
        sprite_type="Front, Back, Icon, etc.",
        variant="Base, Shiny, Anomaly, etc.",
        days="How many extra days to give them"
    )
    async def extenddeadline(self, interaction: discord.Interaction, identifier: str, sprite_type: str, variant: str, days: int):
        task = self.fetch_query("""
            SELECT task_id, user_id, due_date FROM tasks 
            WHERE pokedex_identifier = ? AND sprite_type = ? AND variant = ? AND status = 'Assigned'
        """, (identifier, sprite_type, variant))

        if not task:
            await interaction.response.send_message(
                f"❌ Could not find an active assignment for **{variant} {sprite_type} {identifier}**.", 
                ephemeral=True
            )
            return

        task_id, user_id, due_date_str = task[0]
        
        # Calculate the new date
        current_due_date = datetime.fromisoformat(due_date_str)
        new_due_date = current_due_date + timedelta(days=days)

        try:
            self.execute_query("UPDATE tasks SET due_date = ? WHERE task_id = ?", (new_due_date.isoformat(), task_id))
            await interaction.response.send_message(
                f"✅ Extended the deadline for **{variant} {sprite_type} {identifier}** by **{days} days**! "
                f"\n<@{user_id}>, your new due date is: {new_due_date.strftime('%b %d, %Y')}."
            )
        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)
            
# Error handler for role checks in this Cog
    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingRole) or isinstance(error, app_commands.MissingAnyRole):
            await interaction.response.send_message("❌ You do not have the required role to use this command.", ephemeral=True)
        else:
            await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)
    

    @app_commands.command(name="requestfeedback", description="Mark your task as ready for Verifier review")
    @app_commands.describe(
        identifier="Pokemon name or Dex number",
        sprite_type="Front, Back, Icon, etc.",
        variant="Base, Shiny, Anomaly, etc."
    )
    async def requestfeedback(self, interaction: discord.Interaction, identifier: str, sprite_type: str, variant: str):
        # Verify the user actually owns this active assignment
        task = self.fetch_query("""
            SELECT task_id, forum_thread_id FROM tasks 
            WHERE user_id = ? AND pokedex_identifier = ? AND sprite_type = ? AND variant = ? AND status = 'Assigned'
        """, (interaction.user.id, identifier, sprite_type, variant))

        if not task:
            await interaction.response.send_message(
                f"❌ You do not have an active 'Assigned' task for **{variant} {sprite_type} {identifier}**. (It may already be marked for feedback).", 
                ephemeral=True
            )
            return

        task_id = task[0][0]
        thread_id = task[0][1]

        try:
            # Update the status in the database
            self.execute_query("UPDATE tasks SET status = 'Waiting For Feedback' WHERE task_id = ?", (task_id,))
            await update_task_bundle_forum_status(
                self.bot,
                self.db_path,
                thread_id,
                f"{interaction.user.mention} marked this task as Waiting for Feedback."
            )
            
            await interaction.response.send_message(f"✅ **{variant} {sprite_type} {identifier}** has been marked as **Waiting For Feedback**!")
            
            # If this was done in the forum thread, optionally ping the Verifier role
            if thread_id:
                thread = self.bot.get_channel(thread_id)
                if thread:
                    # Note: You'll need to replace with your actual Verifier Role ID to ping the role properly
                    # e.g., await thread.send("<@&YOUR_VERIFIER_ROLE_ID> a new sprite is ready for review!")
                    pass

        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)

    @app_commands.command(name="requestfeedbackmenu", description="Choose one of your assigned tasks to send for review")
    async def requestfeedbackmenu(self, interaction: discord.Interaction):
        assigned_tasks = self.fetch_query("""
            SELECT task_id, variant, sprite_type, pokedex_identifier
            FROM tasks
            WHERE user_id = ? AND status = 'Assigned'
            ORDER BY due_date ASC
            LIMIT 25
        """, (interaction.user.id,))

        if not assigned_tasks:
            await interaction.response.send_message("❌ You do not have any assigned tasks ready to request feedback for.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Request Feedback",
            description="Choose the task you want to send for verifier review.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.send_message(
            embed=embed,
            view=RequestFeedbackView(self.bot, self.db_path, assigned_tasks),
            ephemeral=True
        )

    @app_commands.command(name="spawnmenu", description="Generate the interactive task assignment board")
    @app_commands.checks.has_role("Directors") # Only admins should be able to print the menu
    async def spawnmenu(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Void Dev Task Board",
            description="Select a category below to claim a new task.",
            color=discord.Color.dark_grey()
        )
        view = TaskBoardView(self.bot, self.db_path)
        
        # Send the menu to the channel
        await interaction.channel.send(embed=embed, view=view)
        # Quietly acknowledge the slash command so it doesn't say "Interaction Failed"
        await interaction.response.send_message("Menu spawned.", ephemeral=True)

class TaskBoardView(discord.ui.View):
    def __init__(self, bot, db_path: str):
        # timeout=None is CRITICAL. It means this menu will stay active forever, 
        # even if the bot restarts.
        super().__init__(timeout=None) 
        self.add_item(TaskCategoryDropdown(bot, db_path))


class TaskTypeView(discord.ui.View):
    def __init__(self, bot, db_path: str, category_key: str):
        super().__init__(timeout=300)
        self.add_item(TaskTypeDropdown(bot, db_path, category_key))


class AvailableTaskView(discord.ui.View):
    def __init__(self, bot, db_path: str, variant: str, sprite_type: str, available_tasks):
        super().__init__(timeout=300)
        self.add_item(AvailableTaskDropdown(bot, db_path, variant, sprite_type, available_tasks))


class AddTaskCategoryView(discord.ui.View):
    def __init__(self, bot, db_path: str):
        super().__init__(timeout=300)
        self.add_item(AddTaskCategoryDropdown(bot, db_path))


class AddTaskTypeView(discord.ui.View):
    def __init__(self, bot, db_path: str, category_key: str):
        super().__init__(timeout=300)
        self.add_item(AddTaskTypeDropdown(bot, db_path, category_key))


class RequestFeedbackView(discord.ui.View):
    def __init__(self, bot, db_path: str, assigned_tasks):
        super().__init__(timeout=300)
        self.add_item(RequestFeedbackDropdown(bot, db_path, assigned_tasks))

async def setup(bot):
    await bot.add_cog(Tasks(bot))
