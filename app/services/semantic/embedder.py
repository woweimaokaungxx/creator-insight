"""本地向量化：sentence-transformers 离线加载已下载模型并编码文本。

与 distill 不同，本项目本身即 Python，无需 subprocess 调 Python worker：
直接在本进程加载 SentenceTransformer（local_files_only=True 强制离线），
模型句柄按 model_id 缓存复用。
"""
from __future__ import annotations

import threading
from typing import List

from .models import is_installed, model_def, model_directory

_LOCK = threading.Lock()
_LOADED: dict[str, object] = {}  # model_id -> SentenceTransformer 句柄


def runtime_ready() -> bool:
    """sentence-transformers 是否可导入（决定 embedder 是否可用）。"""
    try:
        import importlib
        importlib.import_module("sentence_transformers")
        return True
    except Exception:
        return False


def load_embedder(model_id: str):
    if not is_installed(model_id):
        raise RuntimeError(f"模型未安装：{model_id}，请先在设置页下载。")
    with _LOCK:
        if model_id in _LOADED:
            return _LOADED[model_id]
        from sentence_transformers import SentenceTransformer
        st = SentenceTransformer(str(model_directory(model_id)), local_files_only=True, device="cpu")
        _LOADED[model_id] = st
        return st


def encode(model_id: str, texts: List[str]) -> List[List[float]]:
    """返回归一化向量列表（供余弦相似度 = 点积）。"""
    if isinstance(texts, str):
        texts = [texts]
    st = load_embedder(model_id)
    vecs = st.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    return [v.astype("<f4").tolist() for v in vecs]


def dimension_of(model_id: str) -> int:
    return model_def(model_id).dimension
