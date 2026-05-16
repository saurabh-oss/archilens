"""
Git utilities for reading repository state at specific refs.

All extraction happens via GitPython's blob object database so the
working tree is never modified — safe to run during active development,
in CI, or on read-only checkouts.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


@contextmanager
def checkout_ref(
    repo_path: Path,
    ref: str,
) -> Generator[Path, None, None]:
    """
    Context manager that makes a git ref's tree available as a real directory.

    Reads every blob in the commit tree via GitPython's object-database API
    and writes them to a temporary directory, then cleans up on exit.
    The working tree is never touched.

    Usage::

        with checkout_ref(repo_path, "v1.2.3") as tmp:
            snapshot = analyze_repository(tmp, config, git_ref="v1.2.3")

    Raises:
        RuntimeError: If gitpython is not installed or the ref cannot be resolved.
    """
    try:
        import git  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("gitpython is required for multi-ref diff. Install with: pip install gitpython") from exc

    try:
        repo = git.Repo(repo_path, search_parent_directories=True)
    except git.InvalidGitRepositoryError as exc:
        raise RuntimeError(f"{repo_path} is not a Git repository.") from exc

    try:
        commit = repo.commit(ref)
    except git.BadName as exc:
        raise RuntimeError(f"Cannot resolve git ref: {ref!r}") from exc

    with tempfile.TemporaryDirectory(prefix=f"archilens_{_safe_ref(ref)}_") as tmpdir:
        tmp_path = Path(tmpdir)
        _extract_tree(commit.tree, tmp_path)
        logger.debug("Extracted %s to %s", ref, tmp_path)
        yield tmp_path


def resolve_ref(repo_path: Path, ref: str) -> str | None:
    """Return the full commit SHA for *ref*, or None if it cannot be resolved."""
    try:
        import git  # type: ignore[import]

        repo = git.Repo(repo_path, search_parent_directories=True)
        return repo.commit(ref).hexsha
    except Exception:
        return None


def get_tags(repo_path: Path) -> list[str]:
    """Return all tag names sorted oldest-to-newest by commit date."""
    try:
        import git  # type: ignore[import]

        repo = git.Repo(repo_path, search_parent_directories=True)
        tags = sorted(
            repo.tags,
            key=lambda t: t.commit.committed_date,
        )
        return [t.name for t in tags]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_tree(tree, dest: Path) -> None:
    """Recursively write all blobs in *tree* under *dest*."""
    for item in tree:
        item_path = dest / item.path
        if item.type == "blob":
            item_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                item_path.write_bytes(item.data_stream.read())
            except Exception as exc:
                logger.debug("Skipping blob %s: %s", item.path, exc)
        elif item.type == "tree":
            _extract_tree(item, dest)


def _safe_ref(ref: str) -> str:
    """Convert ref to a filesystem-safe string for temp dir naming."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in ref)[:32]
