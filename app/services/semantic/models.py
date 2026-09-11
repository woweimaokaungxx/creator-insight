"""模型注册表：可选模型 + 目录解析 + 安装状态检测。

参考 douyin-creator-distill 的 semantic-model-service.js（MODELS 定义），
改为 Python 原生、与本项目 config / data_dir 约定对齐。
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ...config import ROOT, config

# 模型权重文件判定（用于“安装完成”检测，与 distill 的 hasModelWeights 一致）
_WEIGHT_RE = re.compile(r"(?:model(?:-\d+-of-\d+)?\.safetensors|pytorch_model(?:-\d+-of-\d+)?\.bin)$", re.I)
MODEL_FILE_PATTERNS = [
    "*.json", "*.txt", "*.model", "*.safetensors", "*.py",
    "*.tiktoken", "merges.txt", "vocab.json", "*.csv", "*.gemm",
]


@dataclass(frozen=True)
class ModelDef:
    id: str
    label: str
    repo_id: str                 # HuggingFace repo id
    modelscope_id: str           # ModelScope repo id（国内源，默认下载源）
    approximate_bytes: int
    approximate_size: str
    dimension: int
    license: str
    summary: str


# 两档模型，对齐 distill 的 lightweight / high_precision
MODEL_REGISTRY: dict[str, ModelDef] = {
    "lightweight": ModelDef(
        id="lightweight",
        label="轻量模式",
        repo_id="BAAI/bge-small-zh-v1.5",
        modelscope_id="AI-ModelScope/bge-small-zh-v1.5",
        approximate_bytes=96 * 1024 * 1024,
        approximate_size="约 96 MB",
        dimension=512,
        license="MIT",
        summary="适合标题、简介和分段正文的日常语义筛选，内存占用最低。",
    ),
    "high_precision": ModelDef(
        id="high_precision",
        label="高精度模式",
        repo_id="Qwen/Qwen3-Embedding-0.6B",
        modelscope_id="Qwen/Qwen3-Embedding-0.6B",
        approximate_bytes=round(1.2 * 1024 * 1024 * 1024),
        approximate_size="约 1.2 GB",
        dimension=1024,
        license="Apache-2.0",
        summary="复杂语义、长文本和更精细的知识召回，向量维度更高。",
    ),
}


def default_model_root() -> Path:
    """模型根目录：config.semantic_search.model_root，缺省落到 data_dir/models/embedding。"""
    raw = config.get("semantic_search", "model_root", default=None)
    if raw:
        p = Path(str(raw))
        if not p.is_absolute():
            p = ROOT / p
        return p
    return config.data_dir / "models" / "embedding"


def model_directory(model_id: str) -> Path:
    """某模型权重目录（防越界）。"""
    if model_id not in MODEL_REGISTRY:
        raise KeyError(f"未知的语义模型：{model_id}")
    root = default_model_root().resolve()
    directory = (root / model_id).resolve()
    if directory != root and not str(directory).startswith(f"{root}{os.sep}"):
        raise ValueError("模型目录越界。")
    return directory


def model_def(model_id: str) -> ModelDef:
    m = MODEL_REGISTRY.get(model_id)
    if not m:
        raise KeyError(f"未知的语义模型：{model_id}")
    return m


def has_model_weights(directory: Path) -> bool:
    if not directory.exists():
        return False
    return any(f.is_file() and _WEIGHT_RE.search(f.name) for f in directory.rglob("*"))


def directory_size(directory: Path) -> int:
    if not directory.exists():
        return 0
    total = 0
    for entry in directory.rglob("*"):
        if entry.is_file():
            total += entry.stat().st_size
    return total


def is_installed(model_id: str) -> bool:
    """与 distill 的 installationState 对齐：标记 + config.json + 权重三者齐全。"""
    d = model_directory(model_id)
    marker = d / ".download-complete.json"
    return marker.exists() and (d / "config.json").exists() and has_model_weights(d)


def installation_state(model_id: str) -> dict:
    m = model_def(model_id)
    d = model_directory(model_id)
    installed = is_installed(model_id)
    size = directory_size(d)
    return {
        "id": m.id,
        "label": m.label,
        "repoId": m.repo_id,
        "modelScopeId": m.modelscope_id,
        "approximateSize": m.approximate_size,
        "dimension": m.dimension,
        "license": m.license,
        "summary": m.summary,
        "installed": installed,
        "directory": str(d),
        "sizeBytes": size,
    }


def active_model_id() -> str:
    raw = config.get("semantic_search", "active_model", default="lightweight")
    return raw if raw in MODEL_REGISTRY else "lightweight"


def set_active_model(model_id: str) -> None:
    model_def(model_id)  # 校验
    from ...config import CONFIG_FILE

    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
    cfg.setdefault("semantic_search", {})["active_model"] = model_id
    tmp = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def matches_model_file(file_path: str) -> bool:
    return any(fnmatch.fnmatch(file_path, p) for p in MODEL_FILE_PATTERNS)
