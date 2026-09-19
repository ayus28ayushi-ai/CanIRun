"""
repoready/github_fetcher.py

Fetches a GitHub repository as a zip archive via the GitHub API and extracts
it safely to a temporary directory.

Security guarantees (per CLAUDE.md):
- No code from the repo is ever executed.
- Zip-slip is blocked: every entry's resolved path is checked against the
  target directory before extraction.
- Symlink entries are skipped entirely.
- Banned directories (.git, node_modules, venv, .venv, dist, build,
  __pycache__) are skipped.
- Individual files over 1 MB are skipped.
- Extraction aborts if more than 5 000 files would be extracted.
- Download aborts if the archive exceeds 25 MB.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

from repoready.context import RepoContext

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024   # 25 MB
MAX_FILE_BYTES = 1 * 1024 * 1024        # 1 MB per file
MAX_FILES = 5_000
BANNED_DIRS = {".git", "node_modules", "venv", ".venv", "dist", "build", "__pycache__"}
REQUEST_TIMEOUT = 30  # seconds for initial connection / API calls
STREAM_TIMEOUT = 60   # seconds for streaming the zip


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class InvalidGitHubURL(ValueError):
    """The supplied string is not a recognisable GitHub repository URL."""


class RepoNotFound(Exception):
    """The repository does not exist or is private (HTTP 404)."""


class RateLimited(Exception):
    """GitHub API rate limit reached (HTTP 403 / 429).

    Set GITHUB_TOKEN in your environment to increase the limit.
    """


class RepoTooLarge(Exception):
    """The repository archive exceeds the 25 MB safety limit."""


class FetchError(Exception):
    """An unexpected error occurred while contacting the GitHub API."""


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

# Matches:
#   https://github.com/owner/repo
#   https://github.com/owner/repo.git
#   https://github.com/owner/repo/
#   https://github.com/owner/repo/tree/<branch>
#   github.com/owner/repo  (no scheme)
_GITHUB_RE = re.compile(
    r"""^
    (?:https?://)?          # optional scheme
    github\.com/
    (?P<owner>[^/]+)/
    (?P<repo>[^/\s]+?)      # repo name (non-greedy, no slashes/spaces)
    (?:\.git)?              # optional .git suffix
    (?:/tree/(?P<ref>[^/\s]+))?  # optional /tree/<branch>
    [/]?                    # optional trailing slash
    $""",
    re.VERBOSE,
)


def parse_github_url(url: str) -> tuple[str, str, Optional[str]]:
    """Parse a GitHub repository URL into (owner, repo, ref_or_None).

    Accepts:
        https://github.com/owner/repo
        https://github.com/owner/repo.git
        https://github.com/owner/repo/
        https://github.com/owner/repo/tree/<branch>
        github.com/owner/repo  (no scheme)

    Raises:
        InvalidGitHubURL: if the URL does not match any of the above patterns.
    """
    url = url.strip()
    m = _GITHUB_RE.match(url)
    if not m:
        raise InvalidGitHubURL(
            f"Cannot parse as a GitHub repository URL: {url!r}\n"
            "Expected format: https://github.com/owner/repo"
        )
    owner = m.group("owner")
    repo = m.group("repo")
    ref = m.group("ref")  # may be None
    return owner, repo, ref


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _auth_headers() -> dict[str, str]:
    """Return Authorization header if GITHUB_TOKEN is set, else empty dict."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _raise_for_github_status(response: requests.Response, url: str) -> None:
    """Translate common GitHub API error codes into friendly exceptions."""
    code = response.status_code
    if code == 404:
        raise RepoNotFound(
            f"Repository not found (HTTP 404) for {url!r}. "
            "Check the URL, or the repo may be private."
        )
    if code in (403, 429):
        raise RateLimited(
            f"GitHub API rate limit reached (HTTP {code}). "
            "Set GITHUB_TOKEN in your environment to increase the limit."
        )
    if not response.ok:
        raise FetchError(
            f"Unexpected GitHub API error: HTTP {code} for {url!r}."
        )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _download_zip(owner: str, repo: str, ref: Optional[str]) -> bytes:
    """Download the repository zip archive from GitHub, enforcing the size cap.

    Args:
        owner: Repository owner.
        repo:  Repository name.
        ref:   Branch/tag/commit SHA, or None for the default branch.

    Returns:
        Raw zip bytes.

    Raises:
        RepoNotFound, RateLimited, RepoTooLarge, FetchError.
    """
    # GitHub zipball endpoint: /repos/{owner}/{repo}/zipball/{ref}
    # Omitting ref uses the default branch.
    ref_segment = ref if ref else ""
    api_url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{ref_segment}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        **_auth_headers(),
    }

    try:
        response = requests.get(
            api_url,
            headers=headers,
            stream=True,
            timeout=(REQUEST_TIMEOUT, STREAM_TIMEOUT),
            allow_redirects=True,
        )
    except requests.Timeout:
        raise FetchError("Request to GitHub API timed out.")
    except requests.ConnectionError as exc:
        raise FetchError(f"Could not connect to GitHub API: {exc}") from exc

    _raise_for_github_status(response, api_url)

    buf = io.BytesIO()
    downloaded = 0
    chunk_size = 64 * 1024  # 64 KB

    for chunk in response.iter_content(chunk_size=chunk_size):
        if chunk:
            downloaded += len(chunk)
            if downloaded > MAX_DOWNLOAD_BYTES:
                response.close()
                raise RepoTooLarge(
                    f"Repository archive exceeds the {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB "
                    "safety limit. Only repositories up to 25 MB are supported."
                )
            buf.write(chunk)

    return buf.getvalue()


