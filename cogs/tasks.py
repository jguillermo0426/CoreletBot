import discord
from discord import app_commands
from discord.ext import commands, tasks
import sqlite3
import re
import os
from datetime import datetime, timedelta, timezone
from task_forum import create_task_forum_post, update_task_forum_status, update_task_forum_summary

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
        "label": "Character",
        "description": "Character design, overworld, and battler work",
        "emoji": "🎨",
        "placeholder": "Select a character task...",
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


def has_director_role(user) -> bool:
    return any(role.name == "Directors 🌇" for role in getattr(user, "roles", []))


def normalize_pokemon_identifier(identifier: str):
    match = re.fullmatch(r"\s*(\d+)\s*-\s*(\S.*?)\s*", identifier)
    if not match:
        return None

    dex_number, pokemon_name = match.groups()
    return f"{dex_number} - {pokemon_name}"


def discord_access_error_message(error: discord.Forbidden) -> str:
    return (
        "Discord permission error: I do not have access to one of the channels or threads needed for that action. "
        f"Details: {error}"
    )


def get_task_request_channel_ids():
    raw_values = [
        os.getenv("TASK_REQUEST_CHANNEL_ID"),
        os.getenv("TASK_REQUEST_CHANNEL_IDS"),
        os.getenv("TASK_BOARD_CHANNEL_ID"),
    ]
    channel_ids = set()
    for raw_value in raw_values:
        if not raw_value:
            continue

        for value in raw_value.replace(";", ",").split(","):
            value = value.strip()
            if value.isdigit():
                channel_ids.add(int(value))

    return channel_ids


def fetch_active_task_rows(db_path: str, user_id: int | None = None):
    params = []
    user_filter = ""
    statuses = ACTIVE_TASK_STATUSES
    if user_id is not None:
        user_filter = "AND user_id = ?"
        params.append(user_id)
        statuses = ("Assigned", "Waiting For Feedback")

    placeholders = ", ".join("?" for _ in statuses)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT user_id, sprite_type, variant, pokedex_identifier, due_date, status, min_level, reference_image_url
            FROM tasks
            WHERE status IN ({placeholders})
              {user_filter}
            ORDER BY status DESC, due_date ASC, variant, sprite_type, pokedex_identifier COLLATE NOCASE
        """, (*statuses, *params))
        return cursor.fetchall()


def parse_min_level(value: str):
    value = value.strip()
    if not value:
        return None
    if not value.isdigit():
        raise ValueError("Minimum level must be a whole number.")

    min_level = int(value)
    if min_level < 1:
        raise ValueError("Minimum level must be 1 or higher.")
    return min_level


def format_min_level(min_level):
    return f"Lv {min_level}+" if min_level else "No level req"


def normalize_reference_image_url(value: str | None):
    if value is None:
        return None

    value = value.strip()
    return value or None


def format_reference_image(reference_image_url):
    return f"[Open reference]({reference_image_url})" if reference_image_url else "None"


def fetch_user_level(cursor, user_id: int):
    cursor.execute("SELECT level FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 1


def check_user_min_level(cursor, user_id: int, min_level):
    user_level = fetch_user_level(cursor, user_id)
    if min_level and user_level < min_level:
        return False, user_level
    return True, user_level


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

    await update_task_forum_status(bot, thread_id, aggregate_status, message)
    await update_task_forum_summary(bot, db_path, thread_id)


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
        await interaction.response.send_message(embed=embed, view=view)


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
                SELECT task_id, pokedex_identifier, min_level
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
                description=f"{variant} {sprite_type} | {format_min_level(min_level)}"[:100],
            )
            for task_id, identifier, min_level in available_tasks
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
                    SELECT pokedex_identifier, status, forum_thread_id, min_level
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

                identifier, status, thread_id, min_level = task
                if status not in AVAILABLE_TASK_STATUSES:
                    await interaction.response.edit_message(
                        content=f"❌ **{self.variant} {self.sprite_type} — {identifier}** is no longer available.",
                        embed=None,
                        view=None
                    )
                    return

                has_level, user_level = check_user_min_level(cursor, interaction.user.id, min_level)
                if not has_level:
                    await interaction.response.edit_message(
                        content=(
                            f"❌ **{self.variant} {self.sprite_type} — {identifier}** requires "
                            f"**Level {min_level}**. You are currently **Level {user_level}**."
                        ),
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
        except discord.Forbidden as e:
            await interaction.response.edit_message(content=discord_access_error_message(e), embed=None, view=None)
        except Exception as e:
            await interaction.response.edit_message(content=f"Database error: {e}", embed=None, view=None)


class ThreadAvailableTaskDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str, available_tasks):
        self.bot = bot
        self.db_path = db_path
        options = [
            discord.SelectOption(
                label=f"{variant} {sprite_type}"[:100],
                value=str(task_id),
                description=f"{identifier} | {format_min_level(min_level)}"[:100],
            )
            for task_id, variant, sprite_type, identifier, min_level in available_tasks
        ]
        super().__init__(placeholder="Select a task from this thread...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        task_id = int(self.values[0])
        now = datetime.now(timezone.utc)
        due_date = now + timedelta(days=7)

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT variant, sprite_type, pokedex_identifier, status, forum_thread_id, min_level
                    FROM tasks
                    WHERE task_id = ?
                """, (task_id,))
                task = cursor.fetchone()

                if not task:
                    await interaction.response.edit_message(
                        content="❌ That task no longer exists.",
                        embed=None,
                        view=None
                    )
                    return

                variant, sprite_type, identifier, status, thread_id, min_level = task
                if status not in AVAILABLE_TASK_STATUSES:
                    await interaction.response.edit_message(
                        content=f"❌ **{variant} {sprite_type} — {identifier}** is no longer available.",
                        embed=None,
                        view=None
                    )
                    return

                if isinstance(interaction.channel, discord.Thread) and thread_id != interaction.channel.id:
                    await interaction.response.edit_message(
                        content="❌ That task is not part of this forum thread.",
                        embed=None,
                        view=None
                    )
                    return

                has_level, user_level = check_user_min_level(cursor, interaction.user.id, min_level)
                if not has_level:
                    await interaction.response.edit_message(
                        content=(
                            f"❌ **{variant} {sprite_type} — {identifier}** requires "
                            f"**Level {min_level}**. You are currently **Level {user_level}**."
                        ),
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
                    f"You will be working on **{variant} {sprite_type} — {identifier}** until **{due_date.strftime('%b %d, %Y')}**.\n\n"
                    f"*If the deadline is reached, your task will be automatically returned to the available task board.*"
                ),
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)
        except discord.Forbidden as e:
            await interaction.response.edit_message(content=discord_access_error_message(e), embed=None, view=None)
        except Exception as e:
            await interaction.response.edit_message(content=f"Database error: {e}", embed=None, view=None)


