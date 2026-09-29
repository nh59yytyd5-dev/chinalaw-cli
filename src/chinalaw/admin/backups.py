"""Portable, checked library backups and transactional in-place restoration.

Restoring rows inside the existing SQLite file avoids replacing an inode that
CLI readers or WAL writers may still have open. The final write lock, conflict
check, data replacement and index rebuild share one rollback-capable transaction.
Authentication state is deliberately outside this format.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from chinalaw import __version__, service
from chinalaw.admin import artifacts
from chinalaw.admin.errors import LibraryError
from chinalaw.admin.payloads import encode_json, utc_now
from chinalaw.db import (
    build_current_schema,
    connect,
    connect_readonly,
    current_version,
    get_meta,
    set_meta,
)
from chinalaw.schema import SCHEMA_VERSION
from chinalaw.search_indexes import rebuild_search_indexes

FORMAT_VERSION = 1
MAX_BACKUP_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILES = 20_000
_IDENTIFIER = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FTS = ("laws_fts", "articles_fts", "norm_sources_fts", "norm_clauses_fts")
_DERIVED = {"law_alias_index", *(name + "_rows" for name in _FTS)}
# Bookkeeping tables that legitimately change between a restore preview and its
# confirmation (worker progress, draft expiry, meta timestamps). They are still
# backed up and replaced, but never decide whether the library "changed".
_VOLATILE = frozenset({"library_jobs", "library_drafts", "meta"})
# Staging folders without a preview are uploads still being checked; only sweep
# them once they are clearly abandoned.
_PENDING_GRACE = timedelta(hours=1)


@lru_cache(maxsize=1)
def _schema_spec() -> tuple[dict[str, tuple[str, ...]], frozenset[str]]:
    with closing(sqlite3.connect(":memory:")) as conn:
        build_current_schema(conn)
        tables = frozenset(_user_tables(conn))
        base = {
            name: tuple(row[1] for row in conn.execute(f'PRAGMA table_info("{name}")'))
            for name in sorted(tables)
            if not name.startswith("sqlite_")
            and name not in _DERIVED
            and not any(name == fts or name.startswith(fts + "_") for fts in _FTS)
        }
    return base, tables


def _user_tables(conn: sqlite3.Connection) -> set[str]:
    """Table names excluding SQLite internals such as ANALYZE statistics."""
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        if not row[0].startswith("sqlite_")
    }


def _counts(path: Path | str) -> dict:
    result = service.status(path)
    return {
        key: result[key]
        for key in ("laws", "articles", "revisions", "norm_sources", "norm_clauses", "norm_packs")
    }


def _state_fingerprint(conn: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for table, columns in _schema_spec()[0].items():
        if table in _VOLATILE:
            continue
        digest.update(table.encode())
        fields = ", ".join(f'"{column}"' for column in columns)
        for row in conn.execute(f'SELECT {fields} FROM "{table}" ORDER BY rowid'):
            digest.update(encode_json(list(row)).encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def library_fingerprint(db_path: Path | str) -> str:
    with connect_readonly(db_path) as conn:
        conn.execute("BEGIN")
        return _state_fingerprint(conn)


def create_backup(db_path: Path | str, root: Path | str, output: Path | str) -> dict:
    """Snapshot SQLite first, then package exactly that snapshot's source files."""
    output = Path(output)
    if output.exists():
        raise LibraryError("backup_exists", "备份目标已存在，请选择新文件。", status=409)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="chinalaw-backup-", dir=output.parent) as folder:
        temporary = Path(folder)
        database = temporary / "library.sqlite3"
        with connect_readonly(db_path) as source, closing(sqlite3.connect(database)) as target:
            source.backup(target)
        _validate_database(database)
        files = [
            {
                "path": "library.sqlite3",
                "size": database.stat().st_size,
                "sha256": artifacts.file_digest(database),
            }
        ]
        blobs: dict[str, Path] = {"library.sqlite3": database}
        for entry in artifacts.artifact_manifest(database):
            _, path = artifacts.get_artifact(database, root, entry["id"])
            name = f"blobs/{entry['sha256'][:2]}/{entry['sha256']}"
            if name not in blobs:
                files.append({"path": name, "size": entry["size"], "sha256": entry["sha256"]})
                blobs[name] = path
        if len(files) + 1 > MAX_FILES or sum(item["size"] for item in files) > MAX_EXPANDED_BYTES:
            raise LibraryError("backup_too_large", "资料库超过此版本的备份大小上限。", status=413)
        with connect_readonly(database) as conn:
            identifier = get_meta(conn, "library_id")
        manifest = {
            "format": "chinalaw-library",
            "format_version": FORMAT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "app_version": __version__,
            "library_id": identifier,
            "created_at": utc_now(),
            "counts": _counts(database),
            "files": files,
        }
        archive = temporary / "backup.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("manifest.json", encode_json(manifest))
            for name, path in blobs.items():
                bundle.write(path, name)
        if archive.stat().st_size > MAX_BACKUP_BYTES:
            raise LibraryError("backup_too_large", "备份文件超过 512 MiB。", status=413)
        os.replace(archive, output)
    return manifest