# ---------------------------------------------------------------------------
# Safe extraction
# ---------------------------------------------------------------------------

def _is_banned_dir(parts: tuple[str, ...]) -> bool:
    """Return True if any path component is a banned directory name."""
    return any(part in BANNED_DIRS for part in parts)


def _safe_extract(zip_bytes: bytes, target_dir: str) -> Path:
    """Extract zip_bytes into target_dir with full safety checks.

    Safety measures applied to every zip entry:
    1. Skip symlinks (ZipInfo external_attr check for Unix symlink bit).
    2. Skip banned directory subtrees.
    3. Skip files larger than MAX_FILE_BYTES (uncompressed).
    4. Abort if more than MAX_FILES entries would be extracted.
    5. Zip-slip protection: resolve the final path and assert it is inside
       target_dir before writing any bytes.

    Returns:
        Path to the single top-level folder GitHub includes in its zips.

    Raises:
        RepoTooLarge: if extraction would exceed file count limits.
        FetchError:   if the zip is malformed or has no top-level folder.
    """
    target = Path(target_dir).resolve()

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        entries = zf.infolist()

        # Identify the GitHub-generated top-level folder (e.g. "owner-repo-abc1234/")
        top_level: Optional[str] = None
        for entry in entries:
            parts = Path(entry.filename).parts
            if parts:
                candidate = parts[0]
                if top_level is None:
                    top_level = candidate
                elif top_level != candidate:
                    # Multiple top-level dirs is unexpected but not fatal.
                    pass

        if top_level is None:
            raise FetchError("Downloaded zip archive appears to be empty.")

        extracted_count = 0

        for entry in entries:
            filename = entry.filename

            # --- 1. Skip symlinks (Unix symlink bit: 0xA000 in upper 4 bits) ---
            unix_mode = (entry.external_attr >> 16) & 0xFFFF
            if (unix_mode & 0xF000) == 0xA000:
                continue

            # --- 2. Skip directories themselves (we create them on demand) ---
            if entry.is_dir():
                continue

            parts = Path(filename).parts

            # --- 3. Skip banned directories ---
            if _is_banned_dir(parts):
                continue

            # --- 4. Skip large files ---
            if entry.file_size > MAX_FILE_BYTES:
                continue

            # --- 5. File count cap ---
            extracted_count += 1
            if extracted_count > MAX_FILES:
                raise RepoTooLarge(
                    f"Repository contains more than {MAX_FILES} files. "
                    "Only repositories up to 5 000 files are supported."
                )

            # --- 6. Zip-slip protection ---
            dest = (target / filename).resolve()
            if not str(dest).startswith(str(target) + os.sep) and dest != target:
                # Path escapes the target directory — skip silently (attack vector).
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(entry))

    repo_root = target / top_level
    if not repo_root.is_dir():
        raise FetchError(
            f"Expected top-level directory {top_level!r} was not created during extraction."
        )

    return repo_root


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_repository(url: str) -> RepoContext:
    """Fetch and safely extract a GitHub repository.

    Args:
        url: GitHub repository URL (any format accepted by parse_github_url).

    Returns:
        A :class:`~repoready.context.RepoContext` whose ``root`` points to the
        extracted repository.  The object also carries a private
        ``_temp_dir`` attribute used by :func:`cleanup`.

    Raises:
        InvalidGitHubURL, RepoNotFound, RateLimited, RepoTooLarge, FetchError.
    """
    owner, repo, ref = parse_github_url(url)
    zip_bytes = _download_zip(owner, repo, ref)
    temp_dir = tempfile.mkdtemp(prefix="repoready_")

    try:
        root = _safe_extract(zip_bytes, temp_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    ctx = RepoContext(owner=owner, repo=repo, ref=ref, root=root)
    # Store the temp dir so cleanup() can find it without the caller
    # needing to track it separately.
    object.__setattr__(ctx, "_temp_dir", temp_dir)
    return ctx


def cleanup(ctx: RepoContext) -> None:
    """Delete the temporary directory created by fetch_repository.

    Reads the private ``_temp_dir`` attribute set by :func:`fetch_repository`.
    Safe to call more than once — subsequent calls are no-ops.

    Args:
        ctx: The :class:`~repoready.context.RepoContext` returned by
             :func:`fetch_repository`.
    """
    temp_dir: Optional[str] = getattr(ctx, "_temp_dir", None)
    if temp_dir:
        shutil.rmtree(temp_dir, ignore_errors=True)
        # Clear the attribute so a second call is a guaranteed no-op.
        object.__setattr__(ctx, "_temp_dir", None)


# ---------------------------------------------------------------------------
# CLI entry point:  python -m repoready.github_fetcher <url>
# ---------------------------------------------------------------------------

def _count_files(path: Path) -> int:
    return sum(1 for _ in path.rglob("*") if _.is_file())


def main() -> None:
    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m repoready.github_fetcher <github_url>")
        sys.exit(1)

    url = sys.argv[1]
    print(f"Fetching: {url}")

    try:
        ctx = fetch_repository(url)
    except (InvalidGitHubURL, RepoNotFound, RateLimited, RepoTooLarge, FetchError) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    n_files = _count_files(ctx.root)
    print(f"Owner/repo : {ctx.owner}/{ctx.repo}")
    print(f"Ref        : {ctx.ref or '(default branch)'}")
    print(f"Temp root  : {ctx.root}")
    print(f"Files      : {n_files}")

    # Clean up after ourselves in the CLI demo.
    cleanup(ctx)
    print("Temp directory cleaned up.")


if __name__ == "__main__":
    main()
