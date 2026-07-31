"""Atomic body/metadata cache with SHA-256 verification."""

import hashlib
import json
import os
import stat
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


class CacheIntegrityError(RuntimeError):
    pass


class SourceAccessError(RuntimeError):
    pass


def attest_bytes(
    payload: bytes,
    sidecar_payload: bytes,
    validator,
    *,
    label: str,
) -> dict:
    """Validate one already-captured body/sidecar pair without reopening paths."""
    try:
        metadata = json.loads(sidecar_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CacheIntegrityError(
            f"invalid cache sidecar for {label}"
        ) from exc
    if not isinstance(metadata, dict):
        raise CacheIntegrityError(
            f"cache sidecar must contain an object for {label}"
        )
    if hashlib.sha256(payload).hexdigest() != metadata.get("sha256"):
        raise CacheIntegrityError(f"SHA-256 mismatch for {label}")
    validator(payload)
    return deepcopy(metadata)


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


def read_attested(path: Path, validator) -> tuple[bytes, dict] | None:
    """Return one stable, validated body/sidecar snapshot."""
    sidecar = path.with_name(path.name + ".meta.json")
    try:
        body_before = path.lstat()
    except FileNotFoundError:
        body_before = None
    try:
        sidecar_before = sidecar.lstat()
    except FileNotFoundError:
        sidecar_before = None
    if body_before is None and sidecar_before is None:
        return None
    if body_before is None or sidecar_before is None:
        raise CacheIntegrityError(f"incomplete cache pair for {path}")
    if (
        not stat.S_ISREG(body_before.st_mode)
        or not stat.S_ISREG(sidecar_before.st_mode)
    ):
        raise CacheIntegrityError(
            f"cache pair must contain regular files for {path}"
        )
    sidecar_payload = sidecar.read_bytes()
    payload = path.read_bytes()
    if sidecar.read_bytes() != sidecar_payload:
        raise CacheIntegrityError(
            f"cache sidecar changed while attesting {path}"
        )
    try:
        body_after = path.lstat()
        sidecar_after = sidecar.lstat()
    except FileNotFoundError:
        raise CacheIntegrityError(
            f"cache pair changed while attesting {path}"
        ) from None
    stable_fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if any(
        getattr(before, field) != getattr(after, field)
        for before, after in (
            (body_before, body_after),
            (sidecar_before, sidecar_after),
        )
        for field in stable_fields
    ):
        raise CacheIntegrityError(
            f"cache pair changed while attesting {path}"
        )
    metadata = json.loads(sidecar_payload.decode("utf-8"))
    if not isinstance(metadata, dict):
        raise CacheIntegrityError(
            f"cache sidecar must contain an object for {path}"
        )
    actual = hashlib.sha256(payload).hexdigest()
    if actual != metadata.get("sha256"):
        raise CacheIntegrityError(f"SHA-256 mismatch for {path}")
    with tempfile.TemporaryDirectory(prefix=".nba-data-attestation-") as root:
        snapshot = Path(root) / path.name
        snapshot.write_bytes(payload)
        validator(snapshot)
    try:
        if (
            path.read_bytes() != payload
            or sidecar.read_bytes() != sidecar_payload
        ):
            raise CacheIntegrityError(
                f"cache pair changed while validating {path}"
            )
    except FileNotFoundError:
        raise CacheIntegrityError(
            f"cache pair changed while validating {path}"
        ) from None
    return payload, metadata


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
