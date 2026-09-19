"""
repoready/tools/read_file.py

Safe, read-only file reader for analyzed repository files.

Security guarantees (per CLAUDE.md):
- All path access goes through resolve_repo_path(), which rejects traversal,
  absolute paths, and symlinks.
- Binary files are detected (NUL byte check) and their content is never returned.
- Content is strictly capped at max_bytes to prevent memory exhaustion.
- No code from the repo is ever executed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repoready.context import RepoContext
from repoready.tools.safe_paths import resolve_repo_path

# How many bytes to read at a time when probing for binary content.
_BINARY_PROBE_BYTES = 8192


def _is_binary(data: bytes) -> bool:
    """Return True if *data* contains a NUL byte (binary file heuristic)."""
    return b"\x00" in data


def _read_file_internal(
    ctx: RepoContext,
    path: str,
    max_bytes: int = 60_000,
) -> dict[str, Any]:
    """Core file-reading logic with no side-effects (no logging, no counting).

    Used internally by project_config and other helpers so they don't
    pollute ctx.tool_calls or ctx.activity.
    """
    # Normalise Windows backslashes so both "src\\file.py" and "src/file.py"
    # are accepted as input, while all safety checks still apply.
    path = path.replace("\\", "/")

    resolved = resolve_repo_path(ctx, path)

    # --- Safety check failed ---
    if resolved is None:
        return {"path": path, "exists": False, "error": "path not allowed"}

    # --- File does not exist ---
    if not resolved.exists() or not resolved.is_file():
        return {"path": path, "exists": False}

    # --- Read raw bytes ---
    try:
        raw = resolved.read_bytes()
    except (PermissionError, OSError):
        return {"path": path, "exists": False, "error": "could not read file"}

    # --- Binary detection ---
    probe = raw[:_BINARY_PROBE_BYTES]
    if _is_binary(probe):
        return {"path": path, "exists": True, "binary": True}

    # --- Truncate to max_bytes ---
    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]

    # --- Decode as UTF-8, replacing unrecognised bytes ---
    content = raw.decode("utf-8", errors="replace")
    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)

    return {
        "path": path,
        "exists": True,
        "binary": False,
        "content": content,
        "truncated": truncated,
        "line_count": line_count,
    }


def read_file(
    ctx: RepoContext,
    path: str,
    max_bytes: int = 60_000,
) -> dict[str, Any]:
    """Safely read a text file from the analyzed repository.

    This is the **public tool** — it appends one activity line to
    ``ctx.activity`` per invocation. Internal helpers (e.g. inside
    project_config) must use :func:`_read_file_internal` instead so they do not
    pollute the log.

    Args:
        ctx:       Active :class:`~repoready.context.RepoContext`.
        path:      Relative path to the file inside the repo root.
        max_bytes: Maximum number of bytes to return as content (default 60 000).
                   Content is silently truncated at this boundary.

    Returns:
        A dict with one of the following shapes:

        File not found::

            {"path": path, "exists": False}

        Unsafe / disallowed path::

            {"path": path, "exists": False, "error": "path not allowed"}

        Binary file::

            {"path": path, "exists": True, "binary": True}

        Text file (possibly truncated)::

            {
                "path": path,
                "exists": True,
                "binary": False,
                "content": str,
                "truncated": bool,
                "line_count": int,
            }

    Never executes any content from the file.
    """
    result = _read_file_internal(ctx, path, max_bytes)

    # Log one concise activity line — only for files that actually exist.
    if result.get("exists"):
        if result.get("binary"):
            ctx.log(f"Read {path} (binary, skipped)")
        else:
            ctx.log(f"Read {path}")

    return result
