#!/usr/bin/env python3
"""Compare two JSON manifests and allow only changes under named prefixes.

Usage::

    python scripts/diff_manifest_allowlist.py --before A --after B [--allow PREFIX ...]

Every leaf of both documents is addressed by a dotted path (object keys
joined with ``.``, list indices rendered ``[i]``).  Leaves whose values differ
or that exist on one side only are reported.  A change is allowed when its
path equals an ``--allow`` prefix or starts with that prefix followed by
``.``; the process exits 0 when every change is allowed (printing each as
``allowed: <path>``) and 1 otherwise (printing ``disallowed: <path>`` for
each change outside the allow-list).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


_MISSING = object()


def flatten(document: object, prefix: str = "") -> dict[str, object]:
    """Map every leaf of ``document`` to its dotted path.

    Empty objects and empty lists are leaves of their own so that a container
    appearing or disappearing is visible as a change.
    """
    if isinstance(document, dict):
        if not document:
            return {prefix: document}
        leaves: dict[str, object] = {}
        for key, value in document.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            leaves.update(flatten(value, path))
        return leaves
    if isinstance(document, list):
        if not document:
            return {prefix: document}
        leaves = {}
        for index, value in enumerate(document):
            leaves.update(flatten(value, f"{prefix}[{index}]"))
        return leaves
    return {prefix: document}


def differing_paths(before: dict, after: dict) -> list[str]:
    """Sorted leaf paths whose values differ or exist on one side only."""
    before_leaves = flatten(before)
    after_leaves = flatten(after)
    differing = []
    for path in set(before_leaves) | set(after_leaves):
        left = before_leaves.get(path, _MISSING)
        right = after_leaves.get(path, _MISSING)
        if left is _MISSING or right is _MISSING or not _same_leaf(left, right):
            differing.append(path)
    return sorted(differing)


def _same_leaf(left: object, right: object) -> bool:
    # ``1 == True`` and ``1 == 1.0`` in Python; a manifest that changes a
    # value's JSON type has changed, so compare types as well as values.
    return type(left) is type(right) and left == right


def _allowed(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + ".")


def check(before: dict, after: dict, allowed: Sequence[str]) -> list[str]:
    """Differing paths that are not under any allowed prefix."""
    prefixes = tuple(allowed)
    return [
        path
        for path in differing_paths(before, after)
        if not any(_allowed(path, prefix) for prefix in prefixes)
    ]


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key in JSON object: {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> object:
    raise ValueError(f"non-finite JSON constant is not allowed: {name}")


def load_document(text: str) -> dict:
    """Parse a manifest, rejecting duplicate keys and non-object documents."""
    document = json.loads(
        text,
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(document, dict):
        raise ValueError(
            f"manifest must be a JSON object, got {type(document).__name__}"
        )
    return document


def _read_manifest(path: Path) -> dict:
    try:
        return load_document(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SystemExit(f"ERROR: cannot load {path}: {exc}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Allow only manifest changes under the given prefixes."
    )
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="PREFIX",
        help="dotted path prefix under which changes are allowed (repeatable)",
    )
    args = parser.parse_args(argv)

    before = _read_manifest(args.before)
    after = _read_manifest(args.after)

    disallowed = check(before, after, args.allow)
    if disallowed:
        for path in disallowed:
            print(f"disallowed: {path}")
        return 1
    for path in differing_paths(before, after):
        print(f"allowed: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