class AssignTaskUserSelect(discord.ui.UserSelect):
    def __init__(self, bot, db_path: str, director_id: int):
        self.bot = bot
        self.db_path = db_path
        self.director_id = director_id
        super().__init__(placeholder="Select the assignee...", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.director_id or not has_director_role(interaction.user):
            await interaction.response.send_message("❌ Only the Director who opened this menu can use it.", ephemeral=True)
            return

        assignee = self.values[0]
        if isinstance(interaction.channel, discord.Thread):
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT task_id, variant, sprite_type, pokedex_identifier, min_level
                    FROM tasks
                    WHERE forum_thread_id = ?
                      AND status IN ('Available', 'Unassigned')
                    ORDER BY variant, sprite_type, pokedex_identifier COLLATE NOCASE
                    LIMIT 25
                """, (interaction.channel.id,))
                available_tasks = cursor.fetchall()

            if available_tasks:
                assignee_name = getattr(assignee, "display_name", assignee.name)
                embed = discord.Embed(
                    title=getattr(interaction.channel, "name", "Assign Task"),
                    description=f"Assigning to {assignee.mention}. Choose one of this thread's available tasks.",
                    color=discord.Color.dark_grey()
                )
                await interaction.response.edit_message(
                    embed=embed,
                    view=AssignThreadAvailableTaskView(
                        self.bot,
                        self.db_path,
                        available_tasks,
                        assignee.id,
                        assignee_name,
                        assignee.mention,
                        self.director_id
                    )
                )
                return

        category_options = [
            discord.SelectOption(
                label=category["label"],
                value=key,
                description=category["description"],
                emoji=category["emoji"],
            )
            for key, category in TASK_CATEGORY_OPTIONS.items()
        ]
        embed = discord.Embed(
            title="Assign Task",
            description=f"Assigning to {assignee.mention}. Choose a task category.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.edit_message(
            embed=embed,
            view=AssignTaskCategoryView(
                self.bot,
                self.db_path,
                assignee.id,
                getattr(assignee, "display_name", assignee.name),
                assignee.mention,
                self.director_id,
                category_options
            )
        )


class AssignTaskCategoryDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str, assignee_id: int, assignee_name: str, assignee_mention: str, director_id: int, options):
        self.bot = bot
        self.db_path = db_path
        self.assignee_id = assignee_id
        self.assignee_name = assignee_name
        self.assignee_mention = assignee_mention
        self.director_id = director_id
        super().__init__(placeholder="Select a task category to assign...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.director_id or not has_director_role(interaction.user):
            await interaction.response.send_message("❌ Only the Director who opened this menu can use it.", ephemeral=True)
            return

        category_key = self.values[0]
        category = TASK_CATEGORY_OPTIONS[category_key]
        embed = discord.Embed(
            title=f"Assign {category['label']}",
            description=f"Assigning to {self.assignee_mention}. Choose the specific task type.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.edit_message(
            embed=embed,
            view=AssignTaskTypeView(
                self.bot,
                self.db_path,
                category_key,
                self.assignee_id,
                self.assignee_name,
                self.assignee_mention,
                self.director_id
            )
        )


class AssignTaskTypeDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str, category_key: str, assignee_id: int, assignee_name: str, assignee_mention: str, director_id: int):
        self.bot = bot
        self.db_path = db_path
        self.assignee_id = assignee_id
        self.assignee_name = assignee_name
        self.assignee_mention = assignee_mention
        self.director_id = director_id
        category = TASK_CATEGORY_OPTIONS[category_key]
        super().__init__(
            placeholder=category["placeholder"],
            min_values=1,
            max_values=1,
            options=category["options"],
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.director_id or not has_director_role(interaction.user):
            await interaction.response.send_message("❌ Only the Director who opened this menu can use it.", ephemeral=True)
            return

        variant, sprite_type = self.values[0].split("_", 1)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT task_id, pokedex_identifier, min_level
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
                    description=f"No available tasks of this type can be assigned to {self.assignee_mention}.",
                    color=discord.Color.red()
                ),
                view=None
            )
            return

        embed = discord.Embed(
            title=f"Assign {variant} {sprite_type}",
            description=f"Assigning to {self.assignee_mention}. Choose one available task.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.edit_message(
            embed=embed,
            view=AssignAvailableTaskView(
                self.bot,
                self.db_path,
                variant,
                sprite_type,
                available_tasks,
                self.assignee_id,
                self.assignee_name,
                self.assignee_mention,
                self.director_id
            )
        )


class AssignAvailableTaskDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str, variant: str, sprite_type: str, available_tasks, assignee_id: int, assignee_name: str, assignee_mention: str, director_id: int):
        self.bot = bot
        self.db_path = db_path
        self.variant = variant
        self.sprite_type = sprite_type
        self.assignee_id = assignee_id
        self.assignee_name = assignee_name
        self.assignee_mention = assignee_mention
        self.director_id = director_id
        options = [
            discord.SelectOption(
                label=identifier[:100],
                value=str(task_id),
                description=f"{variant} {sprite_type} | {format_min_level(min_level)}"[:100],
            )
            for task_id, identifier, min_level in available_tasks
        ]
        super().__init__(placeholder="Select an available task to assign...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.director_id or not has_director_role(interaction.user):
            await interaction.response.send_message("❌ Only the Director who opened this menu can use it.", ephemeral=True)
            return

        task_id = int(self.values[0])
        now = datetime.now(timezone.utc)
        due_date = now + timedelta(days=7)

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT pokedex_identifier, status, forum_thread_id, min_level
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

                identifier, status, thread_id, min_level = task
                if status not in AVAILABLE_TASK_STATUSES:
                    await interaction.response.edit_message(
                        content=f"❌ **{self.variant} {self.sprite_type} — {identifier}** is no longer available.",
                        embed=None,
                        view=None
                    )
                    return

                has_level, user_level = check_user_min_level(cursor, self.assignee_id, min_level)
                if not has_level:
                    await interaction.response.edit_message(
                        content=(
                            f"❌ **{self.assignee_mention}** is **Level {user_level}**, but "
                            f"**{self.variant} {self.sprite_type} — {identifier}** requires **Level {min_level}**."
                        ),
                        embed=None,
                        view=None
                    )
                    return

                cursor.execute("""
                    UPDATE tasks
                    SET user_id = ?, status = 'Assigned', assigned_date = ?, due_date = ?
                    WHERE task_id = ?
                """, (self.assignee_id, now.isoformat(), due_date.isoformat(), task_id))
                conn.commit()

            await update_task_bundle_forum_status(
                self.bot,
                self.db_path,
                thread_id,
                f"{self.assignee_mention} was assigned this task by {interaction.user.mention}. Due: {due_date.strftime('%b %d, %Y')}."
            )

            embed = discord.Embed(
                title="Task Assigned",
                description=(
                    f"✅ Assigned **{self.variant} {self.sprite_type} — {identifier}** to {self.assignee_mention}.\n"
                    f"Due: **{due_date.strftime('%b %d, %Y')}**"
                ),
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)
        except discord.Forbidden as e:
            await interaction.response.edit_message(content=discord_access_error_message(e), embed=None, view=None)
        except Exception as e:
            await interaction.response.edit_message(content=f"Database error: {e}", embed=None, view=None)


class AssignThreadAvailableTaskDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str, available_tasks, assignee_id: int, assignee_name: str, assignee_mention: str, director_id: int):
        self.bot = bot
        self.db_path = db_path
        self.assignee_id = assignee_id
        self.assignee_name = assignee_name
        self.assignee_mention = assignee_mention
        self.director_id = director_id
        options = [
            discord.SelectOption(
                label=f"{variant} {sprite_type}"[:100],
                value=str(task_id),
                description=f"{identifier} | {format_min_level(min_level)}"[:100],
            )
            for task_id, variant, sprite_type, identifier, min_level in available_tasks
        ]
        super().__init__(placeholder="Select a task from this thread to assign...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.director_id or not has_director_role(interaction.user):
            await interaction.response.send_message("❌ Only the Director who opened this menu can use it.", ephemeral=True)
            return

        task_id = int(self.values[0])
        now = datetime.now(timezone.utc)
        due_date = now + timedelta(days=7)

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT variant, sprite_type, pokedex_identifier, status, forum_thread_id, min_level
                    FROM tasks
                    WHERE task_id = ?
                """, (task_id,))
                task = cursor.fetchone()

                if not task:
                    await interaction.response.edit_message(
                        content="❌ That task no longer exists.",
                        embed=None,
                        view=None
                    )
                    return

                variant, sprite_type, identifier, status, thread_id, min_level = task
                if status not in AVAILABLE_TASK_STATUSES:
                    await interaction.response.edit_message(
                        content=f"❌ **{variant} {sprite_type} — {identifier}** is no longer available.",
                        embed=None,
                        view=None
                    )
                    return

                if isinstance(interaction.channel, discord.Thread) and thread_id != interaction.channel.id:
                    await interaction.response.edit_message(
                        content="❌ That task is not part of this forum thread.",
                        embed=None,
                        view=None
                    )
                    return

                has_level, user_level = check_user_min_level(cursor, self.assignee_id, min_level)
                if not has_level:
                    await interaction.response.edit_message(
                        content=(
                            f"❌ **{self.assignee_mention}** is **Level {user_level}**, but "
                            f"**{variant} {sprite_type} — {identifier}** requires **Level {min_level}**."
                        ),
                        embed=None,
                        view=None
                    )
                    return

                cursor.execute("""
                    UPDATE tasks
                    SET user_id = ?, status = 'Assigned', assigned_date = ?, due_date = ?
                    WHERE task_id = ?
                """, (self.assignee_id, now.isoformat(), due_date.isoformat(), task_id))
                conn.commit()

            await update_task_bundle_forum_status(
                self.bot,
                self.db_path,
                thread_id,
                f"{self.assignee_mention} was assigned this task by {interaction.user.mention}. Due: {due_date.strftime('%b %d, %Y')}."
            )

            embed = discord.Embed(
                title="Task Assigned",
                description=(
                    f"✅ Assigned **{variant} {sprite_type} — {identifier}** to {self.assignee_mention}.\n"
                    f"Due: **{due_date.strftime('%b %d, %Y')}**"
                ),
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)
        except discord.Forbidden as e:
            await interaction.response.edit_message(content=discord_access_error_message(e), embed=None, view=None)
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

        if bundle_type == "Pokemon":
            self.dex_number_input = discord.ui.TextInput(
                label="Dex Number",
                placeholder="e.g., 138",
                style=discord.TextStyle.short,
                required=True,
            )
            self.pokemon_name_input = discord.ui.TextInput(
                label="Pokemon Name",
                placeholder="e.g., Sealuna",
                style=discord.TextStyle.short,
                required=True,
            )
            self.add_item(self.dex_number_input)
            self.add_item(self.pokemon_name_input)
        else:
            self.identifier_input = discord.ui.TextInput(
                label="Character Name",
                placeholder="e.g., protagonist, rival, shopkeeper...",
                style=discord.TextStyle.short,
                required=True,
            )
            self.add_item(self.identifier_input)

        self.min_level_input = discord.ui.TextInput(
            label="Minimum Level",
            placeholder="Optional. Leave blank for no level requirement.",
            style=discord.TextStyle.short,
            required=False,
        )
        self.add_item(self.min_level_input)
        self.reference_image_input = discord.ui.TextInput(
            label="Reference Image URL",
            placeholder="Optional. Paste an image link if there is one.",
            style=discord.TextStyle.short,
            required=False,
        )
        self.add_item(self.reference_image_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            min_level = parse_min_level(self.min_level_input.value)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        if self.category_key == "pokemon_sprite":
            dex_number = self.dex_number_input.value.strip()
            pokemon_name = self.pokemon_name_input.value.strip()
            if not dex_number.isdigit() or not pokemon_name:
                await interaction.response.send_message(
                    "❌ Pokemon tasks need a numeric Dex Number and a Pokemon Name.",
                    ephemeral=True
                )
                return
            identifier = f"{dex_number} - {pokemon_name}"
        else:
            identifier = self.identifier_input.value.strip()
            if not identifier:
                await interaction.response.send_message("❌ Task name cannot be empty.", ephemeral=True)
                return

        reference_image_url = normalize_reference_image_url(self.reference_image_input.value)

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
                    title=forum_title,
                    reference_image_url=reference_image_url
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
                        INSERT INTO tasks (
                            user_id, sprite_type, variant, pokedex_identifier, status,
                            assigned_date, due_date, forum_thread_id, min_level, reference_image_url
                        )
                        VALUES (NULL, ?, ?, ?, 'Available', NULL, NULL, ?, ?, ?)
                    """, (sprite_type, variant, identifier, thread_id, min_level, reference_image_url))
                    added_count += 1
                conn.commit()

            await update_task_bundle_forum_status(self.bot, self.db_path, thread_id)
            await interaction.followup.send(
                f"✅ Added **{added_count}** available {self.bundle_type} tasks for **{identifier}**."
                f"{' Minimum level: **' + str(min_level) + '**.' if min_level else ''}"
                f"{' Reference image added.' if reference_image_url else ''}"
                f"{' Skipped ' + str(skipped_count) + ' existing tasks.' if skipped_count else ''}",
                ephemeral=True
            )
        except discord.Forbidden as e:
            await interaction.followup.send(discord_access_error_message(e), ephemeral=True)
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
        self.min_level_input = discord.ui.TextInput(
            label="Minimum Level",
            placeholder="Optional. Leave blank for no level requirement.",
            style=discord.TextStyle.short,
            required=False,
        )
        self.add_item(self.min_level_input)
        self.reference_image_input = discord.ui.TextInput(
            label="Reference Image URL",
            placeholder="Optional. Paste an image link if there is one.",
            style=discord.TextStyle.short,
            required=False,
        )
        self.add_item(self.reference_image_input)

    async def on_submit(self, interaction: discord.Interaction):
        identifier = self.identifier_input.value.strip()
        if not identifier:
            await interaction.response.send_message("❌ Task name cannot be empty.", ephemeral=True)
            return

        try:
            min_level = parse_min_level(self.min_level_input.value)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        reference_image_url = normalize_reference_image_url(self.reference_image_input.value)

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
                    INSERT INTO tasks (
                        user_id, sprite_type, variant, pokedex_identifier, status,
                        assigned_date, due_date, forum_thread_id, min_level, reference_image_url
                    )
                    VALUES (NULL, ?, ?, ?, 'Available', NULL, NULL, NULL, ?, ?)
                """, (self.sprite_type, self.variant, identifier, min_level, reference_image_url))
                task_id = cursor.lastrowid
                conn.commit()

            thread = await create_task_forum_post(
                self.bot,
                self.variant,
                self.sprite_type,
                identifier,
                reference_image_url=reference_image_url
            )
            if thread:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE tasks SET forum_thread_id = ? WHERE task_id = ?", (thread.id, task_id))
                    conn.commit()
                await update_task_forum_summary(self.bot, self.db_path, thread.id)

            await interaction.followup.send(
                f"✅ Added **{self.variant} {self.sprite_type} — {identifier}** to the available task board."
                f"{' Minimum level: **' + str(min_level) + '**.' if min_level else ''}"
                f"{' Reference image added.' if reference_image_url else ''}",
                ephemeral=True
            )
        except discord.Forbidden as e:
            await interaction.followup.send(discord_access_error_message(e), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Database error: {e}", ephemeral=True)


class RemoveAvailablePokemonDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str, available_pokemon):
        self.bot = bot
        self.db_path = db_path
        options = [
            discord.SelectOption(
                label=identifier[:100],
                value=str(task_id),
                description=f"{available_count} available task{'s' if available_count != 1 else ''}"[:100],
            )
            for task_id, identifier, available_count in available_pokemon
        ]
        super().__init__(placeholder="Select an available Pokemon to remove...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        task_id = int(self.values[0])

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT pokedex_identifier
                    FROM tasks
                    WHERE task_id = ?
                      AND variant IN ('Base', 'Shiny', 'Anomaly')
                      AND status IN ('Available', 'Unassigned')
                """, (task_id,))
                pokemon = cursor.fetchone()

                if not pokemon:
                    await interaction.response.edit_message(
                        content="❌ That Pokemon is no longer available to remove.",
                        embed=None,
                        view=None
                    )
                    return

                identifier = pokemon[0]
                cursor.execute("""
                    SELECT DISTINCT forum_thread_id
                    FROM tasks
                    WHERE pokedex_identifier = ?
                      AND variant IN ('Base', 'Shiny', 'Anomaly')
                      AND status IN ('Available', 'Unassigned')
                      AND forum_thread_id IS NOT NULL
                """, (identifier,))
                thread_ids = [row[0] for row in cursor.fetchall()]

                cursor.execute("""
                    UPDATE tasks
                    SET status = 'Removed'
                    WHERE pokedex_identifier = ?
                      AND variant IN ('Base', 'Shiny', 'Anomaly')
                      AND status IN ('Available', 'Unassigned')
                """, (identifier,))
                removed_count = cursor.rowcount
                conn.commit()

            for thread_id in thread_ids:
                await update_task_bundle_forum_status(
                    self.bot,
                    self.db_path,
                    thread_id,
                    "This Pokemon was removed from the available task board."
                )

            embed = discord.Embed(
                title="Pokemon Removed",
                description=f"✅ Removed **{removed_count}** available task{'s' if removed_count != 1 else ''} for **{identifier}**.",
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)
        except discord.Forbidden as e:
            await interaction.response.edit_message(content=discord_access_error_message(e), embed=None, view=None)
        except Exception as e:
            await interaction.response.edit_message(content=f"Database error: {e}", embed=None, view=None)


class RemoveAvailableBundleDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str, available_bundles, variants, group_label: str):
        self.bot = bot
        self.db_path = db_path
        self.variants = tuple(variants)
        self.group_label = group_label
        options = [
            discord.SelectOption(
                label=identifier[:100],
                value=str(task_id),
                description=f"{available_count} available task{'s' if available_count != 1 else ''}"[:100],
            )
            for task_id, identifier, available_count in available_bundles
        ]
        super().__init__(
            placeholder=f"Select an available {group_label.lower()} to remove...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        task_id = int(self.values[0])
        placeholders = ", ".join("?" for _ in self.variants)

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT pokedex_identifier
                    FROM tasks
                    WHERE task_id = ?
                      AND variant IN ({placeholders})
                      AND status IN ('Available', 'Unassigned')
                """, (task_id, *self.variants))
                bundle = cursor.fetchone()

                if not bundle:
                    await interaction.response.edit_message(
                        content=f"❌ That {self.group_label.lower()} is no longer available to remove.",
                        embed=None,
                        view=None
                    )
                    return

                identifier = bundle[0]
                cursor.execute(f"""
                    SELECT DISTINCT forum_thread_id
                    FROM tasks
                    WHERE pokedex_identifier = ?
                      AND variant IN ({placeholders})
                      AND status IN ('Available', 'Unassigned')
                      AND forum_thread_id IS NOT NULL
                """, (identifier, *self.variants))
                thread_ids = [row[0] for row in cursor.fetchall()]

                cursor.execute(f"""
                    UPDATE tasks
                    SET status = 'Removed'
                    WHERE pokedex_identifier = ?
                      AND variant IN ({placeholders})
                      AND status IN ('Available', 'Unassigned')
                """, (identifier, *self.variants))
                removed_count = cursor.rowcount
                conn.commit()

            for thread_id in thread_ids:
                await update_task_bundle_forum_status(
                    self.bot,
                    self.db_path,
                    thread_id,
                    f"This {self.group_label.lower()} was removed from the available task board."
                )

            embed = discord.Embed(
                title=f"{self.group_label} Removed",
                description=f"✅ Removed **{removed_count}** available task{'s' if removed_count != 1 else ''} for **{identifier}**.",
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)
        except discord.Forbidden as e:
            await interaction.response.edit_message(content=discord_access_error_message(e), embed=None, view=None)
        except Exception as e:
            await interaction.response.edit_message(content=f"Database error: {e}", embed=None, view=None)


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
        except discord.Forbidden as e:
            await interaction.response.edit_message(content=discord_access_error_message(e), embed=None, view=None)
        except Exception as e:
            await interaction.response.edit_message(content=f"Database error: {e}", embed=None, view=None)


class CancelTaskDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str, assigned_tasks, is_director: bool):
        self.bot = bot
        self.db_path = db_path
        options = []

        for task_id, variant, sprite_type, identifier, _assigned_user, artist_name, due_date_str in assigned_tasks:
            due_text = "No due date"
            if due_date_str:
                due_text = datetime.fromisoformat(due_date_str).strftime('%b %d, %Y')

            description = f"Assigned to {artist_name} | Due {due_text}" if is_director else f"Due {due_text}"
            options.append(discord.SelectOption(
                label=f"{variant} {sprite_type} - {identifier}"[:100],
                value=str(task_id),
                description=description[:100],
            ))

        super().__init__(placeholder="Select a task to cancel...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        task_id = int(self.values[0])

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT variant, sprite_type, pokedex_identifier, user_id, forum_thread_id
                    FROM tasks
                    WHERE task_id = ? AND status = 'Assigned'
                """, (task_id,))
                task = cursor.fetchone()

                if not task:
                    await interaction.response.edit_message(
                        content="❌ That task is no longer an active assignment.",
                        embed=None,
                        view=None
                    )
                    return

                variant, sprite_type, identifier, assigned_user, thread_id = task
                is_assignee = interaction.user.id == assigned_user
                is_director = has_director_role(interaction.user)

                if not (is_assignee or is_director):
                    await interaction.response.edit_message(
                        content="❌ You do not have permission to cancel that task.",
                        embed=None,
                        view=None
                    )
                    return

                cursor.execute("""
                    UPDATE tasks
                    SET status = 'Available', user_id = NULL, assigned_date = NULL, due_date = NULL
                    WHERE task_id = ?
                """, (task_id,))
                conn.commit()

            await update_task_bundle_forum_status(
                self.bot,
                self.db_path,
                thread_id,
                f"This task was returned to Missing by {interaction.user.mention}."
            )

            if is_director and not is_assignee:
                description = f"✅ **(Director Override)** **{variant} {sprite_type} — {identifier}** has been removed from <@{assigned_user}>."
            else:
                description = f"✅ You have successfully cancelled **{variant} {sprite_type} — {identifier}**."

            embed = discord.Embed(
                title="Task Cancelled",
                description=description,
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)
        except discord.Forbidden as e:
            await interaction.response.edit_message(content=discord_access_error_message(e), embed=None, view=None)
        except Exception as e:
            await interaction.response.edit_message(content=f"Database error: {e}", embed=None, view=None)


class Tasks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "data/corelet.db"
        self.ensure_schema()
        # Start the background loop when the cog is loaded
        self.check_deadlines.start() 

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

    async def require_director(self, interaction: discord.Interaction) -> bool:
        if has_director_role(interaction.user):
            return True

        await interaction.response.send_message(
            "❌ You need the Directors role to use this menu.",
            ephemeral=True
        )
        return False

    async def dm_user(self, user_id: int, message: str):
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            if user:
                await user.send(message)
        except discord.HTTPException:
            print(f"Could not DM user {user_id}.")

    def get_thread_available_tasks(self, channel):
        if not isinstance(channel, discord.Thread):
            return []

        return self.fetch_query("""
            SELECT task_id, variant, sprite_type, pokedex_identifier, min_level
            FROM tasks
            WHERE forum_thread_id = ?
              AND status IN ('Available', 'Unassigned')
            ORDER BY variant, sprite_type, pokedex_identifier COLLATE NOCASE
            LIMIT 25
        """, (channel.id,))

    def is_task_request_channel(self, channel) -> bool:
        request_channel_ids = get_task_request_channel_ids()
        if not request_channel_ids:
            return True

        return getattr(channel, "id", None) in request_channel_ids

    async def send_task_claim_menu(self, interaction: discord.Interaction):
        available_tasks = self.get_thread_available_tasks(interaction.channel)
        if available_tasks:
            thread_title = getattr(interaction.channel, "name", "This Task")
            embed = discord.Embed(
                title=thread_title,
                description="Choose one of the available tasks from this thread.",
                color=discord.Color.dark_grey()
            )
            await interaction.response.send_message(
                embed=embed,
                view=ThreadAvailableTaskView(self.bot, self.db_path, available_tasks)
            )
            return

        if isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "❌ There are no available tasks in this thread.",
                ephemeral=True
            )
            return

        if not self.is_task_request_channel(interaction.channel):
            await interaction.response.send_message(
                "❌ Use this in the task request channel, or inside a task forum thread.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Void Dev Task Board",
            description="Select a category below to claim a new task.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.send_message(
            embed=embed,
            view=TaskBoardView(self.bot, self.db_path)
        )

    async def send_active_tasks(self, interaction: discord.Interaction):
        try:
            active_tasks = fetch_active_task_rows(self.db_path)

            if not active_tasks:
                await interaction.response.send_message("❌ There are currently no active tasks.", ephemeral=True)
                return

            view = ActiveTasksView(
                self.db_path,
                interaction.user.id,
                has_director_role(interaction.user),
                active_tasks,
                "All Active Tasks",
            )
            await interaction.response.send_message(embed=view.build_embed(), view=view)

        except discord.Forbidden as e:
            await interaction.response.send_message(discord_access_error_message(e), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)


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
            await update_task_forum_summary(self.bot, self.db_path, thread_id)

            await interaction.response.send_message(f"✅ Assigned **{variant} {sprite_type} {identifier}** to {assignee.mention}. Due by: {due_date.strftime('%Y-%m-%d')}.")
        except discord.Forbidden as e:
            await interaction.response.send_message(discord_access_error_message(e), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)

    @app_commands.command(name="assigntaskmenu", description="Open a menu for assigning an available task")
    async def assigntaskmenu(self, interaction: discord.Interaction):
        if not await self.require_director(interaction):
            return

        embed = discord.Embed(
            title="Assign Task",
            description="Choose the member who should receive an available task.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.send_message(
            embed=embed,
            view=AssignTaskUserView(self.bot, self.db_path, interaction.user.id)
        )

    async def addavailabletask(self, interaction: discord.Interaction, identifier: str, sprite_type: str, variant: str):
        if variant in ("Base", "Shiny", "Anomaly"):
            normalized_identifier = normalize_pokemon_identifier(identifier)
            if normalized_identifier is None:
                await interaction.response.send_message(
                    "❌ Pokemon tasks must use the format `Dex Number - Pokemon Name`, like `138 - Sealuna`.",
                    ephemeral=True
                )
                return
            identifier = normalized_identifier

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
                await update_task_forum_summary(self.bot, self.db_path, thread.id)
            await interaction.response.send_message(
                f"✅ Added **{variant} {sprite_type} — {identifier}** to the available task board.",
                ephemeral=True
            )
        except discord.Forbidden as e:
            await interaction.response.send_message(discord_access_error_message(e), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)

    @app_commands.command(name="addtaskmenu", description="Open a menu for adding available tasks")
    async def addtaskmenu(self, interaction: discord.Interaction):
        if not await self.require_director(interaction):
            return

        embed = discord.Embed(
            title="Add Available Task",
            description="Choose the category and type, then enter the task name.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.send_message(
            embed=embed,
            view=AddTaskCategoryView(self.bot, self.db_path)
        )

    @app_commands.command(name="edittask", description="Edit a task name or reference image")
    @app_commands.describe(
        task_id="The task ID from /tasks or the active task sheet",
        name="New task name. For bundled forum tasks, this renames the whole bundle.",
        reference_image="Optional image attachment to use as the reference",
        reference_image_url="Optional image URL. Use clear/remove/none to remove the reference.",
    )
    async def edittask(
        self,
        interaction: discord.Interaction,
        task_id: int,
        name: str | None = None,
        reference_image: discord.Attachment | None = None,
        reference_image_url: str | None = None,
    ):
        if not await self.require_director(interaction):
            return

        new_name = name.strip() if name else None
        if name is not None and not new_name:
            await interaction.response.send_message("❌ Task name cannot be empty.", ephemeral=True)
            return

        has_reference_update = reference_image is not None or reference_image_url is not None
        if new_name is None and not has_reference_update:
            await interaction.response.send_message(
                "❌ Provide a new name, a reference image, or a reference image URL.",
                ephemeral=True
            )
            return

        if reference_image is not None:
            content_type = reference_image.content_type or ""
            filename = reference_image.filename.casefold()
            has_image_extension = filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
            if content_type and not content_type.startswith("image/") and not has_image_extension:
                await interaction.response.send_message("❌ The reference attachment must be an image.", ephemeral=True)
                return
            new_reference_image_url = reference_image.url
        elif reference_image_url is not None:
            clean_reference = reference_image_url.strip()
            if clean_reference.casefold() in {"clear", "remove", "none"}:
                new_reference_image_url = None
            else:
                new_reference_image_url = normalize_reference_image_url(clean_reference)
        else:
            new_reference_image_url = None

        await interaction.response.defer(ephemeral=True)

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT variant, sprite_type, pokedex_identifier, forum_thread_id
                    FROM tasks
                    WHERE task_id = ?
                """, (task_id,))
                task = cursor.fetchone()

                if not task:
                    await interaction.followup.send("❌ Could not find that task.", ephemeral=True)
                    return

                variant, sprite_type, old_name, thread_id = task
                set_clauses = []
                params = []
                if new_name is not None:
                    set_clauses.append("pokedex_identifier = ?")
                    params.append(new_name)
                if has_reference_update:
                    set_clauses.append("reference_image_url = ?")
                    params.append(new_reference_image_url)

                if thread_id:
                    params.extend([thread_id, old_name])
                    cursor.execute(f"""
                        UPDATE tasks
                        SET {", ".join(set_clauses)}
                        WHERE forum_thread_id = ? AND pokedex_identifier = ?
                    """, tuple(params))
                else:
                    params.append(task_id)
                    cursor.execute(f"""
                        UPDATE tasks
                        SET {", ".join(set_clauses)}
                        WHERE task_id = ?
                    """, tuple(params))
                updated_count = cursor.rowcount
                conn.commit()

            if new_name is not None and thread_id:
                thread = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
                if isinstance(thread, discord.Thread):
                    await thread.edit(name=new_name[:100])

            await update_task_forum_summary(self.bot, self.db_path, thread_id)

            changes = []
            if new_name is not None:
                changes.append(f"name to **{new_name}**")
            if has_reference_update:
                changes.append(
                    "reference image"
                    if new_reference_image_url
                    else "removed reference image"
                )
            await interaction.followup.send(
                f"✅ Updated **{updated_count}** task row{'s' if updated_count != 1 else ''} for "
                f"**{variant} {sprite_type} — {old_name}**: {', '.join(changes)}.",
                ephemeral=True
            )
        except discord.Forbidden as e:
            await interaction.followup.send(discord_access_error_message(e), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Database error: {e}", ephemeral=True)

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
        except discord.Forbidden as e:
            await interaction.response.send_message(discord_access_error_message(e), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)

    async def send_remove_available_pokemon_menu(self, interaction: discord.Interaction):
        if not await self.require_director(interaction):
            return

        available_pokemon = self.fetch_query("""
            SELECT MIN(task_id), pokedex_identifier, COUNT(*)
            FROM tasks
            WHERE variant IN ('Base', 'Shiny', 'Anomaly')
              AND status IN ('Available', 'Unassigned')
            GROUP BY pokedex_identifier
            ORDER BY pokedex_identifier COLLATE NOCASE
            LIMIT 25
        """)

        if not available_pokemon:
            await interaction.response.send_message("❌ There are no available Pokemon to remove.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Remove Available Pokemon",
            description="Choose a Pokemon to remove its unclaimed sprite tasks from the available board.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.send_message(
            embed=embed,
            view=RemoveAvailablePokemonView(self.bot, self.db_path, available_pokemon)
        )

    async def send_remove_available_bundle_menu(self, interaction: discord.Interaction, group_label: str, variants):
        if not await self.require_director(interaction):
            return

        placeholders = ", ".join("?" for _ in variants)
        available_bundles = self.fetch_query(f"""
            SELECT MIN(task_id), pokedex_identifier, COUNT(*)
            FROM tasks
            WHERE variant IN ({placeholders})
              AND status IN ('Available', 'Unassigned')
            GROUP BY pokedex_identifier
            ORDER BY pokedex_identifier COLLATE NOCASE
            LIMIT 25
        """, tuple(variants))

        if not available_bundles:
            await interaction.response.send_message(f"❌ There are no available {group_label.lower()}s to remove.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Remove Available {group_label}",
            description=f"Choose a {group_label.lower()} to remove its unclaimed tasks from the available board.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.send_message(
            embed=embed,
            view=RemoveAvailableBundleView(self.bot, self.db_path, available_bundles, variants, group_label)
        )

    @app_commands.command(name="removepokemonmenu", description="Choose an available Pokemon to remove from the task board")
    async def removepokemonmenu(self, interaction: discord.Interaction):
        await self.send_remove_available_pokemon_menu(interaction)

    @app_commands.command(name="removeavailablepokemonmenu", description="Choose an available Pokemon to remove from the task board")
    async def removeavailablepokemonmenu(self, interaction: discord.Interaction):
        await self.send_remove_available_pokemon_menu(interaction)

    @app_commands.command(name="removecharactermenu", description="Choose an available character to remove from the task board")
    async def removecharactermenu(self, interaction: discord.Interaction):
        await self.send_remove_available_bundle_menu(interaction, "Character", ("Character",))

    @app_commands.command(name="removeavailablecharactermenu", description="Choose an available character to remove from the task board")
    async def removeavailablecharactermenu(self, interaction: discord.Interaction):
        await self.send_remove_available_bundle_menu(interaction, "Character", ("Character",))

    @app_commands.command(name="removesoundmenu", description="Choose an available sound task group to remove from the task board")
    async def removesoundmenu(self, interaction: discord.Interaction):
        await self.send_remove_available_bundle_menu(interaction, "Sound", ("Audio",))

    @app_commands.command(name="removeavailablesoundmenu", description="Choose an available sound task group to remove from the task board")
    async def removeavailablesoundmenu(self, interaction: discord.Interaction):
        await self.send_remove_available_bundle_menu(interaction, "Sound", ("Audio",))

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

    async def send_cancel_task_menu(self, interaction: discord.Interaction):
        is_director = has_director_role(interaction.user)

        if is_director:
            assigned_tasks = self.fetch_query("""
                SELECT t.task_id, t.variant, t.sprite_type, t.pokedex_identifier, t.user_id,
                       COALESCE(u.discord_name, 'Unknown User'), t.due_date
                FROM tasks t
                LEFT JOIN users u ON t.user_id = u.user_id
                WHERE t.status = 'Assigned'
                ORDER BY t.due_date ASC, t.variant, t.sprite_type, t.pokedex_identifier COLLATE NOCASE
                LIMIT 25
            """)
        else:
            assigned_tasks = self.fetch_query("""
                SELECT t.task_id, t.variant, t.sprite_type, t.pokedex_identifier, t.user_id,
                       COALESCE(u.discord_name, 'Unknown User'), t.due_date
                FROM tasks t
                LEFT JOIN users u ON t.user_id = u.user_id
                WHERE t.user_id = ? AND t.status = 'Assigned'
                ORDER BY t.due_date ASC, t.variant, t.sprite_type, t.pokedex_identifier COLLATE NOCASE
                LIMIT 25
            """, (interaction.user.id,))

        if not assigned_tasks:
            message = "❌ There are no assigned tasks to cancel." if is_director else "❌ You do not have any assigned tasks to cancel."
            await interaction.response.send_message(message, ephemeral=True)
            return

        title = "Cancel Any Assignment" if is_director else "Cancel Your Assignment"
        description = "Choose the assigned task to return to the available board."
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.dark_grey()
        )
        await interaction.response.send_message(
            embed=embed,
            view=CancelTaskView(self.bot, self.db_path, assigned_tasks, is_director)
        )

    async def canceltask(self, interaction: discord.Interaction):
        await self.send_cancel_task_menu(interaction)

    @app_commands.command(name="closetask", description="Choose an active assignment to cancel")
    async def closetask(self, interaction: discord.Interaction):
        await self.send_cancel_task_menu(interaction)

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

    @app_commands.command(name="tasks", description="View all currently active sprite tasks")
    async def viewtasks(self, interaction: discord.Interaction):
        await self.send_active_tasks(interaction)

    async def opentasks(self, interaction: discord.Interaction):
        await self.send_active_tasks(interaction)

    @app_commands.command(name="taskmenu", description="Open the task claim menu for this channel")
    async def taskmenu(self, interaction: discord.Interaction):
        await self.send_task_claim_menu(interaction)

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
        except discord.Forbidden as e:
            await interaction.response.send_message(discord_access_error_message(e), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)

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
        except discord.Forbidden as e:
            await interaction.response.send_message(discord_access_error_message(e), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)
            
