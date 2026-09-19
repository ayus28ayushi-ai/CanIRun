"""
repoready/tools/project_config.py

Deterministic project configuration inspector for Node.js and Python projects.

Reads and parses package manifests using stdlib only (json, tomllib, re).
Never imports, executes, or evaluates any content from the analyzed repository.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

from repoready.context import RepoContext
from repoready.tools.read_file import _read_file_internal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NODE_LOCKFILES = [
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
]

_LOCKFILE_TO_PM = {
    "package-lock.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "bun.lockb": "bun",
}

_NODE_VERSION_FILES = [".nvmrc", ".node-version"]
_PYTHON_VERSION_FILES = [".python-version"]

_KNOWN_ENTRYPOINTS = ["main.py", "app.py", "manage.py", "wsgi.py", "run.py"]
_FRAMEWORK_IMPORTS = {
    "streamlit": re.compile(r"\bimport\s+streamlit\b|from\s+streamlit\b"),
    "flask": re.compile(r"\bimport\s+flask\b|from\s+flask\b", re.IGNORECASE),
    "fastapi": re.compile(r"\bimport\s+fastapi\b|from\s+fastapi\b", re.IGNORECASE),
    "django": re.compile(r"\bimport\s+django\b|from\s+django\b|django\.conf\b", re.IGNORECASE),
}
_MAIN_GUARD_RE = re.compile(r'if\s+__name__\s*==\s*["\']__main__["\']')

# Regex patterns for setup.py parsing (never executed — regex only)
_SETUP_PYTHON_REQUIRES_RE = re.compile(
    r"""python_requires\s*=\s*(['"])(.*?)\1""", re.DOTALL
)
_SETUP_ENTRY_POINTS_RE = re.compile(
    r"""entry_points\s*=\s*(\{.*?\})""", re.DOTALL
)

_MAX_REQUIREMENTS = 100


# ---------------------------------------------------------------------------
# Helpers — generic
# ---------------------------------------------------------------------------

def _file_content(ctx: RepoContext, relative_path: str) -> Optional[str]:
    """Return raw text content of a repo file, or None if missing/binary/unsafe."""
    result = _read_file_internal(ctx, relative_path)
    if not result.get("exists") or result.get("binary"):
        return None
    return result.get("content", "")


def _file_exists(ctx: RepoContext, relative_path: str) -> bool:
    result = _read_file_internal(ctx, relative_path)
    return bool(result.get("exists") and not result.get("binary"))


# ---------------------------------------------------------------------------
# Node.js
# ---------------------------------------------------------------------------

def _inspect_node(ctx: RepoContext) -> Optional[dict[str, Any]]:
    """Parse package.json and related files. Returns None if package.json absent."""
    pkg_content = _file_content(ctx, "package.json")
    if pkg_content is None:
        return None

    ctx.log("Inspected package.json")

    # --- Parse JSON ---
    try:
        pkg: dict = json.loads(pkg_content)
    except json.JSONDecodeError as exc:
        return {
            "valid_json": False,
            "json_error": str(exc),
        }

    result: dict[str, Any] = {"valid_json": True}

    # Scalar top-level fields
    for field in ("name", "main", "type", "packageManager"):
        val = pkg.get(field)
        if val is not None:
            result[field] = val

    # scripts
    scripts = pkg.get("scripts")
    result["scripts"] = dict(scripts) if isinstance(scripts, dict) else {}

    # dependencies / devDependencies — names only + count
    for dep_key in ("dependencies", "devDependencies"):
        raw = pkg.get(dep_key)
        if isinstance(raw, dict):
            names = sorted(raw.keys())
            result[dep_key] = {"names": names, "count": len(names)}
        else:
            result[dep_key] = {"names": [], "count": 0}

    # engines
    engines = pkg.get("engines")
    result["engines"] = dict(engines) if isinstance(engines, dict) else {}

    # bin
    bin_val = pkg.get("bin")
    if isinstance(bin_val, dict):
        result["bin"] = list(bin_val.keys())
    elif isinstance(bin_val, str):
        result["bin"] = [bin_val]
    else:
        result["bin"] = []

    # Lockfiles present
    lockfiles_found = [lf for lf in _NODE_LOCKFILES if _file_exists(ctx, lf)]
    result["lockfiles_found"] = lockfiles_found

    # Inferred package manager
    pm_field = pkg.get("packageManager", "")
    if pm_field:
        # "npm@9.0.0" -> "npm"
        inferred = pm_field.split("@")[0].strip()
    elif lockfiles_found:
        inferred = _LOCKFILE_TO_PM.get(lockfiles_found[0], "unknown")
    else:
        inferred = "unknown"
    result["inferred_package_manager"] = inferred

    # Node version files
    node_version_files: dict[str, str] = {}
    for vf in _NODE_VERSION_FILES:
        content = _file_content(ctx, vf)
        if content is not None:
            node_version_files[vf] = content.strip()
    result["node_version_files"] = node_version_files

    return result


# ---------------------------------------------------------------------------
# Python — requirements.txt
# ---------------------------------------------------------------------------

def _inspect_requirements_txt(ctx: RepoContext) -> dict[str, Any]:
    content = _file_content(ctx, "requirements.txt")
    if content is None:
        return {"exists": False}

    lines = []
    for raw in content.splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
            if len(lines) >= _MAX_REQUIREMENTS:
                break

    return {"exists": True, "requirements": lines}


# ---------------------------------------------------------------------------
# Python — pyproject.toml
# ---------------------------------------------------------------------------

def _inspect_pyproject(ctx: RepoContext) -> dict[str, Any]:
    content = _file_content(ctx, "pyproject.toml")
    if content is None:
        return {"exists": False}

    # tomllib available in stdlib from Python 3.11; fall back gracefully.
    try:
        if sys.version_info >= (3, 11):
            import tomllib  # type: ignore[import]
            data = tomllib.loads(content)
        else:
            # tomllib not available; skip deep parsing
            return {
                "exists": True,
                "valid": None,
                "parse_skipped": "tomllib requires Python 3.11+",
            }
    except Exception as exc:
        return {"exists": True, "valid": False, "toml_error": str(exc)}

    result: dict[str, Any] = {"exists": True, "valid": True}

    project = data.get("project", {})
    result["requires_python"] = project.get("requires-python")

    # Dependency names only (strip version specifiers)
    raw_deps = project.get("dependencies", [])
    result["dependencies"] = _strip_version_specifiers(raw_deps)

    # Build backend
    build_system = data.get("build-system", {})
    result["build_backend"] = build_system.get("build-backend")

    # Poetry
    result["has_tool_poetry"] = "poetry" in data.get("tool", {})

    # Project scripts (console_scripts style in [project.scripts])
    proj_scripts = project.get("scripts", {})
    result["project_scripts"] = dict(proj_scripts) if isinstance(proj_scripts, dict) else {}

    return result


def _strip_version_specifiers(deps: list) -> list[str]:
    """Return just the package name from a PEP 508 dependency string."""
    names = []
    split_re = re.compile(r"[>=<!;\[\s@]")
    for dep in deps:
        if isinstance(dep, str):
            name = split_re.split(dep.strip())[0].strip()
            if name:
                names.append(name)
    return names


# ---------------------------------------------------------------------------
# Python — setup.py (regex-only, never executed)
# ---------------------------------------------------------------------------

def _inspect_setup_py(ctx: RepoContext) -> dict[str, Any]:
    content = _file_content(ctx, "setup.py")
    if content is None:
        return {"exists": False}

    result: dict[str, Any] = {"exists": True}

    m_pr = _SETUP_PYTHON_REQUIRES_RE.search(content)
    result["python_requires"] = m_pr.group(2) if m_pr else None

    m_ep = _SETUP_ENTRY_POINTS_RE.search(content)
    result["entry_points_raw"] = m_ep.group(1)[:500] if m_ep else None

    return result


# ---------------------------------------------------------------------------
# Python — likely entrypoints + framework hints
# ---------------------------------------------------------------------------

def _inspect_entrypoints(ctx: RepoContext) -> dict[str, Any]:
    """
    Find likely Python entrypoints at the repo root.

    - Check each of _KNOWN_ENTRYPOINTS for existence.
    - Scan all root-level .py files for `if __name__ == "__main__"` (up to 5
      additional hits beyond the known list).
    - Detect framework import hints from the first 8 KB of each candidate.
    """
    root: Path = ctx.root
    found_known: list[str] = []
    found_main_guard: list[str] = []  # non-known files with main guard
    framework_hints: set[str] = set()

    # Collect all root-level .py files (no subdirs — keep it shallow)
    try:
        py_files = sorted(
            p.name for p in root.iterdir()
            if p.is_file() and not p.is_symlink() and p.suffix == ".py"
        )
    except PermissionError:
        py_files = []

    def _check_file(name: str) -> None:
        """Read file and update framework_hints / found_main_guard."""
        res = _read_file_internal(ctx, name, max_bytes=8192)
        if not res.get("exists") or res.get("binary"):
            return
        text = res.get("content", "")
        for fw, pattern in _FRAMEWORK_IMPORTS.items():
            if pattern.search(text):
                framework_hints.add(fw)
        return text

    for name in _KNOWN_ENTRYPOINTS:
        text = _check_file(name)
        if _read_file_internal(ctx, name).get("exists"):
            found_known.append(name)

    for name in py_files:
        if name in _KNOWN_ENTRYPOINTS:
            continue
        res = _read_file_internal(ctx, name, max_bytes=8192)
        if not res.get("exists") or res.get("binary"):
            continue
        text = res.get("content", "")
        for fw, pattern in _FRAMEWORK_IMPORTS.items():
            if pattern.search(text):
                framework_hints.add(fw)
        if _MAIN_GUARD_RE.search(text) and len(found_main_guard) < 5:
            found_main_guard.append(name)

    return {
        "known_entrypoints": found_known,
        "main_guard_files": found_main_guard,
        "framework_hints": sorted(framework_hints),
    }


# ---------------------------------------------------------------------------
# Python — combined
# ---------------------------------------------------------------------------

def _inspect_python(ctx: RepoContext) -> Optional[dict[str, Any]]:
    """Return Python config dict, or None if no Python signals are present."""
    # Decide whether this looks like a Python project at all.
    has_req = _file_exists(ctx, "requirements.txt")
    has_pyproject = _file_exists(ctx, "pyproject.toml")
    has_setup_py = _file_exists(ctx, "setup.py")
    has_setup_cfg = _file_exists(ctx, "setup.cfg")
    has_pipfile = _file_exists(ctx, "Pipfile")

    # Count .py files in root
    try:
        py_count = sum(
            1 for p in ctx.root.iterdir()
            if p.is_file() and not p.is_symlink() and p.suffix == ".py"
        )
    except PermissionError:
        py_count = 0

    if not any([has_req, has_pyproject, has_setup_py, has_setup_cfg, has_pipfile, py_count > 0]):
        return None

    ctx.log("Inspected Python configuration")

    result: dict[str, Any] = {}

    result["requirements_txt"] = _inspect_requirements_txt(ctx)
    result["pyproject"] = _inspect_pyproject(ctx)
    result["setup_py"] = _inspect_setup_py(ctx)

    # Python version files
    python_version_files: dict[str, str] = {}
    for vf in _PYTHON_VERSION_FILES:
        content = _file_content(ctx, vf)
        if content is not None:
            python_version_files[vf] = content.strip()
    result["python_version_files"] = python_version_files

    result["likely_entrypoints"] = _inspect_entrypoints(ctx)

    return result


# ---------------------------------------------------------------------------
# Public tool
# ---------------------------------------------------------------------------

def inspect_project_config(ctx: RepoContext) -> dict[str, Any]:
    """Inspect Node.js and Python project configuration deterministically.

    Never executes any content from the analyzed repository.
    All parsing uses stdlib (json, tomllib, re).

    Args:
        ctx: Active :class:`~repoready.context.RepoContext`.

    Returns:
        A dict with two top-level keys:

        ``node``
            ``None`` if no ``package.json`` exists; otherwise a dict with
            ``valid_json``, ``name``, ``scripts``, ``dependencies``,
            ``devDependencies``, ``engines``, ``packageManager``, ``main``,
            ``type``, ``bin``, ``lockfiles_found``,
            ``inferred_package_manager``, and ``node_version_files``.

        ``python``
            ``None`` if no Python signals (manifests or .py files) exist;
            otherwise a dict with ``requirements_txt``, ``pyproject``,
            ``setup_py``, ``python_version_files``, and
            ``likely_entrypoints``.
    """
    return {
        "node": _inspect_node(ctx),
        "python": _inspect_python(ctx),
    }
