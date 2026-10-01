"""External AI provider abstraction.

The application MUST NOT depend on Groq directly. All external AI calls
go through this interface so providers can be swapped without touching
business logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class AIProviderError(Exception):
    """Raised when an AI provider call fails.

    Callers (e.g. background workers) are expected to retry with backoff.
    Raw project data must never be destroyed as a result of this error.
    """


class AIProvider(ABC):
    """Minimal interface used by Context Core."""

    @abstractmethod
    def complete_json(self, *, system: str, user: str) -> str:
        """Return a raw string containing JSON produced by the model.

        Implementations must raise AIProviderError on any failure
        (network, non-2xx, malformed response envelope).
        """

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if this provider has everything it needs to run."""