# Error handler for role checks in this Cog
    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingRole) or isinstance(error, app_commands.MissingAnyRole):
            await interaction.response.send_message("❌ You do not have the required role to use this command.", ephemeral=True)
        else:
            await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)
    

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

        except discord.Forbidden as e:
            await interaction.response.send_message(discord_access_error_message(e), ephemeral=True)
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
            view=RequestFeedbackView(self.bot, self.db_path, assigned_tasks)
        )

    @app_commands.command(name="starttasks", description="Generate the interactive task assignment board")
    async def starttasks(self, interaction: discord.Interaction):
        if not await self.require_director(interaction):
            return

        available_tasks = self.get_thread_available_tasks(interaction.channel)
        if available_tasks:
            embed = discord.Embed(
                title=getattr(interaction.channel, "name", "This Task"),
                description="Choose one of the available tasks from this thread.",
                color=discord.Color.dark_grey()
            )
            await interaction.channel.send(
                embed=embed,
                view=ThreadAvailableTaskView(self.bot, self.db_path, available_tasks)
            )
            await interaction.response.send_message("Menu spawned.")
            return

        if not self.is_task_request_channel(interaction.channel):
            await interaction.response.send_message(
                "❌ Spawn the broad task board in the task request channel, or use this inside a task forum thread.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Void Dev Task Board",
            description="Select a category below to claim a new task.",
            color=discord.Color.dark_grey()
        )
        view = TaskBoardView(self.bot, self.db_path)
        
        # Send the menu to the channel
        await interaction.channel.send(embed=embed, view=view)
        # Quietly acknowledge the slash command so it doesn't say "Interaction Failed"
        await interaction.response.send_message("Menu spawned.")

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


