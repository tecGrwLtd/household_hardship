"""Model artifacts on disk: models/<version>/ holds the fitted model files,
metadata.json (everything needed to score reproducibly) and metrics.json
(the evaluation the version was judged on). Which version is live is
tracked in the model_versions table (db.py), not here.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .baselines import RuleBasedModel
from .model import LGBMNeedModel
from .repeat import HistoryRepeatModel, LGBMRepeatModel

ARTIFACT_KINDS = {m.kind: m for m in (LGBMNeedModel, LGBMRepeatModel, HistoryRepeatModel)}

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"
PLACEHOLDER_VERSION = "rules-v0"   # seeded as active in schema.sql


def new_version(kind: str = "lgbm") -> str:
    return f"{kind}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"


def data_hash(X: pd.DataFrame, y: pd.Series) -> str:
    """Fingerprint of exactly the rows and values a version was trained on."""
    h = hashlib.sha256()
    h.update(pd.util.hash_pandas_object(X.astype(str), index=False).values.tobytes())
    h.update(pd.util.hash_pandas_object(y, index=False).values.tobytes())
    return h.hexdigest()


def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             cwd=Path(__file__).parent, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def save(model, version: str, metadata: dict, metrics: dict,
         models_dir: Path = MODELS_DIR) -> Path:
    directory = models_dir / version
    meta = {"version": version, **model.save(directory), **metadata}
    (directory / "metadata.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    (directory / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    return directory


def load(kind: str, artifact_path: str | Path | None):
    """Build the scorer for a model_versions row."""
    if kind == RuleBasedModel.kind:
        return RuleBasedModel()
    if kind in ARTIFACT_KINDS:
        directory = Path(artifact_path)
        if not directory.is_absolute():
            directory = MODELS_DIR.parent / directory
        return ARTIFACT_KINDS[kind].load(directory, read_metadata(directory))
    raise ValueError(f"unknown model kind {kind!r}")


def read_metadata(directory: str | Path) -> dict:
    directory = Path(directory)
    if not directory.is_absolute():
        directory = MODELS_DIR.parent / directory
    return json.loads((directory / "metadata.json").read_text(encoding="utf-8"))


def relative_to_project(path: Path) -> str:
    """Artifact paths are stored relative to the project root, so the same
    registry row works on a laptop and inside the API container."""
    try:
        return path.resolve().relative_to(MODELS_DIR.parent).as_posix()
    except ValueError:
        return str(path)
