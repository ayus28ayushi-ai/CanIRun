"""
repoready/tools/inspect_repository.py

Deterministic repository structure inspector.

Returns a compact snapshot of what actually exists in the repo — no code is
ever executed; all access is read-only via pathlib.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from repoready.context import RepoContext

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_TREE_DEPTH = 3
_TREE_MAX_ENTRIES = 150

# Files to look for (exact filenames, checked case-insensitively).
_KEY_FILE_NAMES: list[str] = [
    # README variants — handled separately (case-insensitive prefix match)
    # Node
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    # Python
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "poetry.lock",
    # Docker
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    # Environment templates
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.dist",
    # Runtime version pins
    ".nvmrc",
    ".node-version",
    ".python-version",
    ".tool-versions",
    # Build
    "Makefile",
]

_README_STEMS = {"readme"}  # case-insensitive prefix
_README_SUFFIXES = {".md", ".rst", ".txt", ""}

# Project-type signals
_NODE_SIGNALS = {"package.json"}
_PYTHON_SIGNALS = {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile"}
_DOCKER_SIGNALS = {"Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}

# Source file extensions to count
_SOURCE_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".py"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_tree(root: Path, max_depth: int, max_entries: int) -> tuple[list[str], bool]:
    """Walk *root* up to *max_depth* levels and return a compact listing.

    Each entry is a string like:
        ``dir/subdir/`` for directories
        ``dir/subdir/file.txt`` for files

    Returns:
        (entries, truncated) — entries is a list of relative-path strings;
        truncated is True if the walk was cut short due to *max_entries*.
    """
    entries: list[str] = []
    truncated = False

    # os.walk is depth-first; we control depth via the relative path parts.
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        depth = len(rel_dir.parts)

        if depth >= max_depth:
            # Don't descend further — clear dirnames in-place to prune os.walk.
            dirnames.clear()

        # Record this directory (skip root itself).
        if depth > 0:
            entry = rel_dir.as_posix() + "/"
            entries.append(entry)
            if len(entries) >= max_entries:
                truncated = True
                return entries, truncated

        # Record files in this directory.
        for fname in sorted(filenames):
            entry = (rel_dir / fname).as_posix() if depth > 0 else fname
            entries.append(entry)
            if len(entries) >= max_entries:
                truncated = True
                return entries, truncated

        # Sort subdirectories for a deterministic order.
        dirnames.sort()

    return entries, truncated


def _find_key_files(root: Path) -> dict[str, str]:
    """Return a mapping of canonical name -> relative path for each key file found.

    README variants are detected by case-insensitive stem + known suffix.
    All other key files are detected by case-insensitive filename comparison
    against a flat scan of the root directory only (no deep search).
    """
    found: dict[str, str] = {}

    # Build a lookup: lower-case name -> actual relative path (root-level only).
    root_files: dict[str, Path] = {}
    try:
        for entry in root.iterdir():
            if entry.is_file() and not entry.is_symlink():
                root_files[entry.name.lower()] = entry
    except PermissionError:
        pass

    # README variants
    for name_lower, path in root_files.items():
        stem = Path(name_lower).stem
        suffix = Path(name_lower).suffix
        if stem in _README_STEMS and suffix in _README_SUFFIXES:
            found[path.name] = path.name  # canonical = actual filename

    # Other key files (case-insensitive)
    for canonical in _KEY_FILE_NAMES:
        if canonical.lower() in root_files:
            actual = root_files[canonical.lower()]
            found[canonical] = actual.name

    return found


def _detect_project_types(key_files: dict[str, str]) -> list[str]:
    """Infer project types from the presence of known key files."""
    found_lower = {k.lower() for k in key_files}
    types: list[str] = []
    if found_lower & {s.lower() for s in _NODE_SIGNALS}:
        types.append("node")
    if found_lower & {s.lower() for s in _PYTHON_SIGNALS}:
        types.append("python")
    if found_lower & {s.lower() for s in _DOCKER_SIGNALS}:
        types.append("docker")
    return types


def _count_source_files(root: Path) -> dict[str, int]:
    """Count source files by extension across the whole tree."""
    counts: dict[str, int] = {ext: 0 for ext in _SOURCE_EXTENSIONS}
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            ext = path.suffix.lower()
            if ext in counts:
                counts[ext] += 1
    return counts


# ---------------------------------------------------------------------------
# Public tool
# ---------------------------------------------------------------------------

def inspect_repository(ctx: RepoContext) -> dict[str, Any]:
    """Return a deterministic snapshot of the repository structure.

    Never executes any repo code.  All access is read-only.

    Args:
        ctx: Active :class:`~repoready.context.RepoContext`.

    Returns:
        A dict with keys:

        ``tree``
            List of relative-path strings (dirs end with ``/``), capped at
            ~150 entries across at most 3 levels of depth.

        ``truncated``
            ``True`` if the tree was cut short.

        ``key_files``
            Dict mapping canonical filename → actual filename for every key
            file found in the repository root.

        ``project_types``
            Subset of ``["node", "python", "docker"]`` inferred from key files.

        ``source_file_counts``
            Dict mapping source extension → integer count across the full tree.
    """
    tree, truncated = _build_tree(ctx.root, _TREE_DEPTH, _TREE_MAX_ENTRIES)
    key_files = _find_key_files(ctx.root)
    project_types = _detect_project_types(key_files)
    source_file_counts = _count_source_files(ctx.root)

    ctx.log("Inspected repository structure")

    return {
        "tree": tree,
        "truncated": truncated,
        "key_files": key_files,
        "project_types": project_types,
        "source_file_counts": source_file_counts,
    }
