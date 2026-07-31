import hashlib
import json
import os
from pathlib import Path
import threading

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


def _write_bound_stage(directory: Path, payload: bytes) -> None:
    (directory / PRODUCTS[0]).write_bytes(payload)
    (directory / PRODUCTS[1]).write_text(
        json.dumps({
            "tables": {
                "games": {
                    "sha256": hashlib.sha256(payload).hexdigest(),
                },
            },
        }),
        encoding="utf-8",
    )


def _replace_operand(value, directory_fd=None) -> Path:
    path = Path(value)
    if directory_fd is None:
        return path
    directory = Path(os.readlink(f"/proc/self/fd/{directory_fd}"))
    return directory / path


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

    def fail_selected_backup(source, destination, *args, **kwargs):
        nonlocal backup_moves
        source_path = _replace_operand(
            source, kwargs.get("src_dir_fd")
        )
        destination_path = _replace_operand(
            destination, kwargs.get("dst_dir_fd")
        )
        if (
            source_path.parent == output
            and destination_path.parent.name.startswith(
                ".playoff-rollback-"
            )
        ):
            backup_moves += 1
            if backup_moves == failed_move:
                raise OSError(f"backup move {failed_move} failed")
        real_replace(source, destination, *args, **kwargs)

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

    def fail_selected_publication(
        source, destination, *args, **kwargs
    ):
        nonlocal publication_moves
        source_path = _replace_operand(
            source, kwargs.get("src_dir_fd")
        )
        destination_path = _replace_operand(
            destination, kwargs.get("dst_dir_fd")
        )
        if (
            source_path.parent == stage
            and destination_path.parent == output
        ):
            publication_moves += 1
            if publication_moves == failed_move:
                raise OSError(f"publication move {failed_move} failed")
        real_replace(source, destination, *args, **kwargs)

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

    def fail_second_publication(
        source, destination, *args, **kwargs
    ):
        nonlocal publication_moves
        source_path = _replace_operand(
            source, kwargs.get("src_dir_fd")
        )
        destination_path = _replace_operand(
            destination, kwargs.get("dst_dir_fd")
        )
        if (
            source_path.parent == stage
            and destination_path.parent == output
        ):
            publication_moves += 1
            if publication_moves == 2:
                raise OSError("second publication failed")
        real_replace(source, destination, *args, **kwargs)

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

    def fail_publication_and_restoration(
        source, destination, *args, **kwargs
    ):
        nonlocal publication_moves
        source_path = _replace_operand(
            source, kwargs.get("src_dir_fd")
        )
        destination_path = _replace_operand(
            destination, kwargs.get("dst_dir_fd")
        )
        if (
            source_path.parent == stage
            and destination_path.parent == output
        ):
            publication_moves += 1
            if publication_moves == 2:
                raise OSError("publication failed")
        if (
            source_path.parent.name.startswith(
                ".playoff-restoration-"
            )
            and destination_path.parent == output
        ):
            raise OSError("restoration failed")
        real_replace(source, destination, *args, **kwargs)

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

    def fail_second_publication(
        source, destination, *args, **kwargs
    ):
        nonlocal publication_moves
        source_path = _replace_operand(
            source, kwargs.get("src_dir_fd")
        )
        destination_path = _replace_operand(
            destination, kwargs.get("dst_dir_fd")
        )
        if (
            source_path.parent == stage
            and destination_path.parent == output
        ):
            publication_moves += 1
            if publication_moves == 2:
                raise OSError("publication failed")
        real_replace(source, destination, *args, **kwargs)

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


def test_publish_pair_rejects_staged_object_swapped_during_move(
    monkeypatch, tmp_path
):
    """Would fail if validation were not bound to the object moved."""
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _seed_pair(stage, "new")
    missing = tmp_path / "missing-target"
    real_replace = os.replace
    swapped = False

    def swap_before_move(source, destination, *args, **kwargs):
        nonlocal swapped
        source_path = _replace_operand(
            source, kwargs.get("src_dir_fd")
        )
        if not swapped and source_path == stage / PRODUCTS[0]:
            source_path.unlink()
            source_path.symlink_to(missing)
            swapped = True
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(publication.os, "replace", swap_before_move)

    with pytest.raises(
        publication.PairPublicationError,
        match="changed during publication",
    ):
        publication.publish_pair(stage, output, PRODUCTS, label="playoff")

    assert not any(
        os.path.lexists(output / name) for name in PRODUCTS
    )