def _validate_database(path: Path) -> None:
    with connect_readonly(path) as conn:
        conn.execute("PRAGMA trusted_schema = OFF")
        if current_version(conn) != SCHEMA_VERSION:
            raise LibraryError("backup_schema", "备份的数据结构版本与当前服务不兼容。")
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise LibraryError("backup_corrupted", "备份数据库完整性校验失败。")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise LibraryError("backup_corrupted", "备份数据库存在缺失的关联记录。")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger', 'view')").fetchone():
            raise LibraryError("backup_schema", "备份包含不支持的视图或触发器。")
        base, expected = _schema_spec()
        if _user_tables(conn) != expected:
            raise LibraryError("backup_schema", "备份的资料表与当前版本不匹配。")
        for table, columns in base.items():
            actual = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
            if actual != set(columns):
                raise LibraryError("backup_schema", "备份的资料字段与当前版本不匹配。")


def _checked_entries(bundle: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    entries = bundle.infolist()
    names = [entry.filename for entry in entries]
    if not entries or len(entries) > MAX_FILES or len(names) != len(set(names)):
        raise LibraryError("backup_files", "备份的文件清单重复或超过数量限制。")
    if sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
        raise LibraryError("backup_too_large", "备份解压后超过 2 GiB。", status=413)
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in entry.filename
            or str(path) != entry.filename
            or entry.is_dir()
            or stat.S_ISLNK(entry.external_attr >> 16)
            or entry.flag_bits & 1
        ):
            raise LibraryError("backup_path", "备份包含不安全的路径或文件类型。")
    return dict(zip(names, entries, strict=True))


def _extract_checked(archive: Path, destination: Path) -> dict:
    with zipfile.ZipFile(archive) as bundle:
        entries = _checked_entries(bundle)
        if "manifest.json" not in entries or entries["manifest.json"].file_size > 8 * 1024 * 1024:
            raise LibraryError("backup_manifest", "缺少有效的备份清单。")
        manifest = json.loads(bundle.read("manifest.json"))
        if not isinstance(manifest, dict) or (
            manifest.get("format") != "chinalaw-library"
            or manifest.get("format_version") != FORMAT_VERSION
            or manifest.get("schema_version") != SCHEMA_VERSION
        ):
            raise LibraryError("backup_schema", "备份格式或数据结构版本不兼容。")
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            raise LibraryError("backup_manifest", "备份清单中没有资料文件。")
        listed: set[str] = set()
        for item in files:
            if not isinstance(item, dict):
                raise LibraryError("backup_manifest", "备份清单格式不正确。")
            name, digest, size = item.get("path"), item.get("sha256"), item.get("size")
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                raise LibraryError("backup_manifest", "备份清单中的校验值不正确。")
            if name not in {"library.sqlite3", f"blobs/{digest[:2]}/{digest}"}:
                raise LibraryError("backup_path", "备份包含不支持的文件路径。")
            if name in listed or name not in entries or size != entries[name].file_size:
                raise LibraryError("backup_manifest", "备份清单与文件不一致。")
            listed.add(name)
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(name) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            if artifacts.file_digest(target) != digest:
                raise LibraryError("backup_corrupted", "备份文件校验失败。")
        if listed | {"manifest.json"} != set(entries) or "library.sqlite3" not in listed:
            raise LibraryError("backup_manifest", "备份中存在未列明或缺失的文件。")
    _validate_database(destination / "library.sqlite3")
    for entry in artifacts.artifact_manifest(destination / "library.sqlite3"):
        artifacts.get_artifact(destination / "library.sqlite3", destination / "blobs", entry["id"])
    return manifest


def _preview_expired(preview: dict) -> bool:
    try:
        return datetime.fromisoformat(preview["expires_at"]) <= datetime.now(timezone.utc)
    except (KeyError, TypeError, ValueError):
        return True


def sweep_restores(staging: Path | str) -> int:
    """Delete expired previews and abandoned uploads; return how many were removed."""
    staging = Path(staging)
    if not staging.is_dir():
        return 0
    removed = 0
    now = datetime.now(timezone.utc)
    for folder in staging.iterdir():
        if not folder.is_dir() or not _IDENTIFIER.fullmatch(folder.name):
            continue
        preview_path = folder / "preview.json"
        try:
            if preview_path.is_file():
                stale = _preview_expired(json.loads(preview_path.read_text(encoding="utf-8")))
            else:
                candidates = [folder, folder / "backup.zip"]
                touched = max(
                    datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                    for path in candidates
                    if path.exists()
                )
                stale = now - touched > _PENDING_GRACE
        except (OSError, ValueError):
            stale = True
        if stale:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return removed


