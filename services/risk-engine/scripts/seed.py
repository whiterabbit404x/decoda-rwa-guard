"""Seed script for risk-engine."""

from datetime import datetime, timezone


def seed() -> None:
    print(f"[risk-engine] seeding placeholder data at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    seed()
