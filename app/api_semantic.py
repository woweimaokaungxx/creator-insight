"""语义检索 API：模型可选 + 自动下载 + 建索引 + 检索。

挂载于 /api/semantic。前端管理页见 src/pages/Semantic.vue。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .services.semantic import semantic_service

router = APIRouter(prefix="/api/semantic", tags=["semantic"])


class SelectRequest(BaseModel):
    active_model: str


class DownloadRequest(BaseModel):
    model_id: str
    source: str = "modelscope"   # modelscope / huggingface


@router.get("/settings")
async def api_semantic_settings():
    """列出可选模型 + 各模型安装状态 + 当前选中 + 运行环境。"""
    return semantic_service.get_settings()


@router.post("/select")
async def api_semantic_select(req: SelectRequest):
    """切换当前语义检索使用的模型。"""
    try:
        return semantic_service.set_active_model(req.active_model)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/download")
async def api_semantic_download(req: DownloadRequest):
    """后台启动模型下载（ModelScope 国内源默认）。立即返回，前端轮询 /download/{id}。"""
    try:
        return semantic_service.start_download(req.model_id, source=req.source)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/download/{model_id}")
async def api_semantic_download_state(model_id: str):
    return semantic_service.get_download_state(model_id)


@router.delete("/models/{model_id}")
async def api_semantic_delete(model_id: str):
    """删除已下载模型及其索引（下载中禁止）。"""
    try:
        return semantic_service.delete_model(model_id)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/index")
async def api_semantic_index(model_id: str | None = None, limit: int = 0):
    """对存量 prediction 建/重建语义索引。需先选中的模型已下载。"""
    try:
        return semantic_service.index_predictions(model_id=model_id, limit=limit)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/search")
async def api_semantic_search(q: str, limit: int = 10, model_id: str | None = None):
    """语义检索：输入查询文本，返回与存量预测最相似的若干条。"""
    try:
        return semantic_service.search(q, limit=limit, model_id=model_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
