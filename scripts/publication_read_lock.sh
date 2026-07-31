# Shared publication-reader locking for shell archive and restore commands.

NBA_PUBLICATION_READ_LOCK_PID=
NBA_PUBLICATION_READ_LOCK_READ_FD=
NBA_PUBLICATION_READ_LOCK_WRITE_FD=

_nba_publication_data_root() {
  local manifest="$1"
  local manifest_directory
  local data_root

  manifest_directory="$(dirname -- "$manifest")"
  if ! data_root="$(
    cd "$manifest_directory/.." 2>/dev/null && pwd -P
  )"; then
    printf 'cannot derive publication data root from manifest: %s\n' \
      "$manifest" >&2
    return 1
  fi
  printf '%s\n' "$data_root"
}

_nba_publication_read_lock_holder() {
  python3 -c '
import fcntl
import os
import stat
import sys


def abort(message, path, error=None):
    detail = "" if error is None else f": {error}"
    print(f"{message}: {path}{detail}", file=sys.stderr)
    raise SystemExit(1)


if not hasattr(os, "O_NOFOLLOW"):
    abort("publication lock requires no-follow support", sys.argv[1])

anchor_descriptors = []
data_descriptors = []
descriptors = []
anchor_bindings = []
bindings = []


def open_bound_directory(path, message):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        observed = os.lstat(path)
    except OSError as error:
        if "descriptor" in locals():
            os.close(descriptor)
        abort(message, path, error)
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(observed.st_mode)
        or (opened.st_dev, opened.st_ino, opened.st_mode)
        != (observed.st_dev, observed.st_ino, observed.st_mode)
    ):
        os.close(descriptor)
        abort(message, path)
    return descriptor, opened


def require_directory(path, expected, message):
    try:
        observed = os.lstat(path)
    except OSError as error:
        abort(message, path, error)
    if (
        not stat.S_ISDIR(observed.st_mode)
        or (expected.st_dev, expected.st_ino, expected.st_mode)
        != (observed.st_dev, observed.st_ino, observed.st_mode)
    ):
        abort(message, path)


