"""Where Muninn's own files are, installed or in a checkout: the contracts it reads (llm-points.yaml, the output
schemas, the vendored Principal Handoff extensions) travel inside the package."""

from __future__ import annotations

from pathlib import Path

CONTRACTS = Path(__file__).resolve().parent / "contracts"


def version() -> str:
    """The version installed (in a checkout, the one its pyproject.toml says)."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as installed

    try:
        return installed("muninn-ai")
    except PackageNotFoundError:
        return "unknown"
