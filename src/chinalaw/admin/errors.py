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


def private_access_denied(*, search: bool = False) -> LibraryError:
    """Explain the catalog vocabulary without granting or probing private access."""
    guidance = "检索公开法规、司法解释等请用 kind=law。"
    public_kinds = ["law"]
    if search:
        guidance = (
            "检索公开法规、司法解释等请用 kind=law（标题）或 kind=article（条文），"
            "也可用 kind=all 检索当前获准访问的资料。"
        )
        public_kinds = ["law", "article", "all"]
    return LibraryError(
        "private_access_denied",
        "此凭据未获私域规范访问权限。kind=norm 指私人导入的资料；" + guidance,
        status=403,
        details={"requested_kind": "norm", "public_kinds": public_kinds},
    )


def require_kind(kind: str) -> str:
    if kind not in {"law", "norm"}:
        raise LibraryError("invalid_kind", "资料类型必须是 law 或 norm。")
    return kind
