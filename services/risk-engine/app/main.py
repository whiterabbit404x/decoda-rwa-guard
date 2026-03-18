from fastapi import FastAPI

app = FastAPI(title="risk-engine service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "risk-engine"}
