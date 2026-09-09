"""FastAPI entry point for the in-memory Geometry Repair service."""

from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.geometry import router as geometry_router


app = FastAPI(
    title="RoomScout Geometry Repair",
    version="1.0.0",
    description="Geometry Critic with explicit ReAct tool routing and deterministic repairs.",
)
app.include_router(geometry_router)


def error_response(status, code, details=None):
    return JSONResponse(
        status_code=status,
        content={
            "schema_version": 1,
            "error": {
                "code": code,
                "message": code,
                "retryable": status == 503,
                "correlation_id": str(uuid4()),
                "details": details or {},
            },
        },
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return error_response(exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return error_response(422, "invalid_request")


@app.get("/health")
def health():
    return {"status": "ok", "mode": "geometry-react-memory"}