class ThreadAvailableTaskView(discord.ui.View):
    def __init__(self, bot, db_path: str, available_tasks):
        super().__init__(timeout=300)
        self.add_item(ThreadAvailableTaskDropdown(bot, db_path, available_tasks))


class AssignTaskUserView(discord.ui.View):
    def __init__(self, bot, db_path: str, director_id: int):
        super().__init__(timeout=300)
        self.add_item(AssignTaskUserSelect(bot, db_path, director_id))


class AssignTaskCategoryView(discord.ui.View):
    def __init__(self, bot, db_path: str, assignee_id: int, assignee_name: str, assignee_mention: str, director_id: int, options):
        super().__init__(timeout=300)
        self.add_item(AssignTaskCategoryDropdown(bot, db_path, assignee_id, assignee_name, assignee_mention, director_id, options))


class AssignTaskTypeView(discord.ui.View):
    def __init__(self, bot, db_path: str, category_key: str, assignee_id: int, assignee_name: str, assignee_mention: str, director_id: int):
        super().__init__(timeout=300)
        self.add_item(AssignTaskTypeDropdown(bot, db_path, category_key, assignee_id, assignee_name, assignee_mention, director_id))


class AssignAvailableTaskView(discord.ui.View):
    def __init__(self, bot, db_path: str, variant: str, sprite_type: str, available_tasks, assignee_id: int, assignee_name: str, assignee_mention: str, director_id: int):
        super().__init__(timeout=300)
        self.add_item(AssignAvailableTaskDropdown(bot, db_path, variant, sprite_type, available_tasks, assignee_id, assignee_name, assignee_mention, director_id))


