import os
import sqlite3
from typing import Optional

import discord


STATUS_TAG_NAMES = {
    "Available": "Missing / Needed",
    "Unassigned": "Missing / Needed",
    "Assigned": "Assigned",
    "Waiting For Feedback": "Waiting for Feedback",
    "Completed": "Accepted - Finished",
}

TASK_SUMMARY_ORDER = {
    "pokemon": [
        ("Base", "Front"),
        ("Base", "Front 2"),
        ("Base", "Back"),
        ("Shiny", "Front"),
        ("Shiny", "Front 2"),
        ("Shiny", "Back"),
        ("Anomaly", "Front"),
        ("Anomaly", "Front 2"),
        ("Anomaly", "Back"),
        ("Base", "Icon"),
    ],
    "character": [
        ("Character", "Design"),
        ("Character", "Overworld"),
        ("Character", "Battler"),
    ],
    "audio": [
        ("Audio", "Music"),
        ("Audio", "Sound Effect"),
        ("Audio", "Cry"),
    ],
}

STATUS_LABELS = {
    "Available": "Missing",
    "Unassigned": "Missing",
    "Assigned": "Assigned",
    "Waiting For Feedback": "Waiting for Feedback",
    "Completed": "Completed",
    "Removed": "Removed",
}


def ensure_task_link_columns(db_path: str):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(tasks)")
        columns = {row[1] for row in cursor.fetchall()}

        if "feedback_message_url" not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN feedback_message_url TEXT")
        if "completion_message_url" not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN completion_message_url TEXT")

        conn.commit()


def get_task_forum_id(variant: str, sprite_type: str):
    if variant == "Audio" and sprite_type == "Music":
        forum_id = os.getenv("MUSIC_TASK_FORUM_CHANNEL_ID") or os.getenv("SOUNDS_TASK_FORUM_CHANNEL_ID")
    elif variant == "Audio" and sprite_type == "Cry":
        forum_id = os.getenv("CRIES_TASK_FORUM_CHANNEL_ID") or os.getenv("SOUNDS_TASK_FORUM_CHANNEL_ID")
    elif variant == "Audio" and sprite_type == "Sound Effect":
        forum_id = os.getenv("SFX_TASK_FORUM_CHANNEL_ID") or os.getenv("SOUNDS_TASK_FORUM_CHANNEL_ID")
    elif variant == "Audio":
        forum_id = os.getenv("SOUNDS_TASK_FORUM_CHANNEL_ID")
    elif variant == "Character":
        forum_id = os.getenv("CHARACTER_TASK_FORUM_CHANNEL_ID")
    else:
        forum_id = os.getenv("POKEMON_TASK_FORUM_CHANNEL_ID")

    if not forum_id:
        forum_id = os.getenv("TASK_FORUM_CHANNEL_ID")

    if not forum_id:
        return None

    try:
        return int(forum_id)
    except ValueError:
        return None


def get_forum_tag(forum_channel: discord.ForumChannel, tag_name: str):
    normalized_name = tag_name.casefold()
    for tag in forum_channel.available_tags:
        if tag.name.casefold() == normalized_name:
            return tag
    return None


def get_status_tag(forum_channel: discord.ForumChannel, status: str):
    tag_name = STATUS_TAG_NAMES.get(status)
    if not tag_name:
        return None
    return get_forum_tag(forum_channel, tag_name)


def get_task_group(rows):
    variants = {row[1] for row in rows}
    if "Audio" in variants:
        return "audio"
    if "Character" in variants:
        return "character"
    return "pokemon"


def format_task_status(status: Optional[str]):
    return STATUS_LABELS.get(status or "Available", status or "Missing")


def format_task_message_link(status: Optional[str], feedback_url: Optional[str], completion_url: Optional[str]):
    if status == "Waiting For Feedback" and feedback_url:
        return f" {feedback_url}"
    if status == "Completed" and completion_url:
        return f" {completion_url}"
    return ""


