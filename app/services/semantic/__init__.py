"""语义检索子包：模型可选 + 自动下载 + 本地向量化（参考 douyin-creator-distill）。

对外只暴露一个单例 `semantic_service`，其余模块为内部实现：
  models.py     —— 模型注册表 / 目录 / 安装状态
  download.py   —— 从 ModelScope / HuggingFace 自动下载（断点续传 + 完成标记）
  embedder.py   —— 本地 sentence-transformers 离线向量化
  service.py    —— 编排：设置 / 选模型 / 下载 / 进度 / 删除 / 建索引 / 检索
"""
from __future__ import annotations

from .service import SemanticService

semantic_service = SemanticService()

__all__ = ["semantic_service", "SemanticService"]
