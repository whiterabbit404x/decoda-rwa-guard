"""Seed script for api."""

from datetime import datetime, timezone


def seed() -> None:
    print(f"[api] seeding placeholder data at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    seed()
