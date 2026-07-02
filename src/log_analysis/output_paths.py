from __future__ import annotations

from pathlib import Path


OUTPUT_ROOT_NAME = "output"


def output_root_from_cwd(cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    return (base / OUTPUT_ROOT_NAME).resolve()


def resolve_output_dir(raw_value: str, cwd: Path | None = None) -> Path:
    path = Path(raw_value)
    base = cwd or Path.cwd()
    if path.is_absolute():
        return path
    parts = path.parts
    if parts and parts[0] == OUTPUT_ROOT_NAME:
        return (base / path).resolve()
    return (output_root_from_cwd(cwd) / path).resolve()


def ensure_output_root(cwd: Path | None = None) -> Path:
    root = output_root_from_cwd(cwd)
    root.mkdir(parents=True, exist_ok=True)
    return root
