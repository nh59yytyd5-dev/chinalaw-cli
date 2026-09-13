"""Explicit deployment settings. Request parameters never select filesystem paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ServerConfig:
    db_path: Path
    state_dir: Path
    public_url: str = "http://127.0.0.1:8765"
    host: str = "127.0.0.1"
    port: int = 8765
    local_mode: bool = True
    start_worker: bool = True

    def __post_init__(self) -> None:
        if self.db_path.resolve() == self.auth_path.resolve():
            raise ValueError("the library and authentication database must use different files")
        parsed = urlsplit(self.public_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("public_url must be an http(s) origin without credentials or a path")
        loopback = {"localhost", "127.0.0.1", "::1"}
        if self.local_mode and (self.host not in loopback or parsed.hostname not in loopback):
            raise ValueError("local mode must bind and advertise a loopback address")
        if not self.local_mode and parsed.scheme != "https":
            raise ValueError(
                "server mode requires an HTTPS public_url (TLS may terminate at a proxy)"
            )
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")

    @property
    def origin(self) -> str:
        return self.public_url.rstrip("/")

    @property
    def resource_url(self) -> str:
        return self.origin + "/mcp"

    @property
    def cookie_secure(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def auth_path(self) -> Path:
        return self.state_dir / "auth.db"

    @property
    def artifacts_dir(self) -> Path:
        return self.db_path.with_name(self.db_path.name + ".assets")

    @property
    def restores_dir(self) -> Path:
        return self.state_dir / "restores"

    @property
    def allowed_hosts(self) -> list[str]:
        return [urlsplit(self.origin).netloc]
