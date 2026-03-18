from fastapi import FastAPI

app = FastAPI(title="oracle-service service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "oracle-service"}
