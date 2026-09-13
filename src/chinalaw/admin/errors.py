"""Transport-independent errors for library management."""

from __future__ import annotations


class LibraryError(ValueError):
    """A user-actionable error with a stable code and optional details."""

    def __init__(
        self, code: str, message: str, *, status: int = 400, details: dict | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or {}

    def payload(self) -> dict:
        return {
            "kind": "library_error",
            "error": self.code,
            "message": str(self),
            "details": self.details,
        }


def require_kind(kind: str) -> str:
    if kind not in {"law", "norm"}:
        raise LibraryError("invalid_kind", "资料类型必须是 law 或 norm。")
    return kind
