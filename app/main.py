from uuid import uuid4
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.api.tasks import router
from app.db.postgres import SessionLocal

app = FastAPI(title="RoomScout", version="0.2.0", description="Durable agent loop and geometry diagnosis with configurable LLM repair")
app.include_router(router)
from app.api.geometry import router as geometry_router
app.include_router(geometry_router)


def error_response(status, code, details=None):
    return JSONResponse(status_code=status, content={"schema_version": 1, "error": {
        "code": code, "message": code, "retryable": status == 503,
        "correlation_id": str(uuid4()), "details": details or {}}})


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return error_response(exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return error_response(422, "invalid_request")


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError):
    return error_response(503, "database_unavailable")


@app.get("/health")
def health():
    with SessionLocal() as session:
        session.execute(text("SELECT 1"))
    return {"status": "ok", "mode": "fake-v0"}