def test_publish_pair_removes_dangling_symlink_when_move_then_fails(
    monkeypatch, tmp_path
):
    """Would fail if rollback cleanup followed a dangling symlink."""
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _seed_pair(stage, "new")
    missing = tmp_path / "missing-target"
    real_replace = os.replace
    failed = False

    def move_swapped_object_then_fail(
        source, destination, *args, **kwargs
    ):
        nonlocal failed
        source_path = _replace_operand(
            source, kwargs.get("src_dir_fd")
        )
        if not failed and source_path == stage / PRODUCTS[0]:
            source_path.unlink()
            source_path.symlink_to(missing)
            real_replace(source, destination, *args, **kwargs)
            failed = True
            raise OSError("failure after moving dangling symlink")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(
        publication.os, "replace", move_swapped_object_then_fail
    )

    with pytest.raises(
        OSError, match="failure after moving dangling symlink"
    ):
        publication.publish_pair(stage, output, PRODUCTS, label="playoff")

    assert not any(
        os.path.lexists(output / name) for name in PRODUCTS
    )


def test_publish_pair_rejects_prior_object_swapped_during_backup(
    monkeypatch, tmp_path
):
    """Would fail if the backed-up prior were not the validated inode."""
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _seed_pair(stage, "new")
    _seed_pair(output, "old")
    missing = tmp_path / "missing-prior-target"
    real_replace = os.replace
    swapped = False

    def swap_before_backup(source, destination, *args, **kwargs):
        nonlocal swapped
        source_path = _replace_operand(
            source, kwargs.get("src_dir_fd")
        )
        destination_path = _replace_operand(
            destination, kwargs.get("dst_dir_fd")
        )
        if (
            not swapped
            and source_path == output / PRODUCTS[0]
            and destination_path.parent.name.startswith(
                ".playoff-rollback-"
            )
        ):
            source_path.unlink()
            source_path.symlink_to(missing)
            swapped = True
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(publication.os, "replace", swap_before_backup)

    with pytest.raises(
        publication.PairPublicationError,
        match="publication restoration failed",
    ):
        publication.publish_pair(stage, output, PRODUCTS, label="playoff")

    assert all(
        (stage / name).read_bytes().startswith(b"new-")
        for name in PRODUCTS
    )
    assert not any(
        (output / name).is_file()
        and (output / name).read_bytes().startswith(b"new-")
        for name in PRODUCTS
    )


def test_compliant_pair_reader_blocks_until_complete_publication(
    monkeypatch, tmp_path
):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _seed_pair(stage, "new")
    _seed_pair(output, "old")
    real_replace = os.replace
    transaction_started = threading.Event()
    allow_writer = threading.Event()
    reader_finished = threading.Event()
    observed = {}

    def pause_after_first_backup(source, destination, *args, **kwargs):
        source_path = _replace_operand(source, kwargs.get("src_dir_fd"))
        destination_path = _replace_operand(
            destination, kwargs.get("dst_dir_fd")
        )
        result = real_replace(source, destination, *args, **kwargs)
        if (
            source_path == output / PRODUCTS[0]
            and destination_path.parent.name.startswith(
                ".playoff-rollback-"
            )
        ):
            transaction_started.set()
            assert allow_writer.wait(timeout=5)
        return result

    def publish():
        publication.publish_pair(
            stage, output, PRODUCTS, label="playoff"
        )

    def read():
        observed.update(
            publication.read_pair_bytes(
                output, PRODUCTS, label="playoff"
            )
        )
        reader_finished.set()

    monkeypatch.setattr(publication.os, "replace", pause_after_first_backup)
    writer = threading.Thread(target=publish)
    writer.start()
    assert transaction_started.wait(timeout=5)
    reader = threading.Thread(target=read)
    reader.start()
    assert not reader_finished.wait(timeout=0.2)
    allow_writer.set()
    writer.join(timeout=5)
    reader.join(timeout=5)

    assert not writer.is_alive()
    assert not reader.is_alive()
    assert observed == {
        name: f"new-{name}".encode() for name in PRODUCTS
    }


def test_publication_lock_remains_stable_when_output_directory_is_replaced(
    tmp_path,
):
    output = tmp_path / "parquet"
    replacement = tmp_path / "replacement"
    output.mkdir()
    replacement.mkdir()

    with publication.publication_lock(output, exclusive=False):
        lock_path = publication.lock_path(output)
        before = lock_path.stat()
        os.replace(output, tmp_path / "prior")
        os.replace(replacement, output)
        after = lock_path.stat()

    assert lock_path.parent == output.parent
    assert lock_path.parent != output
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)


