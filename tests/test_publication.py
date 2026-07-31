import os
from pathlib import Path

import pytest

from nba_data import publication


PRODUCTS = ("games.parquet", "manifest.json")


def _seed_pair(directory: Path, prefix: str) -> dict[str, bytes]:
    payloads = {name: f"{prefix}-{name}".encode() for name in PRODUCTS}
    for name, payload in payloads.items():
        (directory / name).write_bytes(payload)
    return payloads


def _read_pair(directory: Path) -> dict[str, bytes]:
    return {name: (directory / name).read_bytes() for name in PRODUCTS}


def test_publish_pair_replaces_both_products(tmp_path):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _seed_pair(stage, "new")
    expected = _seed_pair(output, "old")

    retained = publication.publish_pair(
        stage, output, PRODUCTS, label="playoff"
    )

    assert retained is None
    assert _read_pair(output) != expected
    assert _read_pair(output) == {
        name: f"new-{name}".encode() for name in PRODUCTS
    }


@pytest.mark.parametrize(
    "products",
    [
        ("games.parquet",),
        ("games.parquet", "games.parquet"),
        ("nested/games.parquet", "manifest.json"),
    ],
)
def test_publish_pair_requires_two_unique_basenames(tmp_path, products):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()

    with pytest.raises(
        publication.PairPublicationError,
        match="publication requires two unique basenames",
    ):
        publication.publish_pair(stage, output, products, label="playoff")


@pytest.mark.parametrize("extra", [False, True])
def test_publish_pair_rejects_incomplete_or_extra_stage(tmp_path, extra):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    (stage / PRODUCTS[0]).write_bytes(b"new")
    if extra:
        (stage / PRODUCTS[1]).write_bytes(b"new")
        (stage / "extra.txt").write_bytes(b"extra")

    with pytest.raises(
        publication.PairPublicationError,
        match="playoff stage must contain exactly",
    ):
        publication.publish_pair(stage, output, PRODUCTS, label="playoff")


@pytest.mark.parametrize("kind", ["directory", "symlink", "fifo"])
def test_publish_pair_rejects_non_regular_staged_product(tmp_path, kind):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _seed_pair(stage, "new")
    target = stage / PRODUCTS[0]
    target.unlink()
    if kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        os.mkfifo(target)
    else:
        replacement = tmp_path / "replacement-stage"
        replacement.write_bytes(b"new")
        target.symlink_to(replacement)

    with pytest.raises(
        publication.PairPublicationError,
        match="non-regular playoff publication product",
    ):
        publication.publish_pair(stage, output, PRODUCTS, label="playoff")


def test_publish_pair_rejects_incomplete_prior_pair(tmp_path):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _seed_pair(stage, "new")
    (output / PRODUCTS[0]).write_bytes(b"old")

    with pytest.raises(
        publication.PairPublicationError,
        match="playoff publication pair is incomplete",
    ):
        publication.publish_pair(stage, output, PRODUCTS, label="playoff")


@pytest.mark.parametrize("kind", ["directory", "symlink", "fifo"])
def test_publish_pair_rejects_non_regular_prior_product(tmp_path, kind):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _seed_pair(stage, "new")
    _seed_pair(output, "old")
    target = output / PRODUCTS[0]
    target.unlink()
    if kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        os.mkfifo(target)
    else:
        replacement = tmp_path / "replacement-prior"
        replacement.write_bytes(b"old")
        target.symlink_to(replacement)

    with pytest.raises(
        publication.PairPublicationError,
        match="non-regular playoff publication product",
    ):
        publication.publish_pair(stage, output, PRODUCTS, label="playoff")


@pytest.mark.parametrize("failed_move", [1, 2])
def test_publish_pair_restores_prior_when_backup_move_fails(
    monkeypatch, tmp_path, failed_move
):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _seed_pair(stage, "new")
    old = _seed_pair(output, "old")
    real_replace = os.replace
    backup_moves = 0

    def fail_selected_backup(source, destination):
        nonlocal backup_moves
        source = Path(source)
        destination = Path(destination)
        if (
            source.parent == output
            and destination.parent.name.startswith(".playoff-rollback-")
        ):
            backup_moves += 1
            if backup_moves == failed_move:
                raise OSError(f"backup move {failed_move} failed")
        real_replace(source, destination)

    monkeypatch.setattr(publication.os, "replace", fail_selected_backup)

    with pytest.raises(OSError, match=f"backup move {failed_move} failed"):
        publication.publish_pair(stage, output, PRODUCTS, label="playoff")

    assert _read_pair(output) == old
    assert list(tmp_path.glob(".playoff-rollback-*")) == []


