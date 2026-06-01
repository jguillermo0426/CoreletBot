import sqlite3
import os

# Ensure the data directory exists
os.makedirs("data", exist_ok=True)

def initialize_db():
    conn = sqlite3.connect("data/corelet.db")
    cursor = conn.cursor()

    # Create Users Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,      -- Discord ID
        discord_name TEXT NOT NULL,
        pronouns TEXT,
        timezone TEXT,
        level INTEGER DEFAULT 1,
        tasks_completed INTEGER DEFAULT 0
    )
    """)

    # Create Tasks Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        task_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,                  -- Foreign key to users
        sprite_type TEXT NOT NULL,        -- Front, Back, Icon, etc.
        variant TEXT NOT NULL,            -- Base, Shiny, Anomaly, etc.
        pokedex_identifier TEXT NOT NULL, -- Name or Dex Number
        status TEXT DEFAULT 'Assigned',   -- Assigned, Waiting For Feedback, Completed
        assigned_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        due_date TIMESTAMP,
        forum_thread_id INTEGER,          -- To track where the task is discussed
        min_level INTEGER,
        reference_image_url TEXT,
        feedback_message_url TEXT,        -- Message linked when waiting for feedback
        completion_message_url TEXT,      -- Message linked when completed
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    """)

    cursor.execute("PRAGMA table_info(tasks)")
    columns = {row[1] for row in cursor.fetchall()}
    if "min_level" not in columns:
        cursor.execute("ALTER TABLE tasks ADD COLUMN min_level INTEGER")
    if "reference_image_url" not in columns:
        cursor.execute("ALTER TABLE tasks ADD COLUMN reference_image_url TEXT")
    if "feedback_message_url" not in columns:
        cursor.execute("ALTER TABLE tasks ADD COLUMN feedback_message_url TEXT")
    if "completion_message_url" not in columns:
        cursor.execute("ALTER TABLE tasks ADD COLUMN completion_message_url TEXT")

    conn.commit()
    conn.close()
    print("Database initialized successfully.")

if __name__ == "__main__":
    initialize_db()
