from fastapi import FastAPI

app = FastAPI(title="event-watcher service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "event-watcher"}
