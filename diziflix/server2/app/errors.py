"""Uniform error envelope: {"error": {"code": ..., "message": ...}}."""
from __future__ import annotations

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(status_code=status, detail={"code": code, "message": message})


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def bad_request(message: str) -> ApiError:
    return ApiError(400, "bad_request", message)
