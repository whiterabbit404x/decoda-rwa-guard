from fastapi import FastAPI

app = FastAPI(title="reconciliation-service service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "reconciliation-service"}
