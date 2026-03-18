from fastapi import FastAPI

app = FastAPI(title="compliance-service service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "compliance-service"}