def build_task_forum_summary(thread_name: str, rows):
    if not rows:
        return (
            f"**Task:** {thread_name}\n"
            "**Status:** Missing\n\n"
            "No task rows are currently linked to this forum post."
        )

    group = get_task_group(rows)
    task_by_type = {
        (variant, sprite_type): (status, user_id, min_level, feedback_url, completion_url)
        for (
            _identifier,
            variant,
            sprite_type,
            status,
            user_id,
            min_level,
            _reference_image_url,
            feedback_url,
            completion_url,
        ) in rows
    }
    aggregate_statuses = [row[3] for row in rows]
    reference_image_urls = sorted({row[6] for row in rows if row[6]})

    if any(status == "Waiting For Feedback" for status in aggregate_statuses):
        aggregate_status = "Waiting for Feedback"
    elif any(status == "Assigned" for status in aggregate_statuses):
        aggregate_status = "Assigned"
    elif aggregate_statuses and all(status == "Completed" for status in aggregate_statuses):
        aggregate_status = "Completed"
    else:
        aggregate_status = "Missing"

    lines = []
    for variant, sprite_type in TASK_SUMMARY_ORDER[group]:
        label = f"{variant} {sprite_type}"
        status, user_id, min_level, feedback_url, completion_url = task_by_type.get(
            (variant, sprite_type),
            (None, None, None, None, None),
        )
        status_text = format_task_status(status)
        message_link = format_task_message_link(status, feedback_url, completion_url)
        min_level_text = f" (Lv {min_level}+)" if min_level else ""
        assignee_text = f" - <@{user_id}>" if user_id else ""
        lines.append(f"{label}{min_level_text} - {status_text}{message_link}{assignee_text}")

    return (
        f"**Task:** {thread_name}\n"
        f"**Status:** {aggregate_status}\n\n"
        + (f"**Reference:** {', '.join(reference_image_urls)}\n\n" if reference_image_urls else "")
        +
        "**Task Breakdown**\n"
        + "\n".join(lines)
    )


async def update_task_forum_summary(bot, db_path: str, thread_id: Optional[int]):
    if not thread_id:
        return

    ensure_task_link_columns(db_path)

    thread = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
    if not isinstance(thread, discord.Thread):
        return

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pokedex_identifier, variant, sprite_type, status, user_id, min_level,
                   reference_image_url, feedback_message_url, completion_message_url
            FROM tasks
            WHERE forum_thread_id = ?
            ORDER BY variant, sprite_type
        """, (thread_id,))
        rows = cursor.fetchall()

    content = build_task_forum_summary(thread.name, rows)
    try:
        starter_message = await thread.fetch_message(thread.id)
    except discord.HTTPException:
        return

    await starter_message.edit(content=content[:2000])


async def get_task_forum_channel(bot, variant: str, sprite_type: str):
    forum_id = get_task_forum_id(variant, sprite_type)
    if not forum_id:
        return None

    channel = bot.get_channel(forum_id) or await bot.fetch_channel(forum_id)
    if isinstance(channel, discord.ForumChannel):
        return channel
    return None


async def create_task_forum_post(
    bot,
    variant: str,
    sprite_type: str,
    identifier: str,
    title: Optional[str] = None,
    reference_image_url: Optional[str] = None,
):
    forum_channel = await get_task_forum_channel(bot, variant, sprite_type)
    if not forum_channel:
        return None

    task_title = title or f"{variant} {sprite_type} - {identifier}"
    missing_tag = get_status_tag(forum_channel, "Available")
    applied_tags = [missing_tag] if missing_tag else []
    result = await forum_channel.create_thread(
        name=task_title,
        content=(
            f"**Task:** {task_title}\n"
            "**Status:** Missing\n\n"
            + (f"**Reference:** {reference_image_url}\n\n" if reference_image_url else "")
            +
            "This task is available to claim from the task board."
        ),
        applied_tags=applied_tags,
    )
    return getattr(result, "thread", result)


async def update_task_forum_status(bot, thread_id: Optional[int], status: str, message: Optional[str] = None):
    if not thread_id:
        return

    thread = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
    if not isinstance(thread, discord.Thread):
        return

    parent = thread.parent
    if not isinstance(parent, discord.ForumChannel):
        return

    status_tag = get_status_tag(parent, status)
    if status_tag:
        kept_tags = [
            tag for tag in thread.applied_tags
            if tag.name.casefold() not in {name.casefold() for name in STATUS_TAG_NAMES.values()}
        ]
        await thread.edit(applied_tags=[*kept_tags, status_tag])

    sent_message = None
    if message:
        sent_message = await thread.send(message)

    return sent_message
