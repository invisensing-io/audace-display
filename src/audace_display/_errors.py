"""Single error type for audace-display.

Any user-facing error condition (unreadable file, unknown channel, empty slice,
invalid demod plugin...) raises :class:`AudaceDisplayError`. The CLI catches it,
prints the message and exits with a non-zero code, without a noisy traceback.
Other exceptions propagate (a bug to fix).
"""
from __future__ import annotations


class AudaceDisplayError(Exception):
    """Expected error, to be presented cleanly to the user."""
