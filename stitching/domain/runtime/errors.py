from __future__ import annotations


class ProjectRequestError(RuntimeError):
    """The request or requested inputs are invalid for project start."""


class ProjectBlockedError(RuntimeError):
    """The request is valid, but runtime start is currently blocked."""
