"""Seed script for event-watcher."""

from phase1_local.dev_support import load_env_file, pretty_json, seed_service

load_env_file()

SERVICE_NAME = 'event-watcher'
PORT = 8005
DETAIL = 'Event ingestion worker capturing local sample events without Redis.'
DEFAULT_METRICS = [{'metric_key': 'event_watch', 'label': 'Event Watcher', 'value': 'Event polling uses in-process scheduling for local development.', 'status': 'Active'}]


def seed() -> None:
    state = seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)
    print(pretty_json(state))


if __name__ == '__main__':
    seed()
