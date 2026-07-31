"""Transactional publication of a two-file artifact pair."""

import os
from pathlib import Path
import shutil
import stat
import tempfile


class PairPublicationError(ValueError):
    """Raised when a publication pair violates its transactional contract."""


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def _identity(value: os.stat_result) -> tuple[int, int, int]:
    return value.st_dev, value.st_ino, value.st_mode


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


def publish_pair(
    stage_dir: Path,
    output_dir: Path,
    products: tuple[str, str],
    *,
    label: str,
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
    try:
        staged = sorted(os.listdir(stage_fd))
        if staged != sorted(products):
            raise PairPublicationError(
                f"{label} stage must contain exactly "
                f"{sorted(products)}"
            )
        stage_identities = {}
        for name in products:
            observed = _entry_stat(stage_fd, name)
            if observed is None or not stat.S_ISREG(observed.st_mode):
                raise PairPublicationError(
                    f"non-regular {label} publication product"
                )
            stage_identities[name] = _identity(observed)
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
        if existing:
            rollback_dir = Path(
                tempfile.mkdtemp(
                    prefix=f".{label}-rollback-", dir=output_dir.parent
                )
            )
            rollback_fd = _open_directory(rollback_dir)
            for name in products:
                expected = prior_identities[name]
                _require_unchanged_regular(
                    output_fd, name, expected, label=label
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
        for name in products:
            expected = stage_identities[name]
            _require_unchanged_regular(
                stage_fd, name, expected, label=label
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
        else:
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
