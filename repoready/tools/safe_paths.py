"""
repoready/tools/safe_paths.py

Central path-safety helper shared by all tools.

Security guarantees (per CLAUDE.md):
- Rejects absolute paths supplied by the caller.
- Rejects any path component that is ".." (traversal attempt).
- Resolves the final path and asserts it remains inside ctx.root.
- Rejects symlinks — never follow a symlink inside the repo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from repoready.context import RepoContext


def resolve_repo_path(ctx: RepoContext, relative_path: str) -> Optional[Path]:
    """Safely resolve *relative_path* against the repository root.

    Args:
        ctx:           Active RepoContext (provides the trusted root).
        relative_path: Caller-supplied relative path to a file or directory
                       inside the repo.

    Returns:
        A resolved absolute :class:`~pathlib.Path` that is guaranteed to sit
        inside ``ctx.root``, or ``None`` if the path fails any safety check.

    Safety checks (in order):
    1. Reject absolute paths.
    2. Reject any path component equal to ``".."``.
    3. Resolve the joined path and confirm it is a descendant of ``ctx.root``.
    4. Reject symlinks.
    """
    # 1. Reject absolute paths.
    if Path(relative_path).is_absolute():
        return None

    # 2. Reject ".." components anywhere in the path.
    parts = Path(relative_path).parts
    if ".." in parts:
        return None

    candidate = (ctx.root / relative_path).resolve()
    root_resolved = ctx.root.resolve()

    # 3. Confirm the resolved path is inside ctx.root.
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None

    # 4. Reject symlinks.
    if candidate.is_symlink():
        return None

    return candidate
