import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
from typing import Optional
from datetime import datetime, timezone

def format_due_date(due_date_str: str) -> str:
    if not due_date_str:
        return "No due date"
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(due_date_str, fmt)
            return f"<t:{int(dt.timestamp())}:d> (<t:{int(dt.timestamp())}:R>)"
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(due_date_str)
        return f"<t:{int(dt.timestamp())}:d> (<t:{int(dt.timestamp())}:R>)"
    except ValueError:
        return due_date_str


def has_director_role(user: discord.Member) -> bool:
    return any(role.name == "Directors 🌇" for role in getattr(user, "roles", []))

class Profiles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "data/corelet.db"

    def execute_query(self, query, params=()):
        """Helper function to execute database writes safely."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()

    def fetch_query(self, query, params=()):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()
        
    @app_commands.command(name="register", description="Register a Void Dev profile")
    @app_commands.describe(
        member="Optional: the Discord member to register. If omitted, registers yourself.",
        pronouns="The member's preferred pronouns",
        timezone="The member's timezone (e.g., EST, UTC+2)"
    )
    async def register(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
        pronouns: Optional[str] = None,
        timezone: Optional[str] = None,
    ):
        if member is None:
            member = interaction.user
        elif member.id != interaction.user.id and not has_director_role(interaction.user):
            await interaction.response.send_message(
                "❌ Only Directors can register other members. You can register yourself without the `member` option.",
                ephemeral=True
            )
            return

        pronouns = pronouns.strip() if pronouns else ""
        timezone = timezone.strip() if timezone else ""

        if not pronouns or not timezone:
            await interaction.response.send_message(
                "❌ Please include both pronouns and timezone.",
                ephemeral=True
            )
            return

        user_id = member.id
        discord_name = member.display_name

        try:
            # Insert or update if they already exist.
            self.execute_query("""
                INSERT INTO users (user_id, discord_name, pronouns, timezone) 
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET 
                discord_name=excluded.discord_name, 
                pronouns=excluded.pronouns, 
                timezone=excluded.timezone
            """, (user_id, discord_name, pronouns, timezone))

            await interaction.response.send_message(
                f"✅ Profile registered for {member.mention}. \n"
                f"**Pronouns:** {pronouns} | **Timezone:** {timezone}",
                ephemeral=True # Only the user sees this message
            )
            
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

    @register.error
    async def register_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        raise error

    @app_commands.command(name="profile", description="View a member's profile and active tasks")
    @app_commands.describe(
        member="Optional: The member whose profile you want to view. If omitted, views your own."
    )
    async def profile(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None
    ):
        if member is None:
            member = interaction.user

        # Fetch profile info
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT discord_name, pronouns, timezone, level, tasks_completed
                FROM users WHERE user_id = ?
            """, (member.id,))
            user_data = cursor.fetchone()

            # Fetch active tasks
            cursor.execute("""
                SELECT task_id, variant, sprite_type, pokedex_identifier, status, due_date, forum_thread_id
                FROM tasks
                WHERE user_id = ? AND status != 'Completed'
                ORDER BY due_date ASC, variant, sprite_type
            """, (member.id,))
            active_tasks = cursor.fetchall()

        # If not registered and has no active tasks
        if not user_data and not active_tasks:
            if member.id == interaction.user.id:
                await interaction.response.send_message(
                    "❌ You don't have a registered profile yet. Use `/register` to create one!",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"❌ {member.display_name} does not have a registered profile or any active tasks.",
                    ephemeral=True
                )
            return

        # Prepare user info (with defaults if unregistered but has tasks)
        if user_data:
            _, pronouns, timezone, level, tasks_completed = user_data
        else:
            pronouns, timezone, level, tasks_completed = None, None, 1, 0

        # Construct embed
        embed = discord.Embed(
            title=f"Void Dev Profile — {member.display_name}",
            color=discord.Color.purple()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        
        # User details fields
        embed.add_field(name="Pronouns", value=pronouns or "*Not set*", inline=True)
        embed.add_field(name="Timezone", value=timezone or "*Not set*", inline=True)
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="Tasks Completed", value=str(tasks_completed), inline=True)

        # Build active tasks text
        if active_tasks:
            task_lines = []
            for tid, var, stype, name, status, due_date, thread_id in active_tasks:
                due_str = format_due_date(due_date)
                thread_mention = f" | <#{thread_id}>" if thread_id else ""
                task_lines.append(
                    f"• **#{tid}**: {var} {stype} — {name}\n"
                    f"  Status: `{status}` | Due: {due_str}{thread_mention}"
                )
            embed.add_field(name="Active Tasks", value="\n".join(task_lines), inline=False)
        else:
            embed.add_field(name="Active Tasks", value="*No active tasks.*", inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="updatelevel", description="Update a member's Void Dev level")
    @app_commands.describe(
        user="The Discord member whose level you want to update",
        level="The new level to set (e.g., 2, 3)"
    )
    async def updatelevel(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        level: int
    ):
        if not has_director_role(interaction.user):
            await interaction.response.send_message(
                "❌ Only Directors can update member levels.",
                ephemeral=True
            )
            return

        if level < 1:
            await interaction.response.send_message(
                "❌ Level must be a positive integer greater than or equal to 1.",
                ephemeral=True
            )
            return

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT level FROM users WHERE user_id = ?", (user.id,))
                row = cursor.fetchone()
                
                if not row:
                    # User not in database; insert them with the new level
                    cursor.execute("""
                        INSERT INTO users (user_id, discord_name, level)
                        VALUES (?, ?, ?)
                    """, (user.id, user.display_name, level))
                else:
                    cursor.execute("""
                        UPDATE users
                        SET level = ?
                        WHERE user_id = ?
                    """, (level, user.id))
                
                conn.commit()

            await interaction.response.send_message(
                f"✅ Successfully updated {user.mention}'s level to **{level}**."
            )

        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

    @app_commands.command(name="whosworking", description="View a summary of all members who are currently working on tasks")
    async def whosworking(self, interaction: discord.Interaction):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT t.user_id, u.discord_name, u.timezone, u.pronouns,
                           COUNT(CASE WHEN t.status = 'Assigned' THEN 1 END) as assigned_count,
                           COUNT(CASE WHEN t.status = 'Waiting For Feedback' THEN 1 END) as feedback_count,
                           COUNT(t.task_id) as total_count
                    FROM tasks t
                    LEFT JOIN users u ON t.user_id = u.user_id
                    WHERE t.status != 'Completed' AND t.user_id IS NOT NULL
                    GROUP BY t.user_id, u.discord_name, u.timezone, u.pronouns
                """)
                workers_data = cursor.fetchall()

            if not workers_data:
                await interaction.response.send_message("❌ No members are currently working on any tasks.", ephemeral=True)
                return

            resolved_workers_data = []
            for row in workers_data:
                user_id, discord_name, timezone, pronouns, assigned_count, feedback_count, total_count = row
                member = interaction.guild.get_member(user_id) if interaction.guild else None
                resolved_name = member.display_name if member else (discord_name or f"User {user_id}")
                resolved_workers_data.append((user_id, resolved_name, timezone, pronouns, assigned_count, feedback_count, total_count))

            # Sort resolved data by total tasks desc, then case-insensitive name asc
            resolved_workers_data.sort(key=lambda x: (-x[6], x[1].casefold()))

            view = WhosWorkingView(interaction.user.id, resolved_workers_data)
            await interaction.response.send_message(embed=view.build_embed(), view=view)
        except Exception as e:
            await interaction.response.send_message(f"❌ An error occurred: {e}", ephemeral=True)

    @app_commands.command(name="workingonwhat", description="View all members and details of what they are working on")
    async def workingonwhat(self, interaction: discord.Interaction):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT t.user_id, u.discord_name, u.timezone, u.pronouns, 
                           t.task_id, t.variant, t.sprite_type, t.pokedex_identifier, t.status, t.due_date, t.forum_thread_id
                    FROM tasks t
                    LEFT JOIN users u ON t.user_id = u.user_id
                    WHERE t.status != 'Completed' AND t.user_id IS NOT NULL
                """)
                rows = cursor.fetchall()

            if not rows:
                await interaction.response.send_message("❌ No members are currently working on any tasks.", ephemeral=True)
                return

            user_tasks = {}
            for row in rows:
                user_id, name, timezone, pronouns, task_id, variant, sprite_type, identifier, status, due_date, thread_id = row
                if user_id not in user_tasks:
                    member = interaction.guild.get_member(user_id) if interaction.guild else None
                    resolved_name = member.display_name if member else (name or f"User {user_id}")
                    user_tasks[user_id] = {
                        "user_id": user_id,
                        "name": resolved_name,
                        "timezone": timezone,
                        "pronouns": pronouns,
                        "tasks": []
                    }
                user_tasks[user_id]["tasks"].append({
                    "id": task_id,
                    "variant": variant,
                    "sprite_type": sprite_type,
                    "identifier": identifier,
                    "status": status,
                    "due_date": due_date,
                    "thread_id": thread_id
                })

            user_tasks_list = sorted(user_tasks.values(), key=lambda x: x["name"].casefold())

            view = WorkingOnWhatView(interaction.user.id, user_tasks_list)
            await interaction.response.send_message(embed=view.build_embed(), view=view)
        except Exception as e:
            await interaction.response.send_message(f"❌ An error occurred: {e}", ephemeral=True)