def prepare_restore(db_path: Path | str, staging: Path | str, stream: BinaryIO) -> dict:
    """Validate an uploaded archive in isolation; keep the current library unchanged."""
    sweep_restores(staging)
    identifier = uuid.uuid4().hex
    folder = Path(staging) / identifier
    folder.mkdir(parents=True, mode=0o700)
    try:
        archive = folder / "backup.zip"
        with archive.open("xb") as output:
            size = 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                if size > MAX_BACKUP_BYTES:
                    raise LibraryError("backup_too_large", "备份文件最多 512 MiB。", status=413)
                output.write(chunk)
        manifest = _extract_checked(archive, folder / "verified")
        result = {
            "kind": "library_restore_preview",
            "id": identifier,
            "fingerprint": artifacts.file_digest(archive),
            "base_fingerprint": library_fingerprint(db_path),
            "current": _counts(db_path),
            "incoming": _counts(folder / "verified/library.sqlite3"),
            "artifact_count": len(artifacts.artifact_manifest(folder / "verified/library.sqlite3")),
            "manifest": manifest,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        }
        (folder / "preview.json").write_text(encode_json(result), encoding="utf-8")
        return result
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        if isinstance(exc, LibraryError):
            raise
        raise LibraryError(
            "backup_invalid", "备份无法读取或校验失败，请选择完整的资料库备份。"
        ) from exc


def get_restore(staging: Path | str, identifier: str) -> dict:
    if not _IDENTIFIER.fullmatch(identifier):
        raise LibraryError("restore_not_found", "恢复预览不存在。", status=404)
    path = Path(staging) / identifier / "preview.json"
    if not path.is_file():
        raise LibraryError("restore_not_found", "恢复预览不存在。", status=404)
    preview = json.loads(path.read_text(encoding="utf-8"))
    if _preview_expired(preview):
        raise LibraryError("restore_expired", "恢复预览已过期，请重新上传备份。", status=410)
    return preview


def _copy_blobs(database: Path, source_root: Path, target_root: Path) -> None:
    for entry in artifacts.artifact_manifest(database):
        _, source = artifacts.get_artifact(database, source_root, entry["id"])
        target = artifacts.blob_path(target_root, entry["sha256"])
        if target.is_file() and artifacts.file_digest(target) == entry["sha256"]:
            continue
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
                with source.open("rb") as stream:
                    shutil.copyfileobj(stream, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _replace_rows(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    for table in _schema_spec()[0]:
        target.execute(f'DELETE FROM "{table}"')
    for table, columns in _schema_spec()[0].items():
        fields = ", ".join(f'"{column}"' for column in columns)
        rows = source.execute(f'SELECT {fields} FROM "{table}" ORDER BY rowid')
        target.executemany(
            f'INSERT INTO "{table}" ({fields}) VALUES ({", ".join("?" for _ in columns)})', rows
        )
    rebuild_search_indexes(target)
    if target.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise LibraryError("backup_corrupted", "恢复后的关联校验失败，已撤销恢复。")


def commit_restore(
    db_path: Path | str,
    root: Path | str,
    staging: Path | str,
    identifier: str,
    *,
    expected_fingerprint: str,
) -> dict:
    if not _IDENTIFIER.fullmatch(identifier):
        raise LibraryError("restore_not_found", "恢复预览不存在。", status=404)
    folder = Path(staging) / identifier
    repeated = {"kind": "library_restored", "id": identifier, "repeated": True}
    # A confirmed restore is idempotent even after its staging folder is gone.
    with connect_readonly(db_path) as conn:
        if get_meta(conn, "last_restore_id") == identifier:
            shutil.rmtree(folder, ignore_errors=True)
            return repeated
    preview = get_restore(staging, identifier)
    if preview["fingerprint"] != expected_fingerprint:
        raise LibraryError("restore_changed", "确认的备份与预览不一致。", status=409)
    if artifacts.file_digest(folder / "backup.zip") != expected_fingerprint:
        raise LibraryError("backup_corrupted", "待恢复备份发生变化，请重新上传。", status=409)
    # Re-extract to a fresh location so staged DB/files cannot change after review.
    with tempfile.TemporaryDirectory(prefix="restore-", dir=folder) as fresh:
        verified = Path(fresh)
        _extract_checked(folder / "backup.zip", verified)
        database = verified / "library.sqlite3"
        _copy_blobs(database, verified / "blobs", Path(root))
        with connect_readonly(database) as source, connect(db_path) as target:
            target.execute("PRAGMA foreign_keys = OFF")
            target.execute("BEGIN IMMEDIATE")
            if get_meta(target, "last_restore_id") == identifier:
                target.execute("ROLLBACK")
                shutil.rmtree(folder, ignore_errors=True)
                return repeated
            if _state_fingerprint(target) != preview["base_fingerprint"]:
                raise LibraryError(
                    "content_conflict",
                    "资料库在预览后已变化，请重新上传并核对恢复范围。",
                    status=409,
                )
            _replace_rows(source, target)
            target.execute(
                "UPDATE library_jobs SET state = 'interrupted', "
                "message = '从备份恢复，请手动重试', finished_at = ? "
                "WHERE state IN ('queued', 'running')",
                (utc_now(),),
            )
            set_meta(target, "last_restore_id", identifier)
            set_meta(target, "last_restore_at", utc_now())
    shutil.rmtree(folder, ignore_errors=True)
    return {
        "kind": "library_restored",
        "id": identifier,
        "repeated": False,
        "counts": preview["incoming"],
    }
