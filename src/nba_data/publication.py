"""Transactional publication of a two-file artifact pair."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


class PairPublicationError(ValueError):
    """Raised when a publication pair violates its transactional contract."""


LOCK_FILENAME = ".parquet-publication.lock"


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def _identity(value: os.stat_result) -> tuple[int, int, int]:
    return value.st_dev, value.st_ino, value.st_mode


def lock_path(output_dir: Path) -> Path:
    """Return the stable lock path governing ``output_dir`` publications."""
    return output_dir.parent / LOCK_FILENAME


@contextmanager
def publication_lock(
    output_dir: Path, *, exclusive: bool
) -> Iterator[None]:
    """Hold the mandatory stable reader/writer publication lock."""
    parent_fd = _open_directory(output_dir.parent)
    lock_fd = None
    try:
        prior = _entry_stat(parent_fd, LOCK_FILENAME)
        if prior is not None and not stat.S_ISREG(prior.st_mode):
            raise PairPublicationError(
                "publication lock must be a regular file"
            )
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            lock_fd = os.open(
                LOCK_FILENAME,
                flags,
                0o600,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            failed = _entry_stat(parent_fd, LOCK_FILENAME)
            if failed is not None and not stat.S_ISREG(failed.st_mode):
                raise PairPublicationError(
                    "publication lock must be a regular file"
                ) from exc
            raise
        observed = os.fstat(lock_fd)
        if not stat.S_ISREG(observed.st_mode):
            raise PairPublicationError(
                "publication lock must be a regular file"
            )
        path_observed = _entry_stat(parent_fd, LOCK_FILENAME)
        if (
            path_observed is None
            or _identity(path_observed) != _identity(observed)
        ):
            raise PairPublicationError(
                "publication lock changed while opening"
            )
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(lock_fd, operation)
        path_after_wait = _entry_stat(parent_fd, LOCK_FILENAME)
        if (
            path_after_wait is None
            or _identity(path_after_wait) != _identity(observed)
        ):
            raise PairPublicationError(
                "publication lock changed while waiting"
            )
        yield
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        os.close(parent_fd)


def _entry_stat(directory_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None


def _require_unchanged_regular(
    directory_fd: int,
    name: str,
    expected: tuple[int, int, int],
    *,
    label: str,
) -> None:
    observed = _entry_stat(directory_fd, name)
    if (
        observed is None
        or not stat.S_ISREG(observed.st_mode)
        or _identity(observed) != expected
    ):
        raise PairPublicationError(
            f"{label} publication product changed during publication: "
            f"{name}"
        )


def _read_unchanged_regular(
    directory_fd: int,
    name: str,
    expected: tuple[int, int, int],
    *,
    label: str,
) -> bytes:
    _require_unchanged_regular(
        directory_fd, name, expected, label=label
    )
    file_fd = os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    try:
        if _identity(os.fstat(file_fd)) != expected:
            raise PairPublicationError(
                f"{label} publication product changed during publication: "
                f"{name}"
            )
        with os.fdopen(os.dup(file_fd), "rb") as handle:
            payload = handle.read()
        if _identity(os.fstat(file_fd)) != expected:
            raise PairPublicationError(
                f"{label} publication product changed during publication: "
                f"{name}"
            )
    finally:
        os.close(file_fd)
    _require_unchanged_regular(
        directory_fd, name, expected, label=label
    )
    return payload


def _require_content(
    directory_fd: int,
    name: str,
    expected_identity: tuple[int, int, int],
    expected_sha256: str,
    *,
    label: str,
) -> bytes:
    payload = _read_unchanged_regular(
        directory_fd,
        name,
        expected_identity,
        label=label,
    )
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise PairPublicationError(
            f"{label} publication product content changed during "
            f"publication: {name}"
        )
    return payload


def _validate_manifest_binding(
    payloads: dict[str, bytes],
    products: tuple[str, str],
    manifest_table: str | None,
    *,
    label: str,
) -> None:
    if manifest_table is None:
        return
    parquet_names = [name for name in products if name.endswith(".parquet")]
    manifest_names = [name for name in products if name.endswith(".json")]
    if len(parquet_names) != 1 or len(manifest_names) != 1:
        raise PairPublicationError(
            f"{label} manifest binding requires one Parquet and one JSON product"
        )
    try:
        manifest = json.loads(
            payloads[manifest_names[0]].decode("utf-8")
        )
        expected = manifest["tables"][manifest_table]["sha256"]
    except (
        KeyError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise PairPublicationError(
            f"{label} manifest does not bind published Parquet"
        ) from exc
    actual = hashlib.sha256(payloads[parquet_names[0]]).hexdigest()
    if not isinstance(expected, str) or expected != actual:
        raise PairPublicationError(
            f"{label} manifest does not bind published Parquet"
        )


def _read_pair_bytes_locked(
    output_dir: Path,
    products: tuple[str, str],
    *,
    label: str,
    manifest_table: str | None = None,
) -> dict[str, bytes]:
    output_fd = _open_directory(output_dir)
    try:
        observed = {
            name: _entry_stat(output_fd, name) for name in products
        }
        existing = [name for name, value in observed.items() if value is not None]
        if len(existing) != len(products):
            raise PairPublicationError(
                f"{label} publication pair is incomplete"
            )
        identities = {}
        payloads = {}
        for name in products:
            value = observed[name]
            assert value is not None
            if not stat.S_ISREG(value.st_mode):
                raise PairPublicationError(
                    f"non-regular {label} publication product"
                )
            identities[name] = _identity(value)
        for name in products:
            payloads[name] = _read_unchanged_regular(
                output_fd,
                name,
                identities[name],
                label=label,
            )
        for name in products:
            _require_content(
                output_fd,
                name,
                identities[name],
                hashlib.sha256(payloads[name]).hexdigest(),
                label=label,
            )
        _validate_manifest_binding(
            payloads,
            products,
            manifest_table,
            label=label,
        )
        return payloads
    finally:
        os.close(output_fd)


def read_pair_bytes(
    output_dir: Path,
    products: tuple[str, str],
    *,
    label: str,
    manifest_table: str | None = None,
) -> dict[str, bytes]:
    """Read one coherent pair while holding the mandatory shared lock."""
    with publication_lock(output_dir, exclusive=False):
        return _read_pair_bytes_locked(
            output_dir,
            products,
            label=label,
            manifest_table=manifest_table,
        )


def _remove_entry(directory_fd: int, name: str) -> None:
    observed = _entry_stat(directory_fd, name)
    if observed is None:
        return
    if stat.S_ISDIR(observed.st_mode):
        os.rmdir(name, dir_fd=directory_fd)
    else:
        os.unlink(name, dir_fd=directory_fd)


def _restore_products(
    rollback_dir: Path,
    moved_prior: list[str],
    output_dir: Path,
    products: tuple[str, str],
    prior_identities: dict[str, tuple[int, int, int]],
    prior_sha256: dict[str, str],
    *,
    label: str,
) -> None:
    """Restore prior products without consuming the retained recovery data."""
    restoration_dir = None
    complete_pair = len(moved_prior) == len(products)
    try:
        restoration_dir = Path(
            tempfile.mkdtemp(
                prefix=f".{label}-restoration-",
                dir=output_dir.parent,
            )
        )
        rollback_fd = _open_directory(rollback_dir)
        restoration_fd = _open_directory(restoration_dir)
        output_fd = _open_directory(output_dir)
        restored_identities = {}
        try:
            for name in moved_prior:
                expected = prior_identities[name]
                _require_unchanged_regular(
                    rollback_fd,
                    name,
                    expected,
                    label=label,
                )
                _require_content(
                    rollback_fd,
                    name,
                    expected,
                    prior_sha256[name],
                    label=label,
                )
                source_fd = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=rollback_fd,
                )
                try:
                    if _identity(os.fstat(source_fd)) != expected:
                        raise PairPublicationError(
                            f"{label} publication product changed during "
                            f"publication: {name}"
                        )
                    destination_fd = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=restoration_fd,
                    )
                    try:
                        os.fchmod(
                            destination_fd,
                            stat.S_IMODE(expected[2]),
                        )
                        with (
                            os.fdopen(
                                os.dup(source_fd), "rb"
                            ) as source_handle,
                            os.fdopen(
                                os.dup(destination_fd), "wb"
                            ) as destination_handle,
                        ):
                            shutil.copyfileobj(
                                source_handle, destination_handle
                            )
                            destination_handle.flush()
                            os.fsync(destination_handle.fileno())
                    finally:
                        os.close(destination_fd)
                finally:
                    os.close(source_fd)
                restored = _entry_stat(restoration_fd, name)
                assert restored is not None
                restored_identities[name] = _identity(restored)
                _require_content(
                    restoration_fd,
                    name,
                    restored_identities[name],
                    prior_sha256[name],
                    label=label,
                )
            for name in moved_prior:
                expected = restored_identities[name]
                _require_unchanged_regular(
                    restoration_fd,
                    name,
                    expected,
                    label=label,
                )
                os.replace(
                    name,
                    name,
                    src_dir_fd=restoration_fd,
                    dst_dir_fd=output_fd,
                )
                _require_unchanged_regular(
                    output_fd,
                    name,
                    expected,
                    label=label,
                )
                _require_content(
                    output_fd,
                    name,
                    expected,
                    prior_sha256[name],
                    label=label,
                )
        finally:
            os.close(output_fd)
            os.close(restoration_fd)
            os.close(rollback_fd)
    except (OSError, PairPublicationError) as restoration_error:
        retained = (
            "complete recovery pair"
            if complete_pair
            else "recovery data"
        )
        raise PairPublicationError(
            f"{label} publication restoration failed; "
            f"{retained} retained at {rollback_dir}"
        ) from restoration_error
    finally:
        if restoration_dir is not None and restoration_dir.exists():
            shutil.rmtree(restoration_dir, ignore_errors=True)


def _publish_pair_locked(
    stage_dir: Path,
    output_dir: Path,
    products: tuple[str, str],
    *,
    label: str,
    manifest_table: str | None,
    precommit: Callable[[], None] | None,
) -> Path | None:
    """Publish the pair, returning retained rollback data after commit."""
    if (
        len(products) != 2
        or len(set(products)) != 2
        or any(Path(name).name != name for name in products)
    ):
        raise PairPublicationError(
            "publication requires two unique basenames"
        )
    stage_fd = _open_directory(stage_dir)
    output_fd = _open_directory(output_dir)
    rollback_dir = None
    rollback_fd = None
    moved_prior = []
    published = []
    prior_identities = {}
    prior_sha256 = {}
    publication_started = False
    try:
        staged = sorted(os.listdir(stage_fd))
        if staged != sorted(products):
            raise PairPublicationError(
                f"{label} stage must contain exactly "
                f"{sorted(products)}"
            )
        stage_identities = {}
        stage_sha256 = {}
        stage_payloads = {}
        for name in products:
            observed = _entry_stat(stage_fd, name)
            if observed is None or not stat.S_ISREG(observed.st_mode):
                raise PairPublicationError(
                    f"non-regular {label} publication product"
                )
            stage_identities[name] = _identity(observed)
            payload = _read_unchanged_regular(
                stage_fd,
                name,
                stage_identities[name],
                label=label,
            )
            stage_payloads[name] = payload
            stage_sha256[name] = hashlib.sha256(payload).hexdigest()
        _validate_manifest_binding(
            stage_payloads,
            products,
            manifest_table,
            label=label,
        )
        prior_stats = {
            name: _entry_stat(output_fd, name) for name in products
        }
        existing = [
            name for name, observed in prior_stats.items()
            if observed is not None
        ]
        if existing and len(existing) != len(products):
            raise PairPublicationError(
                f"{label} publication pair is incomplete"
            )
        for name in existing:
            observed = prior_stats[name]
            assert observed is not None
            if not stat.S_ISREG(observed.st_mode):
                raise PairPublicationError(
                    f"non-regular {label} publication product"
                )
            prior_identities[name] = _identity(observed)
            prior_payload = _read_unchanged_regular(
                output_fd,
                name,
                prior_identities[name],
                label=label,
            )
            prior_sha256[name] = hashlib.sha256(prior_payload).hexdigest()
        if precommit is not None:
            precommit()
        rebound_payloads = {}
        for name in products:
            rebound_payloads[name] = _require_content(
                stage_fd,
                name,
                stage_identities[name],
                stage_sha256[name],
                label=label,
            )
        _validate_manifest_binding(
            rebound_payloads,
            products,
            manifest_table,
            label=label,
        )
        if existing:
            rollback_dir = Path(
                tempfile.mkdtemp(
                    prefix=f".{label}-rollback-", dir=output_dir.parent
                )
            )
            rollback_fd = _open_directory(rollback_dir)
            publication_started = True
            for name in products:
                expected = prior_identities[name]
                _require_unchanged_regular(
                    output_fd, name, expected, label=label
                )
                _require_content(
                    output_fd,
                    name,
                    expected,
                    prior_sha256[name],
                    label=label,
                )
                os.replace(
                    name,
                    name,
                    src_dir_fd=output_fd,
                    dst_dir_fd=rollback_fd,
                )
                moved_prior.append(name)
                _require_unchanged_regular(
                    rollback_fd, name, expected, label=label
                )
                _require_content(
                    rollback_fd,
                    name,
                    expected,
                    prior_sha256[name],
                    label=label,
                )
        else:
            publication_started = True
        for name in products:
            expected = stage_identities[name]
            _require_unchanged_regular(
                stage_fd, name, expected, label=label
            )
            _require_content(
                stage_fd,
                name,
                expected,
                stage_sha256[name],
                label=label,
            )
            os.replace(
                name,
                name,
                src_dir_fd=stage_fd,
                dst_dir_fd=output_fd,
            )
            published.append(name)
            _require_unchanged_regular(
                output_fd, name, expected, label=label
            )
            _require_content(
                output_fd,
                name,
                expected,
                stage_sha256[name],
                label=label,
            )
        committed_payloads = {}
        for name in products:
            committed_payloads[name] = _require_content(
                output_fd,
                name,
                stage_identities[name],
                stage_sha256[name],
                label=label,
            )
        _validate_manifest_binding(
            committed_payloads,
            products,
            manifest_table,
            label=label,
        )
    except (OSError, PairPublicationError):
        if rollback_dir is not None:
            if rollback_fd is not None:
                os.close(rollback_fd)
                rollback_fd = None
            _restore_products(
                rollback_dir,
                moved_prior,
                output_dir,
                products,
                prior_identities,
                prior_sha256,
                label=label,
            )
            try:
                shutil.rmtree(rollback_dir)
            except OSError as cleanup_error:
                raise PairPublicationError(
                    f"{label} publication failed after restoring the prior "
                    "pair; rollback data retained at "
                    f"{rollback_dir}"
                ) from cleanup_error
        elif publication_started:
            for name in products:
                _remove_entry(output_fd, name)
        raise
    finally:
        if rollback_fd is not None:
            os.close(rollback_fd)
        os.close(output_fd)
        os.close(stage_fd)
    if rollback_dir is not None and rollback_dir.exists():
        try:
            shutil.rmtree(rollback_dir)
        except OSError:
            return rollback_dir
    return None


def publish_pair(
    stage_dir: Path,
    output_dir: Path,
    products: tuple[str, str],
    *,
    label: str,
    manifest_table: str | None = None,
    precommit: Callable[[], None] | None = None,
) -> Path | None:
    """Publish a pair under the mandatory exclusive publication lock."""
    with publication_lock(output_dir, exclusive=True):
        return _publish_pair_locked(
            stage_dir,
            output_dir,
            products,
            label=label,
            manifest_table=manifest_table,
            precommit=precommit,
        )
