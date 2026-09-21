"""Single-owner credentials, opaque tokens and sessions, outside portable library data."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from chinalaw.admin.errors import LibraryError

PUBLIC_SCOPE = "chinalaw:public:read"
PRIVATE_SCOPE = "chinalaw:private:read"
READ_SCOPES = frozenset({PUBLIC_SCOPE, PRIVATE_SCOPE})
SESSION_SECONDS = 8 * 3600
PASSWORD_ITERATIONS = 600_000
LOGIN_MAX_FAILURES = 10
LOGIN_BACKOFF_STEP = 0.25
LOGIN_BACKOFF_MAX = 2.0

AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    digest TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    kind TEXT NOT NULL, client_id TEXT NOT NULL, grant_id TEXT NOT NULL,
    scopes_json TEXT NOT NULL, resource TEXT NOT NULL, subject TEXT NOT NULL,
    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, last_used_at INTEGER,
    revoked_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_credentials_grant ON credentials(grant_id);
CREATE TABLE IF NOT EXISTS oauth_clients (
    id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL, created_at INTEGER NOT NULL,
    last_grant_at INTEGER
);
CREATE TABLE IF NOT EXISTS oauth_requests (
    id TEXT PRIMARY KEY, client_id TEXT NOT NULL, params_json TEXT NOT NULL,
    expires_at INTEGER NOT NULL, consumed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS oauth_codes (
    digest TEXT PRIMARY KEY, metadata_json TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
    address TEXT PRIMARY KEY, attempts INTEGER NOT NULL, window_start INTEGER NOT NULL
);
"""


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_scopes(scopes: list[str]) -> list[str]:
    if not scopes or not set(scopes) <= READ_SCOPES or PUBLIC_SCOPE not in scopes:
        raise LibraryError("invalid_scope", "只能授予公开法规和可选的私域只读权限。")
    return sorted(set(scopes))


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str]
    kind: str
    client_id: str = "owner"
    csrf: str | None = None

    @property
    def is_owner(self) -> bool:
        return self.kind == "session" and self.subject == "owner"

    @property
    def can_read_private(self) -> bool:
        return self.is_owner or PRIVATE_SCOPE in self.scopes


class _Transaction:
    """Commit-or-rollback connection scope.

    A class rather than ``@contextmanager`` because the MCP SDK's OAuth errors are
    frozen dataclasses: ``contextlib`` assigns ``__traceback__`` on exceptions that
    pass through a generator-based manager, which those errors reject.
    """

    def __init__(self, path: Path) -> None:
        self.conn = sqlite3.connect(path, timeout=10)
        self.conn.row_factory = sqlite3.Row

    def __enter__(self) -> sqlite3.Connection:
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()


