"""Seed script for compliance-service."""

from datetime import datetime, timezone


def seed() -> None:
    print(f"[compliance-service] seeding placeholder data at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    seed()
