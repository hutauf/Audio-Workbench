"""Small dependency-free contract shared by optional analyzers.

The web application only needs a callable that accepts the normalized WAV and
returns JSON-compatible data. Keeping the contract here lets an analyzer be
tested from the command line or moved to a separate worker later.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class AnalyzerUnavailable(RuntimeError):
    """Raised when an optional runtime or local model is not installed."""


@dataclass(frozen=True)
class AnalyzerManifest:
    """Human- and machine-readable description of an analyzer."""

    id: str
    name: str
    description: str
    technology: str
    input_sample_rate: int = 16_000
    input_channels: int = 1


class Analyzer(Protocol):
    manifest: AnalyzerManifest

    def analyze(self, normalized_path: Path) -> dict[str, Any]:
        """Analyze one normalized mono WAV and return JSON-compatible data."""

