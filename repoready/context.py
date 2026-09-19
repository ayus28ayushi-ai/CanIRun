"""
repoready/context.py

Shared context object for a single repository analysis run.
Passed between deterministic tools and (later) the Strands agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class RepoContext:
    """Lightweight shared state for one repository analysis run.

    Attributes:
        root:       Path to the extracted repository root on disk.
        owner:      GitHub repository owner (organisation or user).
        repo:       GitHub repository name.
        ref:        Branch, tag, or commit SHA used for the fetch; None means
                    the default branch was used.
        activity:   Ordered log of actions / findings appended via log().
        tool_calls: Running count of deterministic tool invocations.
    """

    root: Path
    owner: str
    repo: str
    ref: Optional[str] = None
    activity: list[str] = field(default_factory=list)
    tool_calls: int = 0

    def log(self, message: str) -> None:
        """Append *message* to the activity log."""
        self.activity.append(message)
