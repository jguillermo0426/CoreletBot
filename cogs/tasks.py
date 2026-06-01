import discord
from discord import app_commands
from discord.ext import commands, tasks
import sqlite3
import re
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from task_forum import (
    create_task_forum_post,
    ensure_task_link_columns,
    update_task_forum_status,
    update_task_forum_summary,
)

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
TASK_BOARD_CATEGORY_OPTIONS = {
    "pokemon_sprite": {
        "label": "Spriting",
        "description": "Pokemon sprite tasks grouped by Pokemon",
        "emoji": "🟩",
        "title": "Pokemon Spriting Tasks",
    },
    "character_sprite": {
        "label": "Characters",
        "description": "Character tasks grouped by character",
        "emoji": "🎨",
        "title": "Character Tasks",
    },
    "music": {
        "label": "Music",
        "description": "Music tasks grouped by track or cue",
        "emoji": "🎵",
        "title": "Music Tasks",
    },
}

TASK_BOARD_GROUPS = {
    "pokemon_sprite": {
        "variants": ("Base", "Shiny", "Anomaly"),
        "sprite_types": None,
        "group_label": "Pokemon",
        "empty_message": "There are no available Pokemon spriting tasks right now.",
    },
    "character_sprite": {
        "variants": ("Character",),
        "sprite_types": None,
        "group_label": "character",
        "empty_message": "There are no available character tasks right now.",
    },
    "music": {
        "variants": ("Audio",),
        "sprite_types": ("Music",),
        "group_label": "music task",
        "empty_message": "There are no available music tasks right now.",
    },
}

POKEMON_BOARD_TASK_SLOTS = (
    ("Base", "Icon"),
    ("Base", "Front"),
    ("Base", "Front 2"),
    ("Base", "Back"),
    ("Shiny", "Front"),
    ("Shiny", "Front 2"),
    ("Shiny", "Back"),
    ("Anomaly", "Front"),
    ("Anomaly", "Front 2"),
    ("Anomaly", "Back"),
)


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


def fetch_active_task_rows(db_path: str, user_id: Optional[int] = None):
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


def normalize_task_search_args(scope: Optional[str], category: Optional[str]):
    values = []
    for value in (scope, category):
        if value:
            values.extend(part.strip().casefold() for part in value.split() if part.strip())

    can_do_only = "can" in values
    category_key = None
    category_aliases = {
        "pokemon": "pokemon",
        "spriting": "pokemon",
        "sprite": "pokemon",
        "sprites": "pokemon",
        "character": "character",
        "characters": "character",
        "char": "character",
        "music": "music",
        "song": "music",
        "songs": "music",
        "other": "other",
        "others": "other",
        "sound": "other",
        "sounds": "other",
        "sfx": "other",
        "cry": "other",
        "cries": "other",
    }

    unknown_terms = []
    for value in values:
        if value == "can":
            continue
        if value in category_aliases:
            category_key = category_aliases[value]
        else:
            unknown_terms.append(value)

    return can_do_only, category_key, unknown_terms


def fetch_available_task_search_rows(db_path: str, category_key: Optional[str], user_level: Optional[int]):
    clauses = ["status IN ('Available', 'Unassigned')"]
    params = []

    if user_level is not None:
        clauses.append("(min_level IS NULL OR min_level <= ?)")
        params.append(user_level)

    if category_key == "pokemon":
        clauses.append("variant IN ('Base', 'Shiny', 'Anomaly')")
    elif category_key == "character":
        clauses.append("variant = 'Character'")
    elif category_key == "music":
        clauses.append("variant = 'Audio' AND sprite_type = 'Music'")
    elif category_key == "other":
        clauses.append("""
            NOT (
                variant IN ('Base', 'Shiny', 'Anomaly')
                OR variant = 'Character'
                OR (variant = 'Audio' AND sprite_type = 'Music')
            )
        """)

    where_clause = " AND ".join(clauses)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT task_id, variant, sprite_type, pokedex_identifier, min_level, reference_image_url, forum_thread_id
            FROM tasks
            WHERE {where_clause}
            ORDER BY
                CASE
                    WHEN variant IN ('Base', 'Shiny', 'Anomaly') THEN 1
                    WHEN variant = 'Character' THEN 2
                    WHEN variant = 'Audio' AND sprite_type = 'Music' THEN 3
                    ELSE 4
                END,
                pokedex_identifier COLLATE NOCASE,
                variant,
                sprite_type
        """, tuple(params))
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


def normalize_reference_image_url(value: Optional[str]):
    if value is None:
        return None

    value = value.strip()
    return value or None


def format_reference_image(reference_image_url):
    return f"[Open reference]({reference_image_url})" if reference_image_url else "None"


def is_ephemeral_discord_attachment(url: Optional[str]) -> bool:
    return bool(url and "/ephemeral-attachments/" in url)


def choose_renderable_reference_image(task_rows):
    reference_urls = [row[6] for row in task_rows if row[6]]
    for reference_url in reference_urls:
        if not is_ephemeral_discord_attachment(reference_url):
            return reference_url
    return None


def is_image_attachment(attachment: discord.Attachment) -> bool:
    content_type = attachment.content_type or ""
    filename = attachment.filename.casefold()
    return content_type.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))


def extract_reference_image_from_message(message: discord.Message):
    fallback_url = None

    for attachment in message.attachments:
        if not is_image_attachment(attachment):
            continue
        if not is_ephemeral_discord_attachment(attachment.url):
            return attachment.url
        fallback_url = fallback_url or getattr(attachment, "proxy_url", None)

    for embed in message.embeds:
        image_url = getattr(getattr(embed, "image", None), "url", None)
        if image_url and not is_ephemeral_discord_attachment(image_url):
            return image_url
        fallback_url = fallback_url or getattr(getattr(embed, "image", None), "proxy_url", None)

        thumbnail_url = getattr(getattr(embed, "thumbnail", None), "url", None)
        if thumbnail_url and not is_ephemeral_discord_attachment(thumbnail_url):
            return thumbnail_url
        fallback_url = fallback_url or getattr(getattr(embed, "thumbnail", None), "proxy_url", None)

    for url in re.findall(r"https?://\S+", message.content or ""):
        cleaned_url = url.rstrip(">)].,")
        if not is_ephemeral_discord_attachment(cleaned_url):
            return cleaned_url

    return fallback_url


def fetch_user_level(cursor, user_id: int):
    cursor.execute("SELECT level FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 1


def check_user_min_level(cursor, user_id: int, min_level):
    user_level = fetch_user_level(cursor, user_id)
    if min_level and user_level < min_level:
        return False, user_level
    return True, user_level


def build_task_board_filter(category_key: str):
    config = TASK_BOARD_GROUPS[category_key]
    clauses = ["status IN ('Available', 'Unassigned')"]
    params = []

    variants = config["variants"]
    variant_placeholders = ", ".join("?" for _ in variants)
    clauses.append(f"variant IN ({variant_placeholders})")
    params.extend(variants)

    sprite_types = config["sprite_types"]
    if sprite_types:
        sprite_type_placeholders = ", ".join("?" for _ in sprite_types)
        clauses.append(f"sprite_type IN ({sprite_type_placeholders})")
        params.extend(sprite_types)

    return " AND ".join(clauses), tuple(params)


def build_task_board_scope_filter(category_key: str, statuses):
    config = TASK_BOARD_GROUPS[category_key]
    clauses = [f"status IN ({', '.join('?' for _ in statuses)})"]
    params = list(statuses)

    variants = config["variants"]
    variant_placeholders = ", ".join("?" for _ in variants)
    clauses.append(f"variant IN ({variant_placeholders})")
    params.extend(variants)

    sprite_types = config["sprite_types"]
    if sprite_types:
        sprite_type_placeholders = ", ".join("?" for _ in sprite_types)
        clauses.append(f"sprite_type IN ({sprite_type_placeholders})")
        params.extend(sprite_types)

    return " AND ".join(clauses), tuple(params)


def fetch_available_task_groups(db_path: str, category_key: str):
    where_clause, params = build_task_board_filter(category_key)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT pokedex_identifier, COUNT(*), MIN(min_level), MAX(reference_image_url)
            FROM tasks
            WHERE {where_clause}
            GROUP BY pokedex_identifier
            ORDER BY pokedex_identifier COLLATE NOCASE
        """, params)
        return cursor.fetchall()


