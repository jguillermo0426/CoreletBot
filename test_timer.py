import sqlite3
import argparse
from datetime import datetime, timedelta, timezone

db_path = "data/corelet.db"


def simulate_days_passed(days_ago):
    now = datetime.now(timezone.utc)
    fake_assigned = now - timedelta(days=days_ago)
    fake_due = fake_assigned + timedelta(days=7)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tasks
            SET assigned_date = ?, due_date = ?
            WHERE status = 'Assigned'
        """, (fake_assigned.isoformat(), fake_due.isoformat()))
        updated_count = cursor.rowcount
        conn.commit()

    print(
        f"Success: Updated {updated_count} assigned task(s) to look like "
        f"they were assigned {days_ago} day(s) ago."
    )


def move_assigned_dates_forward(days):
    delta = timedelta(days=days)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT task_id, assigned_date, due_date
            FROM tasks
            WHERE status = 'Assigned'
              AND assigned_date IS NOT NULL
              AND due_date IS NOT NULL
        """)
        rows = cursor.fetchall()
        updated_count = 0

        for task_id, assigned_date, due_date in rows:
            try:
                new_assigned = datetime.fromisoformat(assigned_date) + delta
                new_due = datetime.fromisoformat(due_date) + delta
            except ValueError:
                print(f"Skipped task {task_id}: invalid date format.")
                continue

            cursor.execute("""
                UPDATE tasks
                SET assigned_date = ?, due_date = ?
                WHERE task_id = ?
            """, (new_assigned.isoformat(), new_due.isoformat(), task_id))
            updated_count += cursor.rowcount

        conn.commit()

    print(f"Success: Moved {updated_count} assigned task(s) forward by {days} day(s).")


def parse_args():
    parser = argparse.ArgumentParser(description="Adjust assigned task dates for timer testing.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--days-ago",
        type=int,
        default=3,
        help="Set assigned tasks to look assigned this many days ago. Default: 3.",
    )
    group.add_argument(
        "--forward",
        type=int,
        help="Move current assigned_date and due_date forward by this many days.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.forward is not None:
        move_assigned_dates_forward(args.forward)
    else:
        simulate_days_passed(days_ago=args.days_ago)
