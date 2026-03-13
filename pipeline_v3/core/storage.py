import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_jsonable(v) for v in obj]
    return obj


def write_json(path: str, data: Any) -> None:
    p = Path(path)
    ensure_dir(str(p.parent))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(to_jsonable(data), f, indent=2, sort_keys=False, ensure_ascii=True)


def write_text(path: str, text: str) -> None:
    p = Path(path)
    ensure_dir(str(p.parent))
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)

