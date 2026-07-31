"""Transactional publication of a two-file artifact pair."""

import os
from pathlib import Path
import shutil
import tempfile


class PairPublicationError(ValueError):
    """Raised when a publication pair violates its transactional contract."""


def _restore_products(
    rollback_dir: Path,
    moved_prior: list[str],
    output_dir: Path,
    products: tuple[str, str],
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
        for name in moved_prior:
            shutil.copy2(rollback_dir / name, restoration_dir / name)
        for name in moved_prior:
            os.replace(restoration_dir / name, output_dir / name)
    except OSError as restoration_error:
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
    staged = sorted(path.name for path in stage_dir.iterdir())
    if staged != sorted(products):
        raise PairPublicationError(
            f"{label} stage must contain exactly "
            f"{sorted(products)}"
        )
    if any(
        (stage_dir / name).is_symlink()
        or not (stage_dir / name).is_file()
        for name in products
    ):
        raise PairPublicationError(
            f"non-regular {label} publication product"
        )
    existing = [name for name in products if (
        (output_dir / name).exists() or (output_dir / name).is_symlink()
    )]
    if existing and len(existing) != len(products):
        raise PairPublicationError(f"{label} publication pair is incomplete")
    if any(
        (output_dir / name).is_symlink()
        or not (output_dir / name).is_file()
        for name in existing
    ):
        raise PairPublicationError(
            f"non-regular {label} publication product"
        )
    rollback_dir = None
    moved_prior = []
    published = []
    try:
        if existing:
            rollback_dir = Path(
                tempfile.mkdtemp(
                    prefix=f".{label}-rollback-", dir=output_dir.parent
                )
            )
            for name in products:
                os.replace(output_dir / name, rollback_dir / name)
                moved_prior.append(name)
        for name in products:
            os.replace(stage_dir / name, output_dir / name)
            published.append(name)
    except OSError:
        if rollback_dir is not None:
            _restore_products(
                rollback_dir,
                moved_prior,
                output_dir,
                products,
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
            for name in published:
                path = output_dir / name
                if path.exists():
                    path.unlink()
        raise
    if rollback_dir is not None and rollback_dir.exists():
        try:
            shutil.rmtree(rollback_dir)
        except OSError:
            return rollback_dir
    return None