try:
    data_roots = sys.argv[1:]
    trust_anchors = sorted({os.path.dirname(path) for path in data_roots})
    # The repository/fixture root is the protocol trust anchor.  Every
    # compliant Python and shell participant locks it before the data root.
    for trust_anchor in trust_anchors:
        anchor_descriptor, opened_anchor = open_bound_directory(
            trust_anchor,
            "publication trust anchor must be a directory",
        )
        anchor_descriptors.append(anchor_descriptor)
        fcntl.flock(anchor_descriptor, fcntl.LOCK_SH)
        require_directory(
            trust_anchor,
            opened_anchor,
            "publication trust anchor changed while waiting",
        )
        anchor_bindings.append((trust_anchor, opened_anchor))

    for data_root in data_roots:
        path = os.path.join(data_root, ".parquet-publication.lock")
        directory_descriptor, opened_directory = open_bound_directory(
            data_root,
            "publication data root must be a directory",
        )
        data_descriptors.append(directory_descriptor)
        fcntl.flock(directory_descriptor, fcntl.LOCK_SH)
        require_directory(
            data_root,
            opened_directory,
            "publication data root changed while waiting",
        )

        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            descriptor = os.open(
                ".parquet-publication.lock",
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
        except OSError as error:
            abort("publication lock must be a regular file", path, error)
        descriptors.append(descriptor)

        opened = os.fstat(descriptor)
        try:
            observed = os.stat(
                ".parquet-publication.lock",
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except OSError as error:
            abort("publication lock changed before waiting", path, error)
        if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(
            observed.st_mode
        ):
            abort("publication lock must be a regular file", path)
        if (opened.st_dev, opened.st_ino) != (
            observed.st_dev,
            observed.st_ino,
        ):
            abort("publication lock changed before waiting", path)

        fcntl.flock(descriptor, fcntl.LOCK_SH)
        try:
            observed = os.stat(
                ".parquet-publication.lock",
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except OSError as error:
            abort("publication lock changed while waiting", path, error)
        if (
            not stat.S_ISREG(observed.st_mode)
            or (opened.st_dev, opened.st_ino)
            != (observed.st_dev, observed.st_ino)
        ):
            abort("publication lock changed while waiting", path)
        require_directory(
            data_root,
            opened_directory,
            "publication data root changed while waiting",
        )
        bindings.append((
            data_root,
            directory_descriptor,
            opened_directory,
            path,
            descriptor,
            opened,
        ))

    print("READY", flush=True)
    while sys.stdin.buffer.read(8192):
        pass
    for (
        data_root,
        directory_descriptor,
        opened_directory,
        path,
        descriptor,
        opened,
    ) in bindings:
        try:
            observed = os.stat(
                ".parquet-publication.lock",
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except OSError as error:
            abort("publication lock changed while held", path, error)
        if (
            not stat.S_ISREG(observed.st_mode)
            or (opened.st_dev, opened.st_ino)
            != (observed.st_dev, observed.st_ino)
        ):
            abort("publication lock changed while held", path)
        require_directory(
            data_root,
            opened_directory,
            "publication data root changed while held",
        )
    for trust_anchor, opened_anchor in anchor_bindings:
        require_directory(
            trust_anchor,
            opened_anchor,
            "publication trust anchor changed while held",
        )
finally:
    for descriptor in reversed(descriptors):
        os.close(descriptor)
    for descriptor in reversed(data_descriptors):
        os.close(descriptor)
    for descriptor in reversed(anchor_descriptors):
        os.close(descriptor)
' "$@"
}

capture_publication_manifest() {
  local manifest="$1"
  local template="${2:-${TMPDIR:-/tmp}/.publication-manifest.XXXXXX}"
  local destination

  destination="$(mktemp "$template")" || return
  if ! python3 -c '
import os
import stat
import sys


def fail(message, error=None):
    detail = "" if error is None else f": {error}"
    print(f"{message}: {sys.argv[1]}{detail}", file=sys.stderr)
    raise SystemExit(1)


source_fd = None
destination_fd = None
try:
    if not hasattr(os, "O_NOFOLLOW"):
        fail("publication manifest capture requires no-follow support")
    before = os.lstat(sys.argv[1])
    destination_before = os.lstat(sys.argv[2])
    if not stat.S_ISREG(before.st_mode):
        fail("publication manifest must be a regular file")
    if not stat.S_ISREG(destination_before.st_mode):
        fail("publication manifest destination must be a regular file")
    source_fd = os.open(
        sys.argv[1],
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    opened = os.fstat(source_fd)
    identity = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    if (
        not stat.S_ISREG(opened.st_mode)
        or (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        ) != identity
    ):
        fail("publication manifest changed while opening")
    blocks = []
    while True:
        block = os.read(source_fd, 1024 * 1024)
        if not block:
            break
        blocks.append(block)
    payload = b"".join(blocks)
    os.lseek(source_fd, 0, os.SEEK_SET)
    repeated = []
    while True:
        block = os.read(source_fd, 1024 * 1024)
        if not block:
            break
        repeated.append(block)
    after = os.lstat(sys.argv[1])
    final_opened = os.fstat(source_fd)
    if (
        b"".join(repeated) != payload
        or (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) != identity
        or (
            final_opened.st_dev,
            final_opened.st_ino,
            final_opened.st_mode,
            final_opened.st_size,
            final_opened.st_mtime_ns,
            final_opened.st_ctime_ns,
        ) != identity
    ):
        fail("publication manifest changed while capturing")
    destination_fd = os.open(
        sys.argv[2],
        os.O_WRONLY | os.O_TRUNC | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    destination_stat = os.fstat(destination_fd)
    if (
        not stat.S_ISREG(destination_stat.st_mode)
        or (
            destination_stat.st_dev,
            destination_stat.st_ino,
            destination_stat.st_mode,
        ) != (
            destination_before.st_dev,
            destination_before.st_ino,
            destination_before.st_mode,
        )
    ):
        fail("publication manifest destination must be a regular file")
    remaining = memoryview(payload)
    while remaining:
        written = os.write(destination_fd, remaining)
        remaining = remaining[written:]
    os.fsync(destination_fd)
except OSError as error:
    fail("could not capture publication manifest", error)
finally:
    if source_fd is not None:
        os.close(source_fd)
    if destination_fd is not None:
        os.close(destination_fd)
' "$manifest" "$destination"; then
    rm -f -- "$destination"
    return 1
  fi
  if ! chmod 400 "$destination"; then
    rm -f -- "$destination"
    return 1
  fi
  printf '%s\n' "$destination"
}

acquire_publication_read_locks() {
  local manifest
  local data_root
  local ready
  local holder_read_fd
  local holder_write_fd
  local -a data_roots=()
  local -a sorted_roots=()

  test "$#" -gt 0 || {
    printf 'at least one publication manifest is required\n' >&2
    return 2
  }
  test -z "${NBA_PUBLICATION_READ_LOCK_PID:-}" || {
    printf 'publication read lock is already held by this shell\n' >&2
    return 2
  }

  for manifest in "$@"; do
    data_root="$(_nba_publication_data_root "$manifest")" || return
    data_roots+=("$data_root")
  done
  while IFS= read -r -d '' data_root; do
    sorted_roots+=("$data_root")
  done < <(
    printf '%s\0' "${data_roots[@]}" | LC_ALL=C sort -zu
  )
  coproc NBA_PUBLICATION_READ_LOCK_HOLDER {
    _nba_publication_read_lock_holder "${sorted_roots[@]}"
  }
  NBA_PUBLICATION_READ_LOCK_PID="$!"
  holder_read_fd="${NBA_PUBLICATION_READ_LOCK_HOLDER[0]}"
  holder_write_fd="${NBA_PUBLICATION_READ_LOCK_HOLDER[1]}"
  NBA_PUBLICATION_READ_LOCK_READ_FD="$holder_read_fd"
  NBA_PUBLICATION_READ_LOCK_WRITE_FD="$holder_write_fd"

  if ! IFS= read -r ready <&"$holder_read_fd"; then
    release_publication_read_locks || true
    return 1
  fi
  exec {NBA_PUBLICATION_READ_LOCK_READ_FD}<&-
  NBA_PUBLICATION_READ_LOCK_READ_FD=
  test "$ready" = READY || {
    printf 'publication read lock holder returned an invalid handshake\n' >&2
    release_publication_read_locks || true
    return 1
  }
}

release_publication_read_locks() {
  local holder_pid="${NBA_PUBLICATION_READ_LOCK_PID:-}"
  local status=0

  if test -n "${NBA_PUBLICATION_READ_LOCK_READ_FD:-}"; then
    exec {NBA_PUBLICATION_READ_LOCK_READ_FD}<&- || true
  fi
  if test -n "${NBA_PUBLICATION_READ_LOCK_WRITE_FD:-}"; then
    exec {NBA_PUBLICATION_READ_LOCK_WRITE_FD}>&- || true
  fi
  if test -n "$holder_pid"; then
    wait "$holder_pid" || status="$?"
  fi
  NBA_PUBLICATION_READ_LOCK_PID=
  NBA_PUBLICATION_READ_LOCK_READ_FD=
  NBA_PUBLICATION_READ_LOCK_WRITE_FD=
  return "$status"
}