class AssignThreadAvailableTaskView(discord.ui.View):
    def __init__(self, bot, db_path: str, available_tasks, assignee_id: int, assignee_name: str, assignee_mention: str, director_id: int):
        super().__init__(timeout=300)
        self.add_item(AssignThreadAvailableTaskDropdown(bot, db_path, available_tasks, assignee_id, assignee_name, assignee_mention, director_id))


class AddTaskCategoryView(discord.ui.View):
    def __init__(self, bot, db_path: str):
        super().__init__(timeout=300)
        self.add_item(AddTaskCategoryDropdown(bot, db_path))


class AddTaskTypeView(discord.ui.View):
    def __init__(self, bot, db_path: str, category_key: str):
        super().__init__(timeout=300)
        self.add_item(AddTaskTypeDropdown(bot, db_path, category_key))


class RemoveAvailablePokemonView(discord.ui.View):
    def __init__(self, bot, db_path: str, available_pokemon):
        super().__init__(timeout=300)
        self.add_item(RemoveAvailablePokemonDropdown(bot, db_path, available_pokemon))


class RemoveAvailableBundleView(discord.ui.View):
    def __init__(self, bot, db_path: str, available_bundles, variants, group_label: str):
        super().__init__(timeout=300)
        self.add_item(RemoveAvailableBundleDropdown(bot, db_path, available_bundles, variants, group_label))