@pytest.mark.parametrize("failed_move", [1, 2])
def test_publish_pair_rolls_back_when_new_product_move_fails(
    monkeypatch, tmp_path, failed_move
):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _seed_pair(stage, "new")
    old = _seed_pair(output, "old")
    real_replace = os.replace
    publication_moves = 0

    def fail_selected_publication(source, destination):
        nonlocal publication_moves
        source = Path(source)
        destination = Path(destination)
        if source.parent == stage and destination.parent == output:
            publication_moves += 1
            if publication_moves == failed_move:
                raise OSError(f"publication move {failed_move} failed")
        real_replace(source, destination)

    monkeypatch.setattr(publication.os, "replace", fail_selected_publication)

    with pytest.raises(
        OSError, match=f"publication move {failed_move} failed"
    ):
        publication.publish_pair(stage, output, PRODUCTS, label="playoff")

    assert _read_pair(output) == old
    assert list(tmp_path.glob(".playoff-rollback-*")) == []


def test_publish_pair_removes_partial_first_publication(
    monkeypatch, tmp_path
):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _seed_pair(stage, "new")
    real_replace = os.replace
    publication_moves = 0

    def fail_second_publication(source, destination):
        nonlocal publication_moves
        source = Path(source)
        destination = Path(destination)
        if source.parent == stage and destination.parent == output:
            publication_moves += 1
            if publication_moves == 2:
                raise OSError("second publication failed")
        real_replace(source, destination)

    monkeypatch.setattr(publication.os, "replace", fail_second_publication)

    with pytest.raises(OSError, match="second publication failed"):
        publication.publish_pair(stage, output, PRODUCTS, label="playoff")

    assert not any((output / name).exists() for name in PRODUCTS)


def test_publish_pair_retains_recovery_after_restoration_failure(
    monkeypatch, tmp_path
):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _seed_pair(stage, "new")
    old = _seed_pair(output, "old")
    real_replace = os.replace
    publication_moves = 0

    def fail_publication_and_restoration(source, destination):
        nonlocal publication_moves
        source = Path(source)
        destination = Path(destination)
        if source.parent == stage and destination.parent == output:
            publication_moves += 1
            if publication_moves == 2:
                raise OSError("publication failed")
        if (
            source.parent.name.startswith(".playoff-restoration-")
            and destination.parent == output
        ):
            raise OSError("restoration failed")
        real_replace(source, destination)

    monkeypatch.setattr(
        publication.os, "replace", fail_publication_and_restoration
    )

    with pytest.raises(
        publication.PairPublicationError,
        match="playoff publication restoration failed",
    ) as caught:
        publication.publish_pair(stage, output, PRODUCTS, label="playoff")

    recovery_dir = next(tmp_path.glob(".playoff-rollback-*"))
    assert str(recovery_dir) in str(caught.value)
    assert _read_pair(recovery_dir) == old


def test_publish_pair_retains_rollback_when_cleanup_after_restore_fails(
    monkeypatch, tmp_path
):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _seed_pair(stage, "new")
    old = _seed_pair(output, "old")
    real_replace = os.replace
    real_rmtree = publication.shutil.rmtree
    publication_moves = 0

    def fail_second_publication(source, destination):
        nonlocal publication_moves
        source = Path(source)
        destination = Path(destination)
        if source.parent == stage and destination.parent == output:
            publication_moves += 1
            if publication_moves == 2:
                raise OSError("publication failed")
        real_replace(source, destination)

    def fail_rollback_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".playoff-rollback-"):
            raise OSError("cleanup failed")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(publication.os, "replace", fail_second_publication)
    monkeypatch.setattr(publication.shutil, "rmtree", fail_rollback_cleanup)

    with pytest.raises(
        publication.PairPublicationError,
        match="playoff publication failed after restoring the prior pair",
    ) as caught:
        publication.publish_pair(stage, output, PRODUCTS, label="playoff")

    recovery_dir = next(tmp_path.glob(".playoff-rollback-*"))
    assert str(recovery_dir) in str(caught.value)
    assert _read_pair(output) == old
    assert _read_pair(recovery_dir) == old


def test_publish_pair_returns_retained_prior_after_commit_cleanup_failure(
    monkeypatch, tmp_path
):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    new = _seed_pair(stage, "new")
    old = _seed_pair(output, "old")
    real_rmtree = publication.shutil.rmtree

    def retain_rollback(path, *args, **kwargs):
        if Path(path).name.startswith(".playoff-rollback-"):
            raise OSError("cleanup failed")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(publication.shutil, "rmtree", retain_rollback)

    recovery_dir = publication.publish_pair(
        stage, output, PRODUCTS, label="playoff"
    )

    assert recovery_dir == next(tmp_path.glob(".playoff-rollback-*"))
    assert _read_pair(output) == new
    assert _read_pair(recovery_dir) == old
