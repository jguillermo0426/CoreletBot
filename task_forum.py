import os
from typing import Optional

import discord


STATUS_TAG_NAMES = {
    "Available": "Missing",
    "Unassigned": "Missing",
    "Assigned": "Assigned",
    "Waiting For Feedback": "Waiting for Feedback",
    "Completed": "Complete",
}


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


async def get_task_forum_channel(bot, variant: str, sprite_type: str):
    forum_id = get_task_forum_id(variant, sprite_type)
    if not forum_id:
        return None

    channel = bot.get_channel(forum_id) or await bot.fetch_channel(forum_id)
    if isinstance(channel, discord.ForumChannel):
        return channel
    return None


async def create_task_forum_post(bot, variant: str, sprite_type: str, identifier: str, title: Optional[str] = None):
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

    if message:
        await thread.send(message)
