"""自动下载器：从 ModelScope（国内默认源）或 HuggingFace 拉取嵌入模型权重。

逻辑对齐 douyin-creator-distill/scripts/manage_embedding_model.py：
  · ModelScope 用 modelscope.hub 取下载直链 + requests 断点续传（Range）
  · HuggingFace 用 huggingface_hub.snapshot_download
  · 完成后写 .download-complete.json 标记（服务据此判定 installed）
  · 全程不触发 sentence-transformers / torch（仅需 modelscope + huggingface_hub）
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ModelScope 国内源不走代理，避免被 7890 等代理误拦（与 distill 一致）
_DOMESTIC_NO_PROXY = "modelscope.cn,www.modelscope.cn,.modelscope.cn"


def _ensure_domestic_no_proxy() -> None:
    cur = os.environ.get("NO_PROXY", "")
    parts = [p for p in (cur.split(",") if cur else []) if p]
    for d in _DOMESTIC_NO_PROXY.split(","):
        if d not in parts:
            parts.append(d)
    joined = ",".join(parts)
    os.environ["NO_PROXY"] = joined
    os.environ["no_proxy"] = joined
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


def _emit(progress_cb, **payload) -> None:
    if progress_cb:
        progress_cb(payload)


def _download_modelscope_file(model_id: str, revision: str, file_info: dict, target: Path, progress_cb) -> None:
    import requests
    from modelscope.hub.file_download import get_file_download_url

    rel = str(file_info["Path"]).replace("\\", "/").lstrip("/")
    dest = (target / rel).resolve()
    if dest != target and not str(dest).startswith(f"{target}{os.sep}"):
        raise RuntimeError(f"模型文件路径越界：{rel}")
    expected = int(file_info.get("Size") or 0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (not expected or dest.stat().st_size == expected):
        return  # 已完整

    incomplete = dest.with_name(f"{dest.name}.incomplete")
    downloaded = incomplete.stat().st_size if incomplete.exists() else 0
    headers = {"Range": f"bytes={downloaded}-"} if downloaded else {}
    url = get_file_download_url(model_id, rel, revision)
    with requests.get(url, headers=headers, stream=True, timeout=(20, 120)) as resp:
        if resp.status_code == 416 and expected and downloaded == expected:
            incomplete.replace(dest)
            return
        resp.raise_for_status()
        append = resp.status_code == 206 and downloaded > 0
        if not append:
            downloaded = 0
        with incomplete.open("ab" if append else "wb") as out:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                out.write(chunk)
                downloaded += len(chunk)
                if expected:
                    pct = min(100, int(downloaded * 100 / expected))
                    _emit(progress_cb, status="downloading", file=rel,
                          downloadedBytes=downloaded, expectedBytes=expected, progress=pct)
    if expected and incomplete.stat().st_size != expected:
        raise RuntimeError(f"模型文件不完整：{rel} ({incomplete.stat().st_size}/{expected})")
    incomplete.replace(dest)


def download_model(model_id: str, source: str = "modelscope", progress_cb=None) -> dict:
    """同步下载（由 service 在后台线程调用）。progress_cb 接收进度字典。"""
    from .models import model_def, model_directory, matches_model_file, MODEL_FILE_PATTERNS

    _ensure_domestic_no_proxy()
    m = model_def(model_id)
    target = model_directory(model_id)
    target.mkdir(parents=True, exist_ok=True)
    repo_id = m.modelscope_id if source == "modelscope" else m.repo_id

    _emit(progress_cb, status="downloading", phase="list", message=f"列举 {source} 仓库文件…")
    if source == "modelscope":
        from modelscope.hub.api import HubApi
        revision = "master"
        files = HubApi().get_model_files(repo_id, revision=revision, recursive=True)
        selected = [f for f in files if matches_model_file(str(f.get("Path") or ""))]
        if not selected:
            raise RuntimeError(f"ModelScope 仓库未匹配到模型文件：{repo_id}")
        for f in selected:
            _download_modelscope_file(repo_id, revision, f, target, progress_cb)
    else:
        from huggingface_hub import snapshot_download
        cache = Path(tempfile.gettempdir()) / "creator-insight-hf-cache"
        snap = Path(snapshot_download(
            repo_id=repo_id, cache_dir=str(cache), max_workers=1,
            allow_patterns=list(MODEL_FILE_PATTERNS),
        ))
        shutil.copytree(snap, target, dirs_exist_ok=True)

    # 清理缓存与半成品
    shutil.rmtree(target / ".cache", ignore_errors=True)
    for inc in target.rglob("*.incomplete"):
        inc.unlink(missing_ok=True)
    if (target / "model.safetensors").exists():
        (target / "pytorch_model.bin").unlink(missing_ok=True)

    marker = {
        "repoId": repo_id,
        "source": source,
        "completedAt": datetime.now(timezone.utc).isoformat(),
    }
    (target / ".download-complete.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if source == "huggingface":
        shutil.rmtree(cache, ignore_errors=True)

    _emit(progress_cb, status="installed", **marker)
    return marker
