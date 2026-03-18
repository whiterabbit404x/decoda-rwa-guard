"""Seed script for event-watcher."""

from datetime import datetime, timezone


def seed() -> None:
    print(f"[event-watcher] seeding placeholder data at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    seed()
