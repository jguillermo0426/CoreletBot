import sqlite3
from datetime import datetime, timedelta, timezone

db_path = "data/corelet.db"

def simulate_days_passed(days_ago):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Calculate simulated times
    now = datetime.now(timezone.utc)
    fake_assigned = now - timedelta(days=days_ago)
    fake_due = fake_assigned + timedelta(days=7)

    # Update all tasks to look like they happened 'days_ago'
    cursor.execute("""
        UPDATE tasks 
        SET assigned_date = ?, due_date = ? 
        WHERE status = 'Assigned'
    """, (fake_assigned.isoformat(), fake_due.isoformat()))

    conn.commit()
    conn.close()
    print(f"Success: Updated all active tasks to look like they were assigned {days_ago} days ago!")

if __name__ == "__main__":
    # Change this number to 3 or 7 depending on which reminder you want to test!
    simulate_days_passed(days_ago=3)