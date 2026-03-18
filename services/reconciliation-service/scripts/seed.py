"""Seed script for reconciliation-service."""

from datetime import datetime, timezone


def seed() -> None:
    print(f"[reconciliation-service] seeding placeholder data at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    seed()
