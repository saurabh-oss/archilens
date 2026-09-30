"""Utility functions for ArchiLens."""

from __future__ import annotations

import fnmatch


def module_matches(module_path: str, pattern: str) -> bool:
    """
    Check a module path (e.g. ``src/orders``) against a config glob.

    Module paths are directories, so a pattern ending in ``/**`` also matches
    the directory itself: ``src/orders/**`` matches ``src/orders`` as well as
    anything below it. ``*`` and ``**`` both match across ``/``.

    Matching is case-sensitive and separator-normalised, so the same config
    behaves identically on Windows and Linux.
    """
    path = module_path.replace("\\", "/").strip("/")
    pat = pattern.replace("\\", "/").rstrip("/")

    candidates = [pat]
    if pat.endswith("/**"):
        candidates.append(pat[: -len("/**")])

    return any(fnmatch.fnmatchcase(path, c.replace("**", "*")) for c in candidates)