class WhosWorkingView(discord.ui.View):
    def __init__(self, requester_id: int, workers_data, page_size: int = 10):
        super().__init__(timeout=180)
        self.requester_id = requester_id
        self.workers_data = workers_data
        self.page_size = page_size
        self.page = 0
        self.update_buttons()

    @property
    def page_count(self) -> int:
        return max(1, (len(self.workers_data) + self.page_size - 1) // self.page_size)

    def update_buttons(self):
        self.previous_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.page_count - 1

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Void Devs — Active Workers Summary",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )

        if not self.workers_data:
            embed.description = "❌ No members are currently working on any tasks."
            embed.set_footer(text="Page 1 of 1 • 0 active workers")
            return embed

        embed.description = "Summary of members currently assigned to active tasks."

        start = self.page * self.page_size
        end = start + self.page_size
        
        for idx, row in enumerate(self.workers_data[start:end], start=start + 1):
            user_id, resolved_name, timezone, pronouns, assigned_count, feedback_count, total_count = row
            
            # Format timezone/pronouns if available
            meta_parts = []
            if pronouns:
                meta_parts.append(f"Pronouns: **{pronouns}**")
            if timezone:
                meta_parts.append(f"Timezone: **{timezone}**")
            meta_str = " | ".join(meta_parts)
            meta_line = f"\n*({meta_str})*" if meta_str else ""

            # Format status breakdown
            status_parts = []
            if assigned_count > 0:
                status_parts.append(f"🛠️ {assigned_count} Assigned")
            if feedback_count > 0:
                status_parts.append(f"⏳ {feedback_count} Waiting Feedback")
            
            other_count = total_count - assigned_count - feedback_count
            if other_count > 0:
                status_parts.append(f"📌 {other_count} Other")

            status_str = " | ".join(status_parts)

            embed.add_field(
                name=f"{idx}. {resolved_name} (<@{user_id}>)",
                value=f"💼 Active Tasks: **{total_count}** ({status_str}){meta_line}",
                inline=False
            )

        embed.set_footer(text=f"Page {self.page + 1} of {self.page_count} • {len(self.workers_data)} active workers")
        return embed

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Only the person who ran the command can use these controls.", ephemeral=True)
            return
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Only the person who ran the command can use these controls.", ephemeral=True)
            return
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class WorkingOnWhatView(discord.ui.View):
    def __init__(self, requester_id: int, user_tasks_list, page_size: int = 3):
        super().__init__(timeout=180)
        self.requester_id = requester_id
        self.user_tasks_list = user_tasks_list
        self.page_size = page_size
        self.page = 0
        self.update_buttons()

    @property
    def page_count(self) -> int:
        return max(1, (len(self.user_tasks_list) + self.page_size - 1) // self.page_size)

    def update_buttons(self):
        self.previous_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.page_count - 1

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Void Devs — Who is Working on What",
            color=discord.Color.purple(),
            timestamp=datetime.now(timezone.utc)
        )

        if not self.user_tasks_list:
            embed.description = "❌ No members are currently working on any tasks."
            embed.set_footer(text="Page 1 of 1 • 0 active workers")
            return embed

        embed.description = "Detailed list of tasks currently assigned to members."

        start = self.page * self.page_size
        end = start + self.page_size

        for user_data in self.user_tasks_list[start:end]:
            user_id = user_data["user_id"]
            resolved_name = user_data["name"]
            pronouns = user_data["pronouns"]
            timezone = user_data["timezone"]
            tasks = user_data["tasks"]

            meta_parts = []
            if pronouns:
                meta_parts.append(f"Pronouns: **{pronouns}**")
            if timezone:
                meta_parts.append(f"Timezone: **{timezone}**")
            meta_str = " | ".join(meta_parts)
            meta_line = f"ℹ️ *({meta_str})*\n" if meta_str else ""

            task_lines = []
            for t in tasks:
                due_str = format_due_date(t["due_date"])
                thread_mention = f" | <#{t['thread_id']}>" if t["thread_id"] else ""
                
                status_icon = "📌"
                if t["status"] == "Waiting For Feedback":
                    status_icon = "⏳"
                elif t["status"] == "Assigned":
                    status_icon = "🛠️"

                task_lines.append(
                    f"{status_icon} **#{t['id']}**: {t['variant']} {t['sprite_type']} — {t['identifier']}\n"
                    f"  Status: `{t['status']}` | Due: {due_str}{thread_mention}"
                )

            embed.add_field(
                name=f"👤 {resolved_name} (<@{user_id}>)",
                value=f"{meta_line}" + ("\n".join(task_lines) or "*No active tasks.*"),
                inline=False
            )

        embed.set_footer(text=f"Page {self.page + 1} of {self.page_count} • {len(self.user_tasks_list)} working members")
        return embed

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Only the person who ran the command can use these controls.", ephemeral=True)
            return
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Only the person who ran the command can use these controls.", ephemeral=True)
            return
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


async def setup(bot):
    await bot.add_cog(Profiles(bot))
