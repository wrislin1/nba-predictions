import fcntl
import os
from pathlib import Path
import subprocess
import threading
import time

import pytest

from nba_data import publication


ROOT = Path(__file__).resolve().parents[1]
LOCK_HELPER = ROOT / "scripts/publication_read_lock.sh"


def seed_manifest(tmp_path: Path) -> tuple[Path, Path]:
    manifest = tmp_path / "fixture/data/parquet/build_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}\n", encoding="utf-8")
    return manifest, manifest.parents[1] / ".parquet-publication.lock"


def start_lock_client(
    manifest: Path,
    entered: Path,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            "bash",
            "-c",
            """
set -euo pipefail
source "$1"
acquire_publication_read_locks "$2"
: > "$3"
release_publication_read_locks
""",
            "publication-lock-test",
            str(LOCK_HELPER),
            str(manifest),
            str(entered),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def start_persistent_lock_client(
    manifest: Path,
    entered: Path,
    release_gate: Path,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            "bash",
            "-c",
            """
set -euo pipefail
source "$1"
acquire_publication_read_locks "$2"
: > "$3"
while test ! -e "$4"; do sleep 0.01; done
release_publication_read_locks
""",
            "publication-lock-test",
            str(LOCK_HELPER),
            str(manifest),
            str(entered),
            str(release_gate),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_for_flock_waiter(lock_path: Path, timeout: float = 5) -> None:
    inode = lock_path.stat().st_ino
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        locks = Path("/proc/locks").read_text(encoding="utf-8")
        if any(
            "->" in line
            and " FLOCK " in line
            and " READ " in line
            and f":{inode} " in line
            for line in locks.splitlines()
        ):
            return
        time.sleep(0.01)
    raise AssertionError("shared publication-lock waiter did not appear")


def test_helper_uses_manifest_data_root_and_blocks_for_publisher(
    tmp_path: Path,
) -> None:
    """Would fail if a shell reader used another lock or did not flock it."""
    manifest, lock_path = seed_manifest(tmp_path)
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    entered = tmp_path / "entered"
    client = start_lock_client(manifest, entered)
    try:
        wait_for_flock_waiter(lock_path)
        assert client.poll() is None
        assert not entered.exists()
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        stdout, stderr = client.communicate(timeout=5)
    finally:
        os.close(lock_fd)
        if client.poll() is None:
            client.kill()
            client.wait(timeout=5)

    assert client.returncode == 0, f"stdout={stdout!r}\nstderr={stderr!r}"
    assert entered.is_file()


def test_helper_forgets_closed_handshake_descriptor(tmp_path: Path) -> None:
    """Would fail if release could later close a reused descriptor number."""
    manifest, _ = seed_manifest(tmp_path)

    completed = subprocess.run(
        [
            "bash",
            "-c",
            """
set -euo pipefail
source "$1"
acquire_publication_read_locks "$2"
test -z "${NBA_PUBLICATION_READ_LOCK_READ_FD:-}"
release_publication_read_locks
""",
            "publication-lock-test",
            str(LOCK_HELPER),
            str(manifest),
        ],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_helper_rejects_lock_path_replaced_while_waiting(
    tmp_path: Path,
) -> None:
    """Would fail if the holder entered through an unlinked lock inode."""
    manifest, lock_path = seed_manifest(tmp_path)
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    entered = tmp_path / "entered"
    client = start_lock_client(manifest, entered)
    try:
        wait_for_flock_waiter(lock_path)
        replacement = tmp_path / "replacement-lock"
        replacement.write_bytes(b"")
        os.replace(replacement, lock_path)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        stdout, stderr = client.communicate(timeout=5)
    finally:
        os.close(lock_fd)
        if client.poll() is None:
            client.kill()
            client.wait(timeout=5)

    assert client.returncode != 0, stdout
    assert "publication lock changed while waiting" in stderr
    assert not entered.exists()


def test_helper_rejects_data_root_replaced_while_waiting(
    tmp_path: Path,
) -> None:
    """Would fail if the holder lost its binding to the manifest data root."""
    manifest, lock_path = seed_manifest(tmp_path)
    data_root = manifest.parents[1]
    moved_root = data_root.with_name("data-moved")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    entered = tmp_path / "entered"
    client = start_lock_client(manifest, entered)
    try:
        wait_for_flock_waiter(lock_path)
        os.replace(data_root, moved_root)
        data_root.mkdir()
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        stdout, stderr = client.communicate(timeout=5)
    finally:
        os.close(lock_fd)
        if client.poll() is None:
            client.kill()
            client.wait(timeout=5)

    assert client.returncode != 0, stdout
    assert "publication data root changed while waiting" in stderr
    assert not entered.exists()


def test_replacement_after_ready_cannot_bypass_shared_reader(
    tmp_path: Path,
) -> None:
    """Would fail if a publisher could lock a replacement sentinel."""
    manifest, lock_path = seed_manifest(tmp_path)
    entered = tmp_path / "entered"
    release_gate = tmp_path / "release"
    client = start_persistent_lock_client(
        manifest, entered, release_gate
    )
    publisher_acquired = threading.Event()

    def publish_observer() -> None:
        with publication.publication_lock(
            manifest.parent, exclusive=True
        ):
            publisher_acquired.set()

    try:
        deadline = time.monotonic() + 5
        while not entered.exists() and time.monotonic() < deadline:
            assert client.poll() is None
            time.sleep(0.01)
        assert entered.is_file()
        replacement = tmp_path / "replacement-lock"
        replacement.write_bytes(b"")
        os.replace(replacement, lock_path)
        publisher = threading.Thread(
            target=publish_observer, daemon=True
        )
        publisher.start()
        assert not publisher_acquired.wait(0.2)
        release_gate.touch()
        stdout, stderr = client.communicate(timeout=5)
        assert publisher_acquired.wait(5)
        publisher.join(5)
    finally:
        release_gate.touch(exist_ok=True)
        if client.poll() is None:
            client.kill()
            client.wait(timeout=5)

    assert client.returncode != 0, stdout
    assert "publication lock changed while held" in stderr


def test_data_root_replacement_after_ready_cannot_bypass_shared_reader(
    tmp_path: Path,
) -> None:
    """Would fail if replacing the guarded data inode split the protocol."""
    manifest, _ = seed_manifest(tmp_path)
    data_root = manifest.parents[1]
    moved_root = tmp_path / "data-moved"
    entered = tmp_path / "entered"
    release_gate = tmp_path / "release"
    client = start_persistent_lock_client(
        manifest, entered, release_gate
    )
    publisher_acquired = threading.Event()

    def publish_observer() -> None:
        with publication.publication_lock(
            manifest.parent, exclusive=True
        ):
            publisher_acquired.set()

    try:
        deadline = time.monotonic() + 5
        while not entered.exists() and time.monotonic() < deadline:
            assert client.poll() is None
            time.sleep(0.01)
        assert entered.is_file()
        os.replace(data_root, moved_root)
        data_root.mkdir()
        publisher = threading.Thread(
            target=publish_observer, daemon=True
        )
        publisher.start()
        assert not publisher_acquired.wait(0.2)
        release_gate.touch()
        stdout, stderr = client.communicate(timeout=5)
        assert publisher_acquired.wait(5)
        publisher.join(5)
    finally:
        release_gate.touch(exist_ok=True)
        if client.poll() is None:
            client.kill()
            client.wait(timeout=5)

    assert client.returncode != 0, stdout
    assert "publication data root changed while held" in stderr


@pytest.mark.parametrize("kind", ["directory", "symlink", "fifo"])
def test_helper_rejects_non_regular_lock_path(
    tmp_path: Path,
    kind: str,
) -> None:
    """Would fail if the holder followed or blocked on an unsafe lock path."""
    manifest, lock_path = seed_manifest(tmp_path)
    if kind == "directory":
        lock_path.mkdir()
    elif kind == "symlink":
        lock_path.symlink_to(tmp_path / "missing-lock-target")
    else:
        os.mkfifo(lock_path)
    entered = tmp_path / "entered"

    completed = subprocess.run(
        [
            "bash",
            "-c",
            """
set -euo pipefail
source "$1"
acquire_publication_read_locks "$2"
: > "$3"
release_publication_read_locks
""",
            "publication-lock-test",
            str(LOCK_HELPER),
            str(manifest),
            str(entered),
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode != 0
    assert "publication lock must be a regular file" in completed.stderr
    assert not entered.exists()