class RequestFeedbackView(discord.ui.View):
    def __init__(self, bot, db_path: str, assigned_tasks):
        super().__init__(timeout=300)
        self.add_item(RequestFeedbackDropdown(bot, db_path, assigned_tasks))


class CancelTaskView(discord.ui.View):
    def __init__(self, bot, db_path: str, assigned_tasks, is_director: bool):
        super().__init__(timeout=300)
        self.add_item(CancelTaskDropdown(bot, db_path, assigned_tasks, is_director))


class ActiveTasksMemberSelect(discord.ui.UserSelect):
    def __init__(self, parent_view):
        self.parent_view = parent_view
        super().__init__(placeholder="Select a member to view...", min_values=1, max_values=1, row=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.requester_id or not self.parent_view.is_director:
            await interaction.response.send_message("❌ Only the Director who opened this view can use this selector.", ephemeral=True)
            return

        member = self.values[0]
        rows = fetch_active_task_rows(self.parent_view.db_path, member.id)
        self.parent_view.set_rows(rows, f"{getattr(member, 'display_name', member.name)}'s Active Tasks")
        await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)


class ActiveTasksView(discord.ui.View):
    def __init__(self, db_path: str, requester_id: int, is_director: bool, active_tasks, title: str, page_size: int = 10):
        super().__init__(timeout=300)
        self.db_path = db_path
        self.requester_id = requester_id
        self.is_director = is_director
        self.active_tasks = active_tasks
        self.title = title
        self.page_size = page_size
        self.page = 0
        if is_director:
            self.add_item(ActiveTasksMemberSelect(self))
        self.update_buttons()

    @property
    def page_count(self) -> int:
        return max(1, (len(self.active_tasks) + self.page_size - 1) // self.page_size)

    def update_buttons(self):
        self.previous_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.page_count - 1
        self.view_member.disabled = not self.is_director

    def set_rows(self, active_tasks, title: str):
        self.active_tasks = active_tasks
        self.title = title
        self.page = 0
        self.update_buttons()

    def build_embed(self):
        embed = discord.Embed(
            title=self.title,
            description="Use the controls below to move through active tasks.",
            color=discord.Color.blue()
        )

        if not self.active_tasks:
            embed.description = "No active tasks found for this view."
            embed.set_footer(text="Page 1 of 1 • 0 active tasks")
            return embed

        start = self.page * self.page_size
        end = start + self.page_size
        for task in self.active_tasks[start:end]:
            user_id, sprite_type, variant, identifier, due_date_str, status, min_level, reference_image_url = task
            formatted_date = "Not claimed"
            if due_date_str:
                due_date = datetime.fromisoformat(due_date_str)
                formatted_date = due_date.strftime('%b %d, %Y')

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
                value=(
                    f"**Assigned to:** {assigned_text}\n"
                    f"**Status:** {status}\n"
                    f"**Due:** {formatted_date}\n"
                    f"**Minimum Level:** {format_min_level(min_level)}\n"
                    f"**Reference:** {format_reference_image(reference_image_url)}"
                ),
                inline=False
            )

        embed.set_footer(text=f"Page {self.page + 1} of {self.page_count} • {len(self.active_tasks)} active tasks")
        return embed

    @discord.ui.button(label="All", style=discord.ButtonStyle.secondary)
    async def view_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Only the person who opened this view can use these controls.", ephemeral=True)
            return

        self.set_rows(fetch_active_task_rows(self.db_path), "All Active Tasks")
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Mine", style=discord.ButtonStyle.primary)
    async def view_mine(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Only the person who opened this view can use these controls.", ephemeral=True)
            return

        self.set_rows(fetch_active_task_rows(self.db_path, interaction.user.id), "Your Active Tasks")
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Member", style=discord.ButtonStyle.secondary)
    async def view_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id or not self.is_director:
            await interaction.response.send_message("❌ Only Directors can view tasks by member.", ephemeral=True)
            return

        await interaction.response.send_message("Use the member selector on the task view to choose a member.", ephemeral=True)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Only the person who opened this view can use these controls.", ephemeral=True)
            return

        self.page = max(0, self.page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Only the person who opened this view can use these controls.", ephemeral=True)
            return

        self.page = min(self.page_count - 1, self.page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

async def setup(bot):
    await bot.add_cog(Tasks(bot))