def test_publication_lock_rejects_path_replacement_while_waiting(
    monkeypatch, tmp_path
):
    output = tmp_path / "parquet"
    output.mkdir()
    real_flock = publication.fcntl.flock
    swapped = False

    def swap_after_acquire(lock_fd, operation):
        nonlocal swapped
        real_flock(lock_fd, operation)
        if operation != publication.fcntl.LOCK_UN and not swapped:
            replacement = tmp_path / "replacement-lock"
            replacement.write_bytes(b"")
            os.replace(replacement, publication.lock_path(output))
            swapped = True

    monkeypatch.setattr(publication.fcntl, "flock", swap_after_acquire)

    with pytest.raises(
        publication.PairPublicationError,
        match="publication lock changed while waiting",
    ):
        with publication.publication_lock(output, exclusive=False):
            pytest.fail("replaced lock path must not enter the critical section")

    assert swapped


@pytest.mark.parametrize("kind", ["directory", "symlink", "fifo"])
def test_publication_lock_rejects_non_regular_lock_path(tmp_path, kind):
    output = tmp_path / "parquet"
    output.mkdir()
    lock = publication.lock_path(output)
    if kind == "directory":
        lock.mkdir()
    elif kind == "symlink":
        lock.symlink_to(tmp_path / "missing-lock-target")
    else:
        os.mkfifo(lock)

    with pytest.raises(
        publication.PairPublicationError,
        match="publication lock must be a regular file",
    ):
        with publication.publication_lock(output, exclusive=False):
            pytest.fail("non-regular lock must not enter the critical section")


def test_publish_pair_rolls_back_same_inode_content_mutation(
    monkeypatch, tmp_path
):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _write_bound_stage(stage, b"new parquet bytes")
    old = _seed_pair(output, "old")
    real_replace = os.replace
    mutated = False

    def mutate_after_first_publication(source, destination, *args, **kwargs):
        nonlocal mutated
        source_path = _replace_operand(source, kwargs.get("src_dir_fd"))
        destination_path = _replace_operand(
            destination, kwargs.get("dst_dir_fd")
        )
        result = real_replace(source, destination, *args, **kwargs)
        if (
            not mutated
            and source_path == stage / PRODUCTS[0]
            and destination_path == output / PRODUCTS[0]
        ):
            with destination_path.open("r+b") as handle:
                handle.seek(0)
                handle.write(b"evil")
                handle.flush()
                os.fsync(handle.fileno())
            mutated = True
        return result

    monkeypatch.setattr(
        publication.os, "replace", mutate_after_first_publication
    )

    with pytest.raises(
        publication.PairPublicationError,
        match="content changed during publication",
    ):
        publication.publish_pair(
            stage,
            output,
            PRODUCTS,
            label="playoff",
            manifest_table="games",
        )

    assert _read_pair(output) == old
    assert list(tmp_path.glob(".playoff-rollback-*")) == []


def test_publish_pair_rejects_same_inode_stage_mutation_before_any_move(
    monkeypatch, tmp_path
):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _write_bound_stage(stage, b"new parquet bytes")
    old = _seed_pair(output, "old")
    real_replace = os.replace
    replace_calls = 0

    def count_replace(*args, **kwargs):
        nonlocal replace_calls
        replace_calls += 1
        return real_replace(*args, **kwargs)

    def mutate_stage_in_place():
        with (stage / PRODUCTS[0]).open("r+b") as handle:
            handle.seek(0)
            handle.write(b"evil")
            handle.flush()
            os.fsync(handle.fileno())

    monkeypatch.setattr(publication.os, "replace", count_replace)

    with pytest.raises(
        publication.PairPublicationError,
        match="content changed during publication",
    ):
        publication.publish_pair(
            stage,
            output,
            PRODUCTS,
            label="playoff",
            manifest_table="games",
            precommit=mutate_stage_in_place,
        )

    assert replace_calls == 0
    assert _read_pair(output) == old
    assert list(tmp_path.glob(".playoff-rollback-*")) == []


def test_publish_pair_rejects_manifest_that_does_not_bind_staged_product(
    tmp_path,
):
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    _write_bound_stage(stage, b"new parquet bytes")
    manifest = json.loads((stage / PRODUCTS[1]).read_text(encoding="utf-8"))
    manifest["tables"]["games"]["sha256"] = "0" * 64
    (stage / PRODUCTS[1]).write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    old = _seed_pair(output, "old")

    with pytest.raises(
        publication.PairPublicationError,
        match="manifest does not bind published Parquet",
    ):
        publication.publish_pair(
            stage,
            output,
            PRODUCTS,
            label="playoff",
            manifest_table="games",
        )

    assert _read_pair(output) == old
