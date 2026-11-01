"""Where Muninn's own files are, installed or in a checkout: the contracts it reads (llm-points.yaml, the output
schemas, the vendored Principal Handoff extensions) travel inside the package."""

from __future__ import annotations

from pathlib import Path

CONTRACTS = Path(__file__).resolve().parent / "contracts"