def fetch_available_group_tasks(db_path: str, category_key: str, identifier: str):
    where_clause, params = build_task_board_filter(category_key)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT task_id, variant, sprite_type, pokedex_identifier, min_level
            FROM tasks
            WHERE {where_clause}
              AND pokedex_identifier = ?
            ORDER BY
                CASE variant
                    WHEN 'Base' THEN 1
                    WHEN 'Shiny' THEN 2
                    WHEN 'Anomaly' THEN 3
                    WHEN 'Character' THEN 4
                    WHEN 'Audio' THEN 5
                    ELSE 5
                END,
                CASE sprite_type
                    WHEN 'Design' THEN 1
                    WHEN 'Overworld' THEN 2
                    WHEN 'Battler' THEN 3
                    ELSE 4
                END,
                sprite_type COLLATE NOCASE
        """, (*params, identifier))
        return cursor.fetchall()


def fetch_task_group_details(db_path: str, category_key: str, identifier: str):
    statuses = ("Available", "Unassigned", "Assigned", "Waiting For Feedback", "Completed")
    where_clause, params = build_task_board_scope_filter(category_key, statuses)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT task_id, variant, sprite_type, status, user_id, min_level,
                   reference_image_url, forum_thread_id
            FROM tasks
            WHERE {where_clause}
              AND pokedex_identifier = ?
            ORDER BY
                CASE variant
                    WHEN 'Base' THEN 1
                    WHEN 'Shiny' THEN 2
                    WHEN 'Anomaly' THEN 3
                    WHEN 'Character' THEN 4
                    WHEN 'Audio' THEN 5
                    ELSE 6
                END,
                CASE sprite_type
                    WHEN 'Icon' THEN 0
                    WHEN 'Front' THEN 1
                    WHEN 'Front 2' THEN 2
                    WHEN 'Back' THEN 3
                    WHEN 'Design' THEN 1
                    WHEN 'Overworld' THEN 2
                    WHEN 'Battler' THEN 3
                    ELSE 4
                END,
                sprite_type COLLATE NOCASE
        """, (*params, identifier))
        return cursor.fetchall()


def format_board_task_label(variant: str, sprite_type: str):
    sprite_suffixes = {
        "Front": "F",
        "Front 2": "F2",
        "Back": "B",
        "Icon": "Icons",
    }
    if variant == "Base":
        if sprite_type == "Icon":
            return "Icons"
        return f"Normal{sprite_suffixes.get(sprite_type, sprite_type)}"
    if variant in {"Shiny", "Anomaly"}:
        return f"{variant}{sprite_suffixes.get(sprite_type, sprite_type)}"
    if variant == "Character":
        return sprite_type
    if variant == "Audio":
        return sprite_type
    return f"{variant} {sprite_type}"


def format_board_task_line(variant: str, sprite_type: str, status: str, user_id):
    line = format_board_task_label(variant, sprite_type)
    if user_id:
        return f"{line} <@{user_id}>"
    if status == "Waiting For Feedback":
        return f"{line} (Waiting For Feedback)"
    if status == "Completed":
        return f"{line} (Completed)"
    return line


def build_board_task_lines(category_key: str, task_rows):
    if category_key != "pokemon_sprite":
        return [
            format_board_task_line(variant, sprite_type, status, user_id)
            for _task_id, variant, sprite_type, status, user_id, _min_level, _reference_image_url, _thread_id in task_rows
        ]

    task_by_slot = {
        (variant, sprite_type): (status, user_id)
        for _task_id, variant, sprite_type, status, user_id, _min_level, _reference_image_url, _thread_id in task_rows
    }
    lines = []
    previous_variant = None
    for variant, sprite_type in POKEMON_BOARD_TASK_SLOTS:
        if previous_variant and previous_variant != variant:
            lines.append("")
        status, user_id = task_by_slot.get((variant, sprite_type), ("Available", None))
        lines.append(format_board_task_line(variant, sprite_type, status, user_id))
        previous_variant = variant
    return lines


def parse_pokemon_sprite_alias(value: str):
    clean_value = re.sub(r"[\s_-]+", "", value.strip().casefold())
    aliases = {
        "f": ("Base", "Front"),
        "front": ("Base", "Front"),
        "basefront": ("Base", "Front"),
        "f2": ("Base", "Front 2"),
        "front2": ("Base", "Front 2"),
        "basefront2": ("Base", "Front 2"),
        "b": ("Base", "Back"),
        "back": ("Base", "Back"),
        "baseback": ("Base", "Back"),
        "sf": ("Shiny", "Front"),
        "shinyfront": ("Shiny", "Front"),
        "sf2": ("Shiny", "Front 2"),
        "shinyfront2": ("Shiny", "Front 2"),
        "sb": ("Shiny", "Back"),
        "shinyback": ("Shiny", "Back"),
        "af": ("Anomaly", "Front"),
        "anomalyfront": ("Anomaly", "Front"),
        "af2": ("Anomaly", "Front 2"),
        "anomalyfront2": ("Anomaly", "Front 2"),
        "ab": ("Anomaly", "Back"),
        "anomalyback": ("Anomaly", "Back"),
        "i": ("Base", "Icon"),
        "icon": ("Base", "Icon"),
        "baseicon": ("Base", "Icon"),
    }
    return aliases.get(clean_value)