class AuthStore:
    def __init__(self, path: Path | str, resource_url: str):
        self.path = Path(path)
        self.resource_url = resource_url
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.transaction() as conn:
            conn.executescript(AUTH_SCHEMA)
            self._migrate(conn)
        if os.name == "posix":
            self.path.chmod(0o600)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Idempotent column additions for authentication databases created earlier."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(oauth_clients)")}
        if "last_grant_at" not in columns:
            conn.execute("ALTER TABLE oauth_clients ADD COLUMN last_grant_at INTEGER")

    def transaction(self) -> _Transaction:
        return _Transaction(self.path)

    def has_password(self) -> bool:
        with self.transaction() as conn:
            return (
                conn.execute("SELECT 1 FROM settings WHERE key = 'password'").fetchone() is not None
            )

    def set_password(self, password: str) -> None:
        if not 12 <= len(password) <= 1024:
            raise LibraryError("invalid_password", "密码长度应为 12–1,024 个字符。")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
        encoded = f"{PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES ('password', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (encoded,),
            )
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM credentials WHERE kind = 'pairing'")

    def _password_matches(self, password: str) -> bool:
        with self.transaction() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'password'").fetchone()
        if row is None or len(password) > 1024:
            return False
        iterations, salt, expected = row["value"].split("$")
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(actual.hex(), expected)

    def verify_password(self, password: str, address: str = "local") -> None:
        """Rate-limited password check shared by login and password changes."""
        self._check_login_limit(address)
        if not self._password_matches(password):
            attempts = self._record_failure(address)
            # Linear backoff outside any database transaction slows guessing from one
            # address without letting other addresses lock the owner out.
            time.sleep(min(LOGIN_BACKOFF_STEP * attempts, LOGIN_BACKOFF_MAX))
            raise LibraryError("invalid_credentials", "密码不正确。", status=401)
        with self.transaction() as conn:
            conn.execute("DELETE FROM login_attempts WHERE address = ?", (address,))

    def login(self, password: str, address: str = "local") -> tuple[str, str]:
        self.verify_password(password, address)
        return self.create_session()

    def _check_login_limit(self, address: str) -> None:
        now = int(time.time())
        with self.transaction() as conn:
            conn.execute("DELETE FROM login_attempts WHERE window_start < ?", (now - 300,))
            row = conn.execute(
                "SELECT attempts FROM login_attempts WHERE address = ?", (address,)
            ).fetchone()
        if row is not None and row["attempts"] >= LOGIN_MAX_FAILURES:
            raise LibraryError("login_rate_limited", "登录尝试过多，请五分钟后再试。", status=429)

    def _record_failure(self, address: str) -> int:
        with self.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO login_attempts(address, attempts, window_start) VALUES (?, 1, ?) "
                "ON CONFLICT(address) DO UPDATE SET attempts = attempts + 1",
                (address, int(time.time())),
            )
            return conn.execute(
                "SELECT attempts FROM login_attempts WHERE address = ?", (address,)
            ).fetchone()[0]

    def create_session(self) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
            conn.execute(
                "INSERT INTO sessions(digest, csrf, expires_at) VALUES (?, ?, ?)",
                (token_digest(token), csrf, int(time.time()) + SESSION_SECONDS),
            )
        return token, csrf

    def session(self, token: str | None) -> Principal | None:
        if not token or len(token) > 256:
            return None
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT csrf FROM sessions WHERE digest = ? AND expires_at > ?",
                (token_digest(token), int(time.time())),
            ).fetchone()
        if row is None:
            return None
        return Principal("owner", READ_SCOPES, "session", csrf=row["csrf"])

    def logout(self, token: str | None) -> None:
        if token:
            with self.transaction() as conn:
                conn.execute("DELETE FROM sessions WHERE digest = ?", (token_digest(token),))

    def create_pairing(self) -> str:
        token = secrets.token_urlsafe(32)
        with self.transaction() as conn:
            conn.execute("DELETE FROM credentials WHERE kind = 'pairing'")
            self.insert_token(
                conn,
                token,
                name="local pairing",
                kind="pairing",
                client_id="owner",
                grant_id=uuid.uuid4().hex,
                scopes=[],
                expires_at=int(time.time()) + 180,
            )
        return token

    def consume_pairing(self, token: str) -> tuple[str, str]:
        with self.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self.lookup_token(conn, token, kinds=("pairing",))
            if row is None:
                raise LibraryError(
                    "invalid_pairing", "本地配对链接已失效，请重新启动面板。", status=401
                )
            conn.execute("DELETE FROM credentials WHERE id = ?", (row["id"],))
        return self.create_session()

    def insert_token(
        self,
        conn: sqlite3.Connection,
        token: str,
        *,
        name: str,
        kind: str,
        client_id: str,
        grant_id: str,
        scopes: list[str],
        expires_at: int,
    ) -> str:
        identifier = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO credentials (id, digest, name, kind, client_id, grant_id, scopes_json, "
            "resource, subject, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                identifier,
                token_digest(token),
                name,
                kind,
                client_id,
                grant_id,
                json.dumps(scopes),
                self.resource_url,
                "owner",
                int(time.time()),
                expires_at,
            ),
        )
        return identifier

    def issue_query_token(self, name: str, scopes: list[str], *, days: int = 90) -> dict:
        scopes = validate_scopes(scopes)
        if not name.strip() or len(name) > 80 or not 1 <= days <= 365:
            raise LibraryError("invalid_token_settings", "名称为 1–80 字，有效期为 1–365 天。")
        token = "clq_" + secrets.token_urlsafe(32)
        expires = int(time.time()) + days * 86400
        with self.transaction() as conn:
            identifier = self.insert_token(
                conn,
                token,
                name=name.strip(),
                kind="personal",
                client_id="personal",
                grant_id=uuid.uuid4().hex,
                scopes=scopes,
                expires_at=expires,
            )
        return {
            "id": identifier,
            "token": token,
            "name": name.strip(),
            "scopes": scopes,
            "expires_at": expires,
        }

    def lookup_token(
        self, conn: sqlite3.Connection, token: str, *, kinds: tuple[str, ...]
    ) -> sqlite3.Row | None:
        if not token or len(token) > 256:
            return None
        row = conn.execute(
            "SELECT * FROM credentials WHERE digest = ? AND revoked_at IS NULL AND expires_at > ?",
            (token_digest(token), int(time.time())),
        ).fetchone()
        if (
            row is None
            or row["kind"] not in kinds
            or row["resource"] != self.resource_url
            or row["subject"] != "owner"
        ):
            return None
        return row

    def bearer(self, token: str | None) -> Principal | None:
        if not token:
            return None
        with self.transaction() as conn:
            row = self.lookup_token(conn, token, kinds=("personal", "oauth_access"))
            if row is None:
                return None
            scopes = frozenset(json.loads(row["scopes_json"]))
            if PUBLIC_SCOPE not in scopes or not scopes <= READ_SCOPES:
                return None
            conn.execute(
                "UPDATE credentials SET last_used_at = ? WHERE id = ?",
                (int(time.time()), row["id"]),
            )
        return Principal("owner", scopes, "token", client_id=row["client_id"])

    def list_credentials(self) -> list[dict]:
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT id, name, kind, client_id, grant_id, scopes_json, created_at, "
                "expires_at, last_used_at, revoked_at FROM credentials "
                "WHERE kind IN ('personal', 'oauth_access') ORDER BY created_at DESC, id DESC"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["scopes"] = json.loads(item.pop("scopes_json"))
            result.append(item)
        return result

    def revoke_credential(self, identifier: str) -> None:
        with self.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            revoked = conn.execute(
                "UPDATE credentials SET revoked_at = ? WHERE revoked_at IS NULL AND grant_id = "
                "(SELECT grant_id FROM credentials WHERE id = ? AND revoked_at IS NULL)",
                (int(time.time()), identifier),
            ).rowcount
        if revoked == 0:
            raise LibraryError("credential_not_found", "凭据不存在或已撤销。", status=404)
