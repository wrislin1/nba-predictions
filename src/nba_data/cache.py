"""Atomic body/metadata cache with SHA-256 verification."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


class CacheIntegrityError(RuntimeError):
    pass


class SourceAccessError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_valid(path: Path, validator) -> bytes | None:
    sidecar = path.with_name(path.name + ".meta.json")
    if not path.exists() and not sidecar.exists():
        return None
    if not path.exists() or not sidecar.exists():
        raise CacheIntegrityError(f"incomplete cache pair for {path}")
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    actual = sha256_file(path)
    if actual != metadata.get("sha256"):
        raise CacheIntegrityError(f"SHA-256 mismatch for {path}")
    validator(path)
    return path.read_bytes()


def require_valid(path: Path, validator) -> bytes:
    payload = read_valid(path, validator)
    if payload is None:
        raise CacheIntegrityError(f"missing cache entry for {path}")
    return payload


def publish_bytes(path: Path, payload: bytes, metadata: dict, validator) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body_part = path.with_name(path.name + ".part")
    meta_path = path.with_name(path.name + ".meta.json")
    meta_part = meta_path.with_name(meta_path.name + ".part")
    body_part.write_bytes(payload)
    validator(body_part)
    record = {
        **metadata,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "content_length": len(payload),
        "sha256": sha256_file(body_part),
    }
    meta_part.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    os.replace(body_part, path)
    os.replace(meta_part, meta_path)
    return path
