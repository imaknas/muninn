"""File extension → language, and project markers → language."""

from __future__ import annotations

from pathlib import Path

EXTENSIONS = {
    ".go": "go",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".rb": "ruby",
    ".sql": "sql",
    ".sh": "shell",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".tex": "latex",
}

MARKERS = {
    "go.mod": "go",
    "pyproject.toml": "python",
    "Cargo.toml": "rust",
    "package.json": "javascript",
    "tsconfig.json": "typescript",
}


def language_of(ext: str) -> str | None:
    return EXTENSIONS.get(ext.lower())


def project_languages(root: Path) -> list[str]:
    """Languages a project declares through its build files (top level only)."""
    try:
        return sorted({lang for marker, lang in MARKERS.items() if (root / marker).exists()})
    except OSError:
        return []
