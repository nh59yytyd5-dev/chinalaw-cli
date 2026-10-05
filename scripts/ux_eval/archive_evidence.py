"""Make private, verified evidence archives without changing source files.

Pause writers before use. Refuses an existing destination. The public catalog only
lists dataset-level facts; per-file names and hashes stay inside private archives.
"""

import argparse
import hashlib
import io
import json
import os
import stat
import tarfile
from datetime import datetime, timezone
from pathlib import Path


def digest(stream):
    result = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(chunk)
    return result.hexdigest()


def inventory(root):
    rows = []
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        item = {"path": path.relative_to(root).as_posix()}
        if stat.S_ISLNK(info.st_mode):
            item.update(kind="symlink", target=os.readlink(path))
        elif stat.S_ISREG(info.st_mode):
            with path.open("rb") as stream:
                item.update(kind="file", bytes=info.st_size, sha256=digest(stream))
        elif stat.S_ISDIR(info.st_mode):
            item.update(kind="directory")
        else:
            raise ValueError(f"Unsupported evidence file: {path}")
        rows.append(item)
    return rows


def verify_archive(path, expected):
    with tarfile.open(path, "r:gz") as archive:
        embedded = json.load(archive.extractfile("MANIFEST.json"))
        if embedded != expected:
            raise ValueError("Embedded manifest differs")
        members = {member.name: member for member in archive.getmembers()}
        if len(members) != len(archive.getmembers()):
            raise ValueError("Duplicate archive member")
        if set(members) != {"MANIFEST.json"} | {
            "data/" + item["path"] for item in expected
        }:
            raise ValueError("Archive file inventory differs")
        for item in expected:
            member = members["data/" + item["path"]]
            if item["kind"] == "file":
                if not member.isfile() or member.size != item["bytes"]:
                    raise ValueError("Archive size/type differs")
                if digest(archive.extractfile(member)) != item["sha256"]:
                    raise ValueError("Archive content hash differs")
            elif item["kind"] == "symlink":
                if not member.issym() or member.linkname != item["target"]:
                    raise ValueError("Archive link differs")
            elif not member.isdir():
                raise ValueError("Archive directory differs")


def archive_dataset(root, destination):
    rows = inventory(root)
    output = destination / f"{root.name}.tar.gz"
    temporary = output.with_suffix(".partial")
    with tarfile.open(temporary, "x:gz", compresslevel=6, dereference=False) as archive:
        # Do not coalesce hardlinks: every regular file has a verifiable byte stream.
        for item in rows:
            source = root / item["path"]
            info = archive.gettarinfo(source, arcname="data/" + item["path"])
            if item["kind"] == "file":
                info.type = tarfile.REGTYPE
                info.linkname = ""
                info.size = item["bytes"]
                with source.open("rb") as stream:
                    archive.addfile(info, stream)
            else:
                archive.addfile(info)
        payload = json.dumps(rows, ensure_ascii=False, indent=2).encode()
        info = tarfile.TarInfo("MANIFEST.json")
        info.size = len(payload)
        info.mode = 0o600
        archive.addfile(info, io.BytesIO(payload))
    verify_archive(temporary, rows)
    if inventory(root) != rows:
        raise ValueError("Source changed during archiving; stop writers and retry")
    temporary.rename(output)
    with output.open("rb") as stream:
        checksum = digest(stream)
    return {
        "dataset": root.name,
        "archive": output.name,
        "files": sum(item["kind"] == "file" for item in rows),
        "source_bytes": sum(item.get("bytes", 0) for item in rows),
        "archive_bytes": output.stat().st_size,
        "sha256": checksum,
        "verified": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("roots", type=Path, nargs="+")
    args = parser.parse_args()
    roots = [root.resolve(strict=True) for root in args.roots]
    destination = args.destination.resolve()
    if len({root.name for root in roots}) != len(roots):
        parser.error("Dataset names must be unique")
    if any(destination == root or root in destination.parents for root in roots):
        parser.error("Archive destination must be outside sources")
    os.umask(0o077)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = {"schema_version": 1,
              "created_at": datetime.now(timezone.utc).isoformat(), "datasets": []}
    for root in roots:
        item = archive_dataset(root, destination)
        result["datasets"].append(item)
        (destination / "catalog.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(item), flush=True)


if __name__ == "__main__":
    main()
