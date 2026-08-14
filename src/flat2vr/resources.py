"""Locate the bundled container build context."""

from __future__ import annotations

from pathlib import Path


def container_context() -> Path:
    path = Path(__file__).resolve().parent / "container"
    if not (path / "Dockerfile").is_file():
        raise RuntimeError(f"bundled Docker context is incomplete: {path}")
    return path