def build_identifier_search(identifier: Optional[str]):
    if not identifier:
        return "", ()

    clean_identifier = identifier.strip()
    if not clean_identifier:
        return "", ()

    normalized_identifier = normalize_pokemon_identifier(clean_identifier)
    if normalized_identifier:
        return "AND pokedex_identifier = ?", (normalized_identifier,)

    lowered_identifier = clean_identifier.casefold()
    if clean_identifier.isdigit():
        dex_number = str(int(clean_identifier))
        return (
            "AND (pokedex_identifier LIKE ? OR pokedex_identifier LIKE ?)",
            (f"{clean_identifier} - %", f"{dex_number} - %"),
        )

    return (
        "AND (LOWER(pokedex_identifier) = ? OR LOWER(pokedex_identifier) LIKE ?)",
        (lowered_identifier, f"% - {lowered_identifier}"),
    )


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
            for key, category in TASK_BOARD_CATEGORY_OPTIONS.items()
        ]
        super().__init__(placeholder="Select a task category...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        category_key = self.values[0]
        groups = fetch_available_task_groups(self.db_path, category_key)
        category = TASK_BOARD_CATEGORY_OPTIONS[category_key]

        if not groups:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title=category["title"],
                    description=TASK_BOARD_GROUPS[category_key]["empty_message"],
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
            return

        view = AvailableTaskGroupView(self.bot, self.db_path, category_key, groups, interaction.user.id)
        await interaction.response.send_message(embed=await view.build_embed(), view=view)


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
    def __init__(self, bot, db_path: str, available_tasks, placeholder: str = "Select a task from this thread..."):
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
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

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
                embed = discord.Embed(
                    title=getattr(interaction.channel, "name", "Assign Task"),
                    description=(
                        f"Choose the task to assign in this thread, then pick who should get it.\n"
                        f"Current assignee choice: {assignee.mention}"
                    ),
                    color=discord.Color.dark_grey()
                )
                await interaction.response.edit_message(
                    embed=embed,
                    view=AssignThreadAvailableTaskView(
                        self.bot,
                        self.db_path,
                        available_tasks,
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
            description=f"Assigning to {self.assignee_mention}. Choose one or more available tasks.",
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
        self.tasks_by_id = {}
        options = [
            discord.SelectOption(
                label=identifier[:100],
                value=str(task_id),
                description=f"{variant} {sprite_type} | {format_min_level(min_level)}"[:100],
            )
            for task_id, identifier, min_level in available_tasks
        ]
        for task_id, identifier, min_level in available_tasks:
            self.tasks_by_id[int(task_id)] = (identifier, min_level)
        super().__init__(
            placeholder="Select one or more available tasks to assign...",
            min_values=1,
            max_values=min(25, len(options)),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.director_id or not has_director_role(interaction.user):
            await interaction.response.send_message("❌ Only the Director who opened this menu can use it.", ephemeral=True)
            return

        await interaction.response.defer()
        tasks_cog = interaction.client.get_cog("Tasks") or self.bot.get_cog("Tasks")
        if tasks_cog is None:
            await interaction.edit_original_response(content="❌ The task manager is not available right now.", embed=None, view=None)
            return

        await tasks_cog.assign_available_tasks_to_member(
            interaction,
            self.assignee_id,
            self.assignee_mention,
            self.values,
            interaction.user.mention,
        )


class AssignThreadAvailableTaskDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str, available_tasks, director_id: int):
        self.bot = bot
        self.db_path = db_path
        self.director_id = director_id
        self.tasks_by_id = {}
        options = [
            discord.SelectOption(
                label=f"{variant} {sprite_type}"[:100],
                value=str(task_id),
                description=f"{identifier} | {format_min_level(min_level)}"[:100],
            )
            for task_id, variant, sprite_type, identifier, min_level in available_tasks
        ]
        for task_id, variant, sprite_type, identifier, min_level in available_tasks:
            self.tasks_by_id[int(task_id)] = (variant, sprite_type, identifier, min_level)
        super().__init__(
            placeholder="Select the task to assign...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.director_id or not has_director_role(interaction.user):
            await interaction.response.send_message("❌ Only the Director who opened this menu can use it.", ephemeral=True)
            return

        task_id = int(self.values[0])
        task = self.tasks_by_id.get(task_id)
        if not task:
            await interaction.response.edit_message(content="❌ That task is no longer available.", embed=None, view=None)
            return

        variant, sprite_type, identifier, min_level = task
        embed = discord.Embed(
            title=f"Assign {variant} {sprite_type}",
            description=(
                f"**Task:** {variant} {sprite_type} - {identifier}\n"
                f"**Minimum level:** {format_min_level(min_level)}\n\n"
                "Now choose who should get this task."
            ),
            color=discord.Color.dark_grey()
        )
        await interaction.response.edit_message(
            embed=embed,
            view=AssignThreadAssigneeView(
                self.bot,
                self.db_path,
                task_id,
                variant,
                sprite_type,
                identifier,
                min_level,
                self.director_id,
            )
        )


class AssignThreadAssigneeSelect(discord.ui.UserSelect):
    def __init__(self, bot, db_path: str, task_id: int, variant: str, sprite_type: str, identifier: str, min_level, director_id: int):
        self.bot = bot
        self.db_path = db_path
        self.task_id = task_id
        self.variant = variant
        self.sprite_type = sprite_type
        self.identifier = identifier
        self.min_level = min_level
        self.director_id = director_id
        super().__init__(placeholder="Select the assignee for this task...", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.director_id or not has_director_role(interaction.user):
            await interaction.response.send_message("❌ Only the Director who opened this menu can use it.", ephemeral=True)
            return

        assignee = self.values[0]
        tasks_cog = interaction.client.get_cog("Tasks") or self.bot.get_cog("Tasks")
        if tasks_cog is None:
            await interaction.response.edit_message(content="❌ The task manager is not available right now.", embed=None, view=None)
            return

        await interaction.response.defer()
        await tasks_cog.assign_available_tasks_to_member(
            interaction,
            assignee.id,
            assignee.mention,
            [self.task_id],
            interaction.user.mention,
            thread_id=interaction.channel.id if isinstance(interaction.channel, discord.Thread) else None,
        )


class AssignThreadAssigneeView(discord.ui.View):
    def __init__(self, bot, db_path: str, task_id: int, variant: str, sprite_type: str, identifier: str, min_level, director_id: int):
        super().__init__(timeout=300)
        self.add_item(
            AssignThreadAssigneeSelect(
                bot,
                db_path,
                task_id,
                variant,
                sprite_type,
                identifier,
                min_level,
                director_id,
            )
        )


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
                cursor.execute("""
                    UPDATE tasks
                    SET status = 'Waiting For Feedback', feedback_message_url = NULL
                    WHERE task_id = ?
                """, (task_id,))
                conn.commit()

            status_message = await update_task_bundle_forum_status(
                self.bot,
                self.db_path,
                thread_id,
                f"{interaction.user.mention} marked this task as Waiting for Feedback."
            )
            if status_message:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE tasks SET feedback_message_url = ? WHERE task_id = ?",
                        (status_message.jump_url, task_id),
                    )
                    conn.commit()
                await update_task_forum_summary(self.bot, self.db_path, thread_id)

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


class DirectCompleteDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str, tasks, director_id: int):
        self.bot = bot
        self.db_path = db_path
        self.director_id = director_id
        self.tasks_by_id = {}
        options = []

        for task_id, variant, sprite_type, identifier, status, artist_name, due_date_str in tasks:
            task_id = int(task_id)
            self.tasks_by_id[task_id] = (task_id, variant, sprite_type, identifier)
            due_text = "No due date"
            if due_date_str:
                due_text = datetime.fromisoformat(due_date_str).strftime('%b %d, %Y')

            options.append(discord.SelectOption(
                label=f"{variant} {sprite_type} - {identifier}"[:100],
                value=str(task_id),
                description=f"{status} | {artist_name} | Due {due_text}"[:100],
            ))

        super().__init__(placeholder="Select a task to mark complete...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.director_id or not has_director_role(interaction.user):
            await interaction.response.send_message("❌ Only the Director who opened this menu can use it.", ephemeral=True)
            return

        task_id = int(self.values[0])
        if task_id not in self.tasks_by_id:
            await interaction.response.edit_message(content="❌ That task is no longer available.", embed=None, view=None)
            return

        tasks_cog = interaction.client.get_cog("Tasks") or self.bot.get_cog("Tasks")
        if tasks_cog is None:
            await interaction.response.edit_message(content="❌ The task manager is not available right now.", embed=None, view=None)
            return

        result, error = await tasks_cog.mark_task_complete_directly(interaction, task_id)
        if error:
            await interaction.response.edit_message(content=error, embed=None, view=None)
            return

        previous_status = result["status"]
        message = (
            f"✅ **{result['variant']} {result['sprite_type']} — {result['identifier']}** was marked as **Completed** by {interaction.user.mention}."
        )
        if previous_status == "Waiting For Feedback" and result["completion_message_url"]:
            message += "\nThe original feedback submission was linked as the completion reference."

        if result["thread_id"]:
            await update_task_bundle_forum_status(
                self.bot,
                self.db_path,
                result["thread_id"],
                f"{interaction.user.mention} marked this task as Completed without waiting for feedback."
            )

        await interaction.response.edit_message(
            content=message,
            embed=None,
            view=None
        )


class DirectCompleteView(discord.ui.View):
    def __init__(self, bot, db_path: str, tasks, director_id: int):
        super().__init__(timeout=300)
        self.add_item(DirectCompleteDropdown(bot, db_path, tasks, director_id))


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


class ReassignTaskSelectDropdown(discord.ui.Select):
    def __init__(self, bot, db_path: str, reassignable_tasks, director_id: int):
        self.bot = bot
        self.db_path = db_path
        self.director_id = director_id
        self.tasks_by_id = {}
        options = []

        for task_id, variant, sprite_type, identifier, status, user_id, artist_name, due_date_str in reassignable_tasks:
            task_id = int(task_id)
            self.tasks_by_id[task_id] = (
                task_id,
                variant,
                sprite_type,
                identifier,
                status,
                user_id,
                artist_name,
                due_date_str,
            )

            due_text = "No due date"
            if due_date_str:
                due_text = datetime.fromisoformat(due_date_str).strftime('%b %d, %Y')

            if status == "Completed":
                description = f"Completed by {artist_name}"
            else:
                description = f"{status} | {artist_name} | Due {due_text}"

            options.append(discord.SelectOption(
                label=f"{variant} {sprite_type} - {identifier}"[:100],
                value=str(task_id),
                description=description[:100],
            ))

        super().__init__(placeholder="Select the task to reassign...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.director_id or not has_director_role(interaction.user):
            await interaction.response.send_message("❌ Only the Director who opened this menu can use it.", ephemeral=True)
            return

        task_id = int(self.values[0])
        task = self.tasks_by_id.get(task_id)
        if not task:
            await interaction.response.edit_message(content="❌ That task is no longer available for reassignment.", embed=None, view=None)
            return

        _task_id, variant, sprite_type, identifier, status, user_id, artist_name, due_date_str = task
        due_text = "No due date"
        if due_date_str:
            due_text = datetime.fromisoformat(due_date_str).strftime('%b %d, %Y')

        assigned_text = f"<@{user_id}>" if user_id else "Unassigned"
        embed = discord.Embed(
            title=f"Reassign {variant} {sprite_type}",
            description=(
                f"**Task:** {variant} {sprite_type} - {identifier}\n"
                f"**Current assignee:** {assigned_text}\n"
                f"**Status:** {status}\n"
                f"**Due:** {due_text}\n\n"
                "Choose the new assignee."
            ),
            color=discord.Color.dark_grey()
        )
        await interaction.response.edit_message(
            embed=embed,
            view=ReassignTaskAssigneeView(
                self.bot,
                self.db_path,
                task,
                self.director_id
            )
        )


class ReassignTaskAssigneeSelect(discord.ui.UserSelect):
    def __init__(self, bot, db_path: str, task, director_id: int):
        self.bot = bot
        self.db_path = db_path
        self.task = task
        self.director_id = director_id
        super().__init__(placeholder="Select the new assignee...", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.director_id or not has_director_role(interaction.user):
            await interaction.response.send_message("❌ Only the Director who opened this menu can use it.", ephemeral=True)
            return

        assignee = self.values[0]
        task_id, variant, sprite_type, identifier, status, user_id, artist_name, due_date_str = self.task
        tasks_cog = interaction.client.get_cog("Tasks") or self.bot.get_cog("Tasks")
        if tasks_cog is None:
            await interaction.response.edit_message(content="❌ The task manager is not available right now.", embed=None, view=None)
            return

        result, error = await tasks_cog.reassign_task_to_member(interaction, task_id, assignee)
        if error:
            await interaction.response.edit_message(content=error, embed=None, view=None)
            return

        previous_user_mention = f"<@{result['previous_user_id']}>" if result["previous_user_id"] else result["previous_user_name"]
        if result["status"] == "Completed":
            description = (
                f"✅ **Completed task reassigned.**\n"
                f"**Task:** {result['variant']} {result['sprite_type']} - {result['identifier']}\n"
                f"**From:** {previous_user_mention}\n"
                f"**To:** {result['new_user_mention']}\n"
                f"**Credit:** completion count transferred."
            )
        else:
            description = (
                f"✅ **Task reassigned.**\n"
                f"**Task:** {result['variant']} {result['sprite_type']} - {result['identifier']}\n"
                f"**From:** {previous_user_mention}\n"
                f"**To:** {result['new_user_mention']}\n"
                f"**Deadline:** {result['new_due_date'].strftime('%b %d, %Y')}"
            )

        if result["thread_id"]:
            if result["status"] == "Completed":
                forum_message = (
                    f"This completed task was reassigned from {previous_user_mention} "
                    f"to {result['new_user_mention']} by {interaction.user.mention}."
                )
            else:
                forum_message = (
                    f"This task was reassigned from {previous_user_mention} "
                    f"to {result['new_user_mention']} by {interaction.user.mention}."
                )
            await update_task_bundle_forum_status(
                self.bot,
                self.db_path,
                result["thread_id"],
                forum_message,
            )

        await interaction.response.edit_message(
            content=description,
            embed=None,
            view=None
        )


class ReassignTaskAssigneeView(discord.ui.View):
    def __init__(self, bot, db_path: str, task, director_id: int):
        super().__init__(timeout=300)
        self.add_item(ReassignTaskAssigneeSelect(bot, db_path, task, director_id))


class ReassignTaskSelectView(discord.ui.View):
    def __init__(self, bot, db_path: str, reassignable_tasks, director_id: int):
        super().__init__(timeout=300)
        self.add_item(ReassignTaskSelectDropdown(bot, db_path, reassignable_tasks, director_id))


class Tasks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "data/corelet.db"
        self.task_picker_prompts = {}
        self.tasks_per_level = 5
        self.ensure_schema()
        # Start the background loop when the cog is loaded
        self.check_deadlines.start() 

    def ensure_schema(self):
        ensure_task_link_columns(self.db_path)
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

    def ensure_user_record(self, cursor, user_id: int, discord_name: str):
        cursor.execute("""
            INSERT INTO users (user_id, discord_name)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                discord_name = excluded.discord_name
        """, (user_id, discord_name))

    def adjust_user_task_total(self, cursor, user_id: int, delta: int, discord_name: str):
        self.ensure_user_record(cursor, user_id, discord_name)
        cursor.execute("SELECT tasks_completed FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        current_completed = int(row[0] or 0) if row else 0
        new_completed = max(0, current_completed + delta)
        new_level = (new_completed // self.tasks_per_level) + 1
        cursor.execute(
            "UPDATE users SET tasks_completed = ?, level = ? WHERE user_id = ?",
            (new_completed, new_level, user_id),
        )
        return new_completed, new_level

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

    def remember_task_picker_prompt(self, channel_id: int, user_id: int):
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        self.task_picker_prompts[(channel_id, user_id)] = expires_at

    def has_active_task_picker_prompt(self, channel_id: int, user_id: int) -> bool:
        now = datetime.now(timezone.utc)
        expired_keys = [
            key for key, expires_at in self.task_picker_prompts.items()
            if expires_at <= now
        ]
        for key in expired_keys:
            self.task_picker_prompts.pop(key, None)

        return self.task_picker_prompts.pop((channel_id, user_id), None) is not None

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

    def get_reassignable_tasks(self, channel):
        statuses = ("Assigned", "Waiting For Feedback", "Completed")
        status_placeholders = ", ".join("?" for _ in statuses)
        if isinstance(channel, discord.Thread):
            return self.fetch_query(f"""
                SELECT t.task_id, t.variant, t.sprite_type, t.pokedex_identifier, t.status, t.user_id,
                       COALESCE(u.discord_name, 'Unknown User'), t.due_date
                FROM tasks t
                LEFT JOIN users u ON t.user_id = u.user_id
                WHERE t.forum_thread_id = ?
                  AND t.status IN ({status_placeholders})
                ORDER BY
                    CASE t.status
                        WHEN 'Assigned' THEN 1
                        WHEN 'Waiting For Feedback' THEN 2
                        WHEN 'Completed' THEN 3
                        ELSE 4
                    END,
                    t.due_date ASC,
                    t.variant,
                    t.sprite_type,
                    t.pokedex_identifier COLLATE NOCASE
                LIMIT 25
            """, (channel.id, *statuses))

        return self.fetch_query(f"""
            SELECT t.task_id, t.variant, t.sprite_type, t.pokedex_identifier, t.status, t.user_id,
                   COALESCE(u.discord_name, 'Unknown User'), t.due_date
            FROM tasks t
            LEFT JOIN users u ON t.user_id = u.user_id
            WHERE t.status IN ({status_placeholders})
            ORDER BY
                CASE t.status
                    WHEN 'Assigned' THEN 1
                    WHEN 'Waiting For Feedback' THEN 2
                    WHEN 'Completed' THEN 3
                    ELSE 4
                END,
                t.due_date ASC,
                t.variant,
                t.sprite_type,
                t.pokedex_identifier COLLATE NOCASE
            LIMIT 25
            """, statuses)

    def get_direct_complete_tasks(self, channel):
        statuses = ("Assigned", "Waiting For Feedback")
        status_placeholders = ", ".join("?" for _ in statuses)
        if isinstance(channel, discord.Thread):
            return self.fetch_query(f"""
                SELECT t.task_id, t.variant, t.sprite_type, t.pokedex_identifier, t.status,
                       COALESCE(u.discord_name, 'Unknown User'), t.due_date
                FROM tasks t
                LEFT JOIN users u ON t.user_id = u.user_id
                WHERE t.forum_thread_id = ?
                  AND t.status IN ({status_placeholders})
                ORDER BY
                    CASE t.status
                        WHEN 'Waiting For Feedback' THEN 1
                        WHEN 'Assigned' THEN 2
                        ELSE 3
                    END,
                    t.due_date ASC,
                    t.variant,
                    t.sprite_type,
                    t.pokedex_identifier COLLATE NOCASE
                LIMIT 25
            """, (channel.id, *statuses))

        return self.fetch_query(f"""
            SELECT t.task_id, t.variant, t.sprite_type, t.pokedex_identifier, t.status,
                   COALESCE(u.discord_name, 'Unknown User'), t.due_date
            FROM tasks t
            LEFT JOIN users u ON t.user_id = u.user_id
            WHERE t.status IN ({status_placeholders})
            ORDER BY
                CASE t.status
                    WHEN 'Waiting For Feedback' THEN 1
                    WHEN 'Assigned' THEN 2
                    ELSE 3
                END,
                t.due_date ASC,
                t.variant,
                t.sprite_type,
                t.pokedex_identifier COLLATE NOCASE
            LIMIT 25
        """, statuses)

    async def reassign_task_to_member(self, interaction: discord.Interaction, task_id: int, assignee: discord.Member):
        now = datetime.now(timezone.utc)
        new_due_date = now + timedelta(days=7)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.task_id, t.status, t.user_id, t.variant, t.sprite_type, t.pokedex_identifier,
                       t.forum_thread_id, t.due_date, t.assigned_date, t.feedback_message_url,
                       t.completion_message_url, COALESCE(u.discord_name, 'Unknown User')
                FROM tasks t
                LEFT JOIN users u ON t.user_id = u.user_id
                WHERE t.task_id = ?
            """, (task_id,))
            task = cursor.fetchone()

            if not task:
                return None, "❌ That task could not be found."

            (
                task_id,
                status,
                current_user_id,
                variant,
                sprite_type,
                identifier,
                thread_id,
                _due_date,
                _assigned_date,
                _feedback_message_url,
                _completion_message_url,
                current_user_name,
            ) = task

            if current_user_id is None:
                return None, "❌ That task does not currently have an assignee to transfer from."

            if current_user_id == assignee.id:
                return None, f"❌ **{variant} {sprite_type} — {identifier}** is already assigned to {assignee.mention}."

            if status == "Completed":
                self.adjust_user_task_total(cursor, current_user_id, -1, current_user_name)
                self.adjust_user_task_total(cursor, assignee.id, 1, getattr(assignee, "display_name", assignee.name))
                cursor.execute("""
                    UPDATE tasks
                    SET user_id = ?
                    WHERE task_id = ?
                """, (assignee.id, task_id))
            else:
                cursor.execute("""
                    UPDATE tasks
                    SET user_id = ?, status = 'Assigned', assigned_date = ?, due_date = ?,
                        feedback_message_url = NULL, completion_message_url = NULL
                    WHERE task_id = ?
                """, (assignee.id, now.isoformat(), new_due_date.isoformat(), task_id))

            conn.commit()

        return {
            "task_id": task_id,
            "status": status,
            "variant": variant,
            "sprite_type": sprite_type,
            "identifier": identifier,
            "thread_id": thread_id,
            "previous_user_id": current_user_id,
            "previous_user_name": current_user_name,
            "new_user_id": assignee.id,
            "new_user_name": getattr(assignee, "display_name", assignee.name),
            "new_user_mention": assignee.mention,
            "new_due_date": new_due_date,
        }, None

    async def mark_task_complete_directly(self, interaction: discord.Interaction, task_id: int):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.task_id, t.status, t.user_id, t.variant, t.sprite_type, t.pokedex_identifier,
                       t.forum_thread_id, t.feedback_message_url, COALESCE(u.discord_name, 'Unknown User')
                FROM tasks t
                LEFT JOIN users u ON t.user_id = u.user_id
                WHERE t.task_id = ?
            """, (task_id,))
            task = cursor.fetchone()

            if not task:
                return None, "❌ That task could not be found."

            (
                task_id,
                status,
                user_id,
                variant,
                sprite_type,
                identifier,
                thread_id,
                feedback_message_url,
                current_user_name,
            ) = task

            if status not in ("Assigned", "Waiting For Feedback"):
                return None, f"❌ **{variant} {sprite_type} — {identifier}** is not in a state that can be marked complete."

            if user_id is None:
                return None, f"❌ **{variant} {sprite_type} — {identifier}** does not have an assignee yet."

            completion_message_url = feedback_message_url
            cursor.execute("""
                UPDATE tasks
                SET status = 'Completed',
                    completion_message_url = ?
                WHERE task_id = ?
            """, (completion_message_url, task_id))

            self.adjust_user_task_total(
                cursor,
                user_id,
                1,
                current_user_name,
            )

            conn.commit()

        return {
            "task_id": task_id,
            "variant": variant,
            "sprite_type": sprite_type,
            "identifier": identifier,
            "thread_id": thread_id,
            "status": status,
            "user_id": user_id,
            "user_name": current_user_name,
            "completion_message_url": completion_message_url,
        }, None

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
            description="Select Spriting, Characters, or Music below, or type one as your next message.",
            color=discord.Color.dark_grey()
        )
        self.remember_task_picker_prompt(interaction.channel.id, interaction.user.id)
        await interaction.response.send_message(
            embed=embed,
            view=TaskBoardView(self.bot, self.db_path)
        )

    async def send_task_group_picker(self, interaction: discord.Interaction, category_key: str):
        if isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "❌ Use this in the task request channel, or use `/taskmenu` inside a task forum thread.",
                ephemeral=True
            )
            return

        if not self.is_task_request_channel(interaction.channel):
            await interaction.response.send_message(
                "❌ Use this in the task request channel.",
                ephemeral=True
            )
            return

        groups = fetch_available_task_groups(self.db_path, category_key)
        category = TASK_BOARD_CATEGORY_OPTIONS[category_key]
        if not groups:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title=category["title"],
                    description=TASK_BOARD_GROUPS[category_key]["empty_message"],
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
            return

        view = AvailableTaskGroupView(self.bot, self.db_path, category_key, groups, interaction.user.id)
        await interaction.response.send_message(embed=await view.build_embed(), view=view)

    async def claim_available_task(
        self,
        interaction: discord.Interaction,
        user_id: int,
        user_mention: str,
        variant: str,
        sprite_type: str,
        identifier: Optional[str] = None,
    ):
        identifier_clause, identifier_params = build_identifier_search(identifier)
        thread_clause = ""
        thread_params = ()
        if isinstance(interaction.channel, discord.Thread) and not identifier_clause:
            thread_clause = "AND forum_thread_id = ?"
            thread_params = (interaction.channel.id,)
        elif not identifier_clause:
            await interaction.response.send_message(
                "❌ Include a Pokemon name or Dex number, or use this inside a task forum thread.",
                ephemeral=True
            )
            return

        now = datetime.now(timezone.utc)
        due_date = now + timedelta(days=7)

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT task_id, pokedex_identifier, status, forum_thread_id, min_level
                    FROM tasks
                    WHERE variant = ?
                      AND sprite_type = ?
                      AND status IN ('Available', 'Unassigned')
                      {identifier_clause}
                      {thread_clause}
                    ORDER BY pokedex_identifier COLLATE NOCASE
                    LIMIT 2
                """, (variant, sprite_type, *identifier_params, *thread_params))
                matches = cursor.fetchall()

                if not matches:
                    target = identifier.strip() if identifier else getattr(interaction.channel, "name", "this thread")
                    await interaction.response.send_message(
                        f"❌ Could not find an available **{variant} {sprite_type}** task for **{target}**.",
                        ephemeral=True
                    )
                    return

                if len(matches) > 1:
                    await interaction.response.send_message(
                        "❌ That matched more than one Pokemon. Use the full `Dex Number - Pokemon Name` format.",
                        ephemeral=True
                    )
                    return

                task_id, matched_identifier, status, thread_id, min_level = matches[0]
                has_level, user_level = check_user_min_level(cursor, user_id, min_level)
                if not has_level:
                    await interaction.response.send_message(
                        (
                            f"❌ **{variant} {sprite_type} — {matched_identifier}** requires "
                            f"**Level {min_level}**. You are currently **Level {user_level}**."
                        ),
                        ephemeral=True
                    )
                    return

                cursor.execute("""
                    UPDATE tasks
                    SET user_id = ?, status = 'Assigned', assigned_date = ?, due_date = ?
                    WHERE task_id = ?
                """, (user_id, now.isoformat(), due_date.isoformat(), task_id))
                conn.commit()

            await update_task_bundle_forum_status(
                self.bot,
                self.db_path,
                thread_id,
                f"{user_mention} claimed this task. Due: {due_date.strftime('%b %d, %Y')}."
            )

            await interaction.response.send_message(
                (
                    f"✅ Assigned **{variant} {sprite_type} — {matched_identifier}** to {user_mention}.\n"
                    f"Due: **{due_date.strftime('%b %d, %Y')}**"
                )
            )
        except discord.Forbidden as e:
            await interaction.response.send_message(discord_access_error_message(e), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)

    async def assign_available_tasks_to_member(
        self,
        interaction: discord.Interaction,
        assignee_id: int,
        assignee_mention: str,
        task_ids,
        director_mention: str,
        thread_id: Optional[int] = None,
    ):
        now = datetime.now(timezone.utc)
        due_date = now + timedelta(days=7)
        selected_task_ids = [int(task_id) for task_id in task_ids]
        assigned_tasks = []
        skipped_tasks = []
        forum_updates = []

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                for task_id in selected_task_ids:
                    cursor.execute("""
                        SELECT task_id, variant, sprite_type, pokedex_identifier, status, forum_thread_id, min_level
                        FROM tasks
                        WHERE task_id = ?
                    """, (task_id,))
                    task = cursor.fetchone()

                    if not task:
                        skipped_tasks.append(f"Task #{task_id} was not found.")
                        continue

                    task_id, variant, sprite_type, identifier, status, task_thread_id, min_level = task

                    if status not in AVAILABLE_TASK_STATUSES:
                        skipped_tasks.append(
                            f"**{variant} {sprite_type} - {identifier}** is no longer available."
                        )
                        continue

                    if thread_id is not None and task_thread_id != thread_id:
                        skipped_tasks.append(
                            f"**{variant} {sprite_type} - {identifier}** is not part of this thread."
                        )
                        continue

                    has_level, user_level = check_user_min_level(cursor, assignee_id, min_level)
                    if not has_level:
                        skipped_tasks.append(
                            f"**{variant} {sprite_type} - {identifier}** requires Level {min_level}, but {assignee_mention} is Level {user_level}."
                        )
                        continue

                    cursor.execute("""
                        UPDATE tasks
                        SET user_id = ?, status = 'Assigned', assigned_date = ?, due_date = ?
                        WHERE task_id = ?
                    """, (assignee_id, now.isoformat(), due_date.isoformat(), task_id))

                    assigned_tasks.append((variant, sprite_type, identifier))
                    forum_updates.append((task_thread_id, variant, sprite_type, identifier))

                conn.commit()

            for task_thread_id, variant, sprite_type, identifier in forum_updates:
                if not task_thread_id:
                    continue
                await update_task_bundle_forum_status(
                    self.bot,
                    self.db_path,
                    task_thread_id,
                    f"{assignee_mention} was assigned this task by {director_mention}. Due: {due_date.strftime('%b %d, %Y')}."
                )

            if assigned_tasks:
                description_lines = [
                    f"✅ Assigned **{len(assigned_tasks)}** task{'s' if len(assigned_tasks) != 1 else ''} to {assignee_mention}.",
                    f"Due: **{due_date.strftime('%b %d, %Y')}**",
                ]
                if assigned_tasks:
                    description_lines.append("")
                    description_lines.append("Assigned:")
                    for variant, sprite_type, identifier in assigned_tasks[:10]:
                        description_lines.append(f"- {variant} {sprite_type} — {identifier}")
                    if len(assigned_tasks) > 10:
                        description_lines.append(f"- ...and {len(assigned_tasks) - 10} more")
                if skipped_tasks:
                    description_lines.append("")
                    description_lines.append("Skipped:")
                    for message in skipped_tasks[:10]:
                        description_lines.append(f"- {message}")
                    if len(skipped_tasks) > 10:
                        description_lines.append(f"- ...and {len(skipped_tasks) - 10} more")

                embed = discord.Embed(
                    title="Tasks Assigned",
                    description="\n".join(description_lines),
                    color=discord.Color.green()
                )
            else:
                description_lines = [
                    "❌ None of the selected tasks could be assigned.",
                ]
                if skipped_tasks:
                    description_lines.append("")
                    description_lines.append("Skipped:")
                    for message in skipped_tasks[:10]:
                        description_lines.append(f"- {message}")
                    if len(skipped_tasks) > 10:
                        description_lines.append(f"- ...and {len(skipped_tasks) - 10} more")

                embed = discord.Embed(
                    title="No Tasks Assigned",
                    description="\n".join(description_lines),
                    color=discord.Color.red()
                )

            await interaction.edit_original_response(embed=embed, view=None)
        except discord.Forbidden as e:
            await interaction.edit_original_response(content=discord_access_error_message(e), embed=None, view=None)
        except Exception as e:
            await interaction.edit_original_response(content=f"Database error: {e}", embed=None, view=None)

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
            await interaction.response.send_message(embed=await view.build_embed(), view=view)

        except discord.Forbidden as e:
            await interaction.response.send_message(discord_access_error_message(e), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)


    async def assign_task_to_member(self, interaction: discord.Interaction, assignee: discord.Member, identifier: str, sprite_type: str, variant: str):
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

            await update_task_bundle_forum_status(
                self.bot,
                self.db_path,
                thread_id,
                f"{assignee.mention} was assigned this task. Due: {due_date.strftime('%b %d, %Y')}."
            )

            await interaction.response.send_message(f"✅ Assigned **{variant} {sprite_type} {identifier}** to {assignee.mention}. Due by: {due_date.strftime('%Y-%m-%d')}.")
        except discord.Forbidden as e:
            await interaction.response.send_message(discord_access_error_message(e), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Database error: {e}", ephemeral=True)

    @app_commands.command(name="assigntaskmenu", description="Open a menu for assigning an available task")
    async def assigntaskmenu(self, interaction: discord.Interaction):
        if not await self.require_director(interaction):
            return

        if isinstance(interaction.channel, discord.Thread):
            available_tasks = self.get_thread_available_tasks(interaction.channel)
            if not available_tasks:
                await interaction.response.send_message(
                    "❌ There are no available tasks in this thread.",
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title=getattr(interaction.channel, "name", "Assign Task"),
                description="Choose the task to assign first, then pick the assignee for that task.",
                color=discord.Color.dark_grey()
            )
            await interaction.response.send_message(
                embed=embed,
                view=AssignThreadAvailableTaskView(self.bot, self.db_path, available_tasks, interaction.user.id)
            )
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

    async def send_reassign_task_menu(self, interaction: discord.Interaction):
        if not await self.require_director(interaction):
            return

        reassignable_tasks = self.get_reassignable_tasks(interaction.channel)
        if not reassignable_tasks:
            await interaction.response.send_message(
                "❌ There are no assigned, waiting, or completed tasks available to reassign here.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Reassign Task",
            description="Choose the task first, then pick the new assignee.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.send_message(
            embed=embed,
            view=ReassignTaskSelectView(self.bot, self.db_path, reassignable_tasks, interaction.user.id)
        )

    @app_commands.command(name="reassigntaskmenu", description="Open a menu for reassigning an assigned or completed task")
    async def reassigntaskmenu(self, interaction: discord.Interaction):
        await self.send_reassign_task_menu(interaction)

    async def send_direct_complete_menu(self, interaction: discord.Interaction):
        if not await self.require_director(interaction):
            return

        direct_complete_tasks = self.get_direct_complete_tasks(interaction.channel)
        if not direct_complete_tasks:
            await interaction.response.send_message(
                "❌ There are no assigned or waiting tasks available to mark complete here.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Mark Task Complete",
            description="Choose a task to mark complete without waiting for feedback.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.send_message(
            embed=embed,
            view=DirectCompleteView(self.bot, self.db_path, direct_complete_tasks, interaction.user.id)
        )

    @app_commands.command(name="completetaskmenu", description="Open a menu for marking a task complete without waiting for feedback")
    async def completetaskmenu(self, interaction: discord.Interaction):
        await self.send_direct_complete_menu(interaction)

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
        name: Optional[str] = None,
        reference_image: Optional[discord.Attachment] = None,
        reference_image_url: Optional[str] = None,
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

    async def send_task_search(self, interaction: discord.Interaction, scope: Optional[str] = None, category: Optional[str] = None):
        can_do_only, category_key, unknown_terms = normalize_task_search_args(scope, category)
        if unknown_terms:
            await interaction.response.send_message(
                (
                    "❌ I did not understand: "
                    f"`{' '.join(unknown_terms)}`. Try `can`, `pokemon`, `character`, `music`, or `other`."
                ),
                ephemeral=True
            )
            return

        user_level = None
        if can_do_only:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                user_level = fetch_user_level(cursor, interaction.user.id)

        rows = fetch_available_task_search_rows(self.db_path, category_key, user_level)
        if not rows:
            title = "Available Task Search"
            filters = []
            if can_do_only:
                filters.append(f"Level {user_level} or lower")
            if category_key:
                filters.append(category_key.title())
            filter_text = f" matching **{', '.join(filters)}**" if filters else ""
            await interaction.response.send_message(f"❌ No available tasks found{filter_text}.", ephemeral=True)
            return

        title_parts = ["Available Tasks"]
        if category_key:
            title_parts.append(category_key.title())
        if can_do_only:
            title_parts.append(f"You Can Do, Lv {user_level}")

        view = LookForTasksView(
            self.db_path,
            interaction.user.id,
            rows,
            " - ".join(title_parts),
            can_do_only,
            category_key,
            user_level,
        )
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

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

    @app_commands.command(name="lookfortasks", description="Search available tasks by level and category")
    @app_commands.describe(
        scope="Optional: can, pokemon, character, music, other",
        category="Optional second filter: pokemon, character, music, other",
    )
    async def lookfortasks(self, interaction: discord.Interaction, scope: Optional[str] = None, category: Optional[str] = None):
        await self.send_task_search(interaction, scope, category)

    async def opentasks(self, interaction: discord.Interaction):
        await self.send_active_tasks(interaction)

    @app_commands.command(name="taskmenu", description="Open the task claim menu for this channel")
    async def taskmenu(self, interaction: discord.Interaction):
        await self.send_task_claim_menu(interaction)

    @app_commands.command(name="spritingtasks", description="Browse available Pokemon spriting tasks")
    async def spritingtasks(self, interaction: discord.Interaction):
        await self.send_task_group_picker(interaction, "pokemon_sprite")

    @app_commands.command(name="charactertasks", description="Browse available character tasks")
    async def charactertasks(self, interaction: discord.Interaction):
        await self.send_task_group_picker(interaction, "character_sprite")

    @app_commands.command(name="musictasks", description="Browse available music tasks")
    async def musictasks(self, interaction: discord.Interaction):
        await self.send_task_group_picker(interaction, "music")

    @app_commands.command(name="assigntask", description="Claim an available Pokemon sprite task")
    @app_commands.describe(
        pokemon="Dex number, Pokemon name, or 'Dex - Name'. Optional inside a task forum thread.",
        sprite="Task shorthand: f, f2, b, sf, sf2, sb, af, af2, ab, or icon.",
    )
    async def assigntask(self, interaction: discord.Interaction, pokemon: Optional[str] = None, sprite: Optional[str] = None):
        if sprite is None and pokemon and parse_pokemon_sprite_alias(pokemon):
            sprite = pokemon
            pokemon = None

        if sprite is None:
            await interaction.response.send_message(
                "❌ Include a sprite shorthand like `f`, `f2`, `b`, `sf`, `sf2`, `sb`, `af`, `af2`, `ab`, or `icon`.",
                ephemeral=True
            )
            return

        parsed_task = parse_pokemon_sprite_alias(sprite)
        if parsed_task is None:
            await interaction.response.send_message(
                "❌ Unknown sprite shorthand. Try `f`, `f2`, `b`, `sf`, `sf2`, `sb`, `af`, `af2`, `ab`, or `icon`.",
                ephemeral=True
            )
            return

        variant, sprite_type = parsed_task
        await self.claim_available_task(
            interaction,
            interaction.user.id,
            interaction.user.mention,
            variant,
            sprite_type,
            pokemon,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if isinstance(message.channel, discord.Thread):
            return
        if not self.is_task_request_channel(message.channel):
            return
        if not self.has_active_task_picker_prompt(message.channel.id, message.author.id):
            return

        content = message.content.strip().casefold()
        category_key = None
        if content in {"spriting", "sprite", "sprites", "pokemon", "pokemon spriting"}:
            category_key = "pokemon_sprite"
        elif content in {"character", "characters", "character tasks", "charactertasks"}:
            category_key = "character_sprite"
        elif content in {"music", "music tasks", "musictasks"}:
            category_key = "music"

        if category_key is None:
            return

        groups = fetch_available_task_groups(self.db_path, category_key)
        category = TASK_BOARD_CATEGORY_OPTIONS[category_key]
        if not groups:
            embed = discord.Embed(
                title=category["title"],
                description=TASK_BOARD_GROUPS[category_key]["empty_message"],
                color=discord.Color.red()
            )
            await message.channel.send(embed=embed)
            return

        view = AvailableTaskGroupView(self.bot, self.db_path, category_key, groups, message.author.id)
        await message.channel.send(embed=await view.build_embed(), view=view)

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
            self.execute_query("""
                UPDATE tasks
                SET status = 'Waiting For Feedback', feedback_message_url = NULL
                WHERE task_id = ?
            """, (task_id,))
            status_message = await update_task_bundle_forum_status(
                self.bot,
                self.db_path,
                thread_id,
                f"{interaction.user.mention} marked this task as Waiting for Feedback."
            )
            if status_message:
                self.execute_query(
                    "UPDATE tasks SET feedback_message_url = ? WHERE task_id = ?",
                    (status_message.jump_url, task_id),
                )
                await update_task_forum_summary(self.bot, self.db_path, thread_id)
            
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
            description="Select Spriting, Characters, or Music below to claim a new task.",
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
    def __init__(self, bot, db_path: str, available_tasks, placeholder: str = "Select a task from this thread..."):
        super().__init__(timeout=300)
        self.add_item(ThreadAvailableTaskDropdown(bot, db_path, available_tasks, placeholder))


class AvailableTaskGroupView(discord.ui.View):
    def __init__(self, bot, db_path: str, category_key: str, groups, requester_id: int, page_size: int = 1):
        super().__init__(timeout=300)
        self.bot = bot
        self.db_path = db_path
        self.category_key = category_key
        self.groups = groups
        self.requester_id = requester_id
        self.page_size = page_size
        self.page = 0
        self.thread_reference_cache = {}
        self.rebuild_items()

    @property
    def page_count(self) -> int:
        return max(1, (len(self.groups) + self.page_size - 1) // self.page_size)

    def rebuild_items(self):
        self.clear_items()
        if self.page_count > 1:
            self.add_item(self.previous_page)
            self.add_item(self.next_page)

    def current_identifier(self):
        return self.groups[self.page][0]

    async def fetch_thread_reference_image(self, thread_id):
        if not thread_id:
            return None
        if thread_id in self.thread_reference_cache:
            return self.thread_reference_cache[thread_id]

        reference_image_url = None
        try:
            thread = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
            if isinstance(thread, discord.Thread):
                starter_message = getattr(thread, "starter_message", None)
                if starter_message:
                    reference_image_url = extract_reference_image_from_message(starter_message)

                fetch_message = getattr(thread, "fetch_message", None)
                if reference_image_url is None and fetch_message:
                    try:
                        starter_message = await fetch_message(thread_id)
                        reference_image_url = extract_reference_image_from_message(starter_message)
                    except discord.HTTPException:
                        reference_image_url = None

                parent_fetch_message = getattr(getattr(thread, "parent", None), "fetch_message", None)
                if reference_image_url is None and parent_fetch_message:
                    try:
                        starter_message = await parent_fetch_message(thread_id)
                        reference_image_url = extract_reference_image_from_message(starter_message)
                    except discord.HTTPException:
                        reference_image_url = None

                if reference_image_url is None:
                    async for message in thread.history(limit=50, oldest_first=True):
                        reference_image_url = extract_reference_image_from_message(message)
                        if reference_image_url:
                            break
        except discord.HTTPException:
            reference_image_url = None

        self.thread_reference_cache[thread_id] = reference_image_url
        return reference_image_url

    async def build_embed(self):
        identifier = self.current_identifier()
        task_rows = fetch_task_group_details(self.db_path, self.category_key, identifier)
        reference_image_url = choose_renderable_reference_image(task_rows)
        has_expired_reference = (
            reference_image_url is None
            and any(is_ephemeral_discord_attachment(row[6]) for row in task_rows if row[6])
        )
        thread_id = next((row[7] for row in task_rows if row[7]), None)
        if reference_image_url is None:
            reference_image_url = await self.fetch_thread_reference_image(thread_id)
        if reference_image_url:
            has_expired_reference = False

        task_lines = build_board_task_lines(self.category_key, task_rows)

        description_parts = ["**Tasks**"]
        if thread_id:
            description_parts.append(f"<#{thread_id}>")
        if has_expired_reference:
            description_parts.append("*Reference image link expired. Re-add it with a non-ephemeral Discord attachment or image URL.*")
        description_parts.append("\n".join(task_lines) if task_lines else "No active task rows found.")

        embed = discord.Embed(
            title=identifier,
            description="\n".join(description_parts),
            color=discord.Color.dark_grey()
        )
        if reference_image_url:
            embed.set_image(url=reference_image_url)
        embed.set_footer(text=f"Next [<] [>] • {self.page + 1}/{len(self.groups)}")
        return embed

    @discord.ui.button(label="<", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Only the person who opened this picker can use it.", ephemeral=True)
            return

        self.page = (self.page - 1) % self.page_count
        self.rebuild_items()
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label=">", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Only the person who opened this picker can use it.", ephemeral=True)
            return

        self.page = (self.page + 1) % self.page_count
        self.rebuild_items()
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)


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
    def __init__(self, bot, db_path: str, available_tasks, director_id: int):
        super().__init__(timeout=300)
        self.add_item(AssignThreadAvailableTaskDropdown(bot, db_path, available_tasks, director_id))


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


class LookForTasksView(discord.ui.View):
    def __init__(
        self,
        db_path: str,
        requester_id: int,
        rows,
        title: str,
        can_do_only: bool,
        category_key: Optional[str],
        user_level: Optional[int],
        page_size: int = 10,
    ):
        super().__init__(timeout=300)
        self.db_path = db_path
        self.requester_id = requester_id
        self.rows = rows
        self.title = title
        self.can_do_only = can_do_only
        self.category_key = category_key
        self.user_level = user_level
        self.page_size = page_size
        self.page = 0
        self.rebuild_items()

    @property
    def page_count(self) -> int:
        return max(1, (len(self.rows) + self.page_size - 1) // self.page_size)

    def rebuild_items(self):
        self.clear_items()
        if self.page_count > 1:
            self.add_item(self.previous_page)
            self.add_item(self.next_page)

    def build_embed(self):
        embed = discord.Embed(
            title=self.title,
            color=discord.Color.dark_grey()
        )

        filters = []
        if self.can_do_only:
            filters.append(f"within your level range: Lv {self.user_level}")
        if self.category_key:
            filters.append(f"category: {self.category_key}")
        embed.description = "Filters: " + ", ".join(filters) if filters else "All available tasks."

        start = self.page * self.page_size
        end = start + self.page_size
        for task_id, variant, sprite_type, identifier, min_level, reference_image_url, thread_id in self.rows[start:end]:
            link_text = f"\n**Forum:** <#{thread_id}>" if thread_id else ""
            reference_text = ""
            if reference_image_url and not is_ephemeral_discord_attachment(reference_image_url):
                reference_text = f"\n**Reference:** [Open image]({reference_image_url})"

            embed.add_field(
                name=f"{variant} {sprite_type} - {identifier}"[:256],
                value=(
                    f"**Task ID:** {task_id}\n"
                    f"**Minimum Level:** {format_min_level(min_level)}"
                    f"{link_text}"
                    f"{reference_text}"
                ),
                inline=False,
            )

        embed.set_footer(text=f"Page {self.page + 1}/{self.page_count} - {len(self.rows)} matching tasks")
        return embed

    @discord.ui.button(label="<", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Only the person who opened this search can use it.", ephemeral=True)
            return

        self.page = (self.page - 1) % self.page_count
        self.rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label=">", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Only the person who opened this search can use it.", ephemeral=True)
            return

        self.page = (self.page + 1) % self.page_count
        self.rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


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


class SubmitForFeedbackDropdown(discord.ui.Select):
    def __init__(self, cog: Tasks, message: discord.Message, assigned_tasks):
        self.cog = cog
        self.message = message
        options = [
            discord.SelectOption(
                label=f"{variant} {sprite_type} - {identifier}"[:100],
                value=str(task_id),
                description="Link this message in the task breakdown",
            )
            for task_id, variant, sprite_type, identifier in assigned_tasks
        ]
        super().__init__(
            placeholder="Select the task this message is ready for...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        task_id = int(self.values[0])

        task = self.cog.fetch_query("""
            SELECT variant, sprite_type, pokedex_identifier, forum_thread_id
            FROM tasks
            WHERE task_id = ? AND user_id = ? AND status = 'Assigned'
        """, (task_id, interaction.user.id))

        if not task:
            await interaction.followup.send("❌ That task is no longer assigned to you.", ephemeral=True)
            return

        variant, sprite_type, identifier, thread_id = task[0]
        try:
            self.cog.execute_query("""
                UPDATE tasks
                SET status = 'Waiting For Feedback', feedback_message_url = ?
                WHERE task_id = ?
            """, (self.message.jump_url, task_id))

            await update_task_bundle_forum_status(
                self.cog.bot,
                self.cog.db_path,
                thread_id,
                f"{interaction.user.mention} marked this task as Waiting for Feedback: {self.message.jump_url}"
            )
            await update_task_forum_summary(self.cog.bot, self.cog.db_path, thread_id)

            await interaction.followup.send(
                f"✅ **{variant} {sprite_type} — {identifier}** has been marked as **Waiting For Feedback**.",
                ephemeral=True
            )
        except discord.Forbidden as e:
            await interaction.followup.send(discord_access_error_message(e), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Database error: {e}", ephemeral=True)


class SubmitForFeedbackView(discord.ui.View):
    def __init__(self, cog: Tasks, message: discord.Message, assigned_tasks):
        super().__init__(timeout=300)
        self.add_item(SubmitForFeedbackDropdown(cog, message, assigned_tasks))


async def request_feedback_context_menu(interaction: discord.Interaction, message: discord.Message):
    if not message.attachments:
        await interaction.response.send_message("❌ That message has no attachments to submit for feedback.", ephemeral=True)
        return

    cog = interaction.client.get_cog("Tasks")
    if cog is None:
        await interaction.response.send_message("❌ Task tools are not loaded.", ephemeral=True)
        return

    assigned_tasks = cog.fetch_query("""
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
        description="Choose the task this submission should be linked to.",
        color=discord.Color.dark_grey()
    )
    await interaction.response.send_message(
        embed=embed,
        view=SubmitForFeedbackView(cog, message, assigned_tasks),
        ephemeral=True
    )


async def setup(bot):
    await bot.add_cog(Tasks(bot))
    bot.tree.add_command(app_commands.ContextMenu(
        name="Request Feedback",
        callback=request_feedback_context_menu,
    ))
