"""语义检索编排：设置 / 选模型 / 下载 / 进度 / 删除 / 建索引 / 检索。

后台任务沿用项目既有的「提交即返回 + 线程处理 + 轮询」模式（见 app/main.py 的 ingest）。
"""
from __future__ import annotations

import array
import json
import threading
import time
import uuid
from typing import List

from ...db import get_conn
from ...config import config
from . import embedder, models


class SemanticService:
    def __init__(self) -> None:
        self._downloads: dict[str, dict] = {}
        self._dl_lock = threading.Lock()

    # ── 运行环境 ─────────────────────────────────────────────
    def runtime(self) -> dict:
        dl_ready = self._downloader_ready()
        emb_ready = embedder.runtime_ready()
        return {
            "downloader_ready": dl_ready,
            "embedder_ready": emb_ready,
            "active_model": models.active_model_id(),
            "download_source": "ModelScope 国内源",
        }

    @staticmethod
    def _downloader_ready() -> bool:
        try:
            import importlib
            importlib.import_module("modelscope")
            importlib.import_module("huggingface_hub")
            return True
        except Exception:
            return False

    # ── 设置 ────────────────────────────────────────────────
    def get_settings(self) -> dict:
        active = models.active_model_id()
        model_list = []
        for mid in models.MODEL_REGISTRY:
            state = models.installation_state(mid)
            dl = self._downloads.get(mid)
            if dl and dl.get("status") == "downloading":
                state["status"] = "downloading"
                state["progress"] = dl.get("progress", state.get("progress", 0))
                state["message"] = dl.get("message", "")
            elif dl and dl.get("status") == "failed":
                state["status"] = "failed"
                state["error"] = dl.get("error", "")
            else:
                state["status"] = "installed" if state["installed"] else "missing"
            state["selected"] = (mid == active)
            model_list.append(state)
        return {
            "schemaVersion": "1.0",
            "activeModel": active,
            "modelRoot": str(models.default_model_root()),
            "runtime": self.runtime(),
            "models": model_list,
            "indexedCount": self._indexed_count(active),
        }

    def set_active_model(self, model_id: str) -> dict:
        models.set_active_model(model_id)
        return self.get_settings()

    # ── 下载（后台线程 + 进度）──────────────────────────────
    def start_download(self, model_id: str, source: str = "modelscope") -> dict:
        if model_id not in models.MODEL_REGISTRY:
            raise KeyError(f"未知模型：{model_id}")
        with self._dl_lock:
            existing = self._downloads.get(model_id)
            if existing and existing.get("status") == "downloading":
                return self.get_settings()
        if not self._downloader_ready():
            raise RuntimeError("缺少下载依赖（modelscope / huggingface_hub），请先安装。")
        if not models.model_directory(model_id).exists():
            models.model_directory(model_id).mkdir(parents=True, exist_ok=True)

        task = {
            "status": "downloading", "progress": 0, "message": "准备下载…",
            "startedAt": int(time.time()), "error": "",
        }
        with self._dl_lock:
            self._downloads[model_id] = task

        def _progress(payload: dict) -> None:
            with self._dl_lock:
                t = self._downloads.get(model_id)
                if not t:
                    return
                if payload.get("status") == "downloading":
                    if "progress" in payload:
                        t["progress"] = payload["progress"]
                    if payload.get("message"):
                        t["message"] = payload["message"]
                    elif payload.get("file"):
                        t["message"] = f"下载 {payload['file']} … {payload.get('progress', 0)}%"

        def _run() -> None:
            try:
                from .download import download_model
                download_model(model_id, source=source, progress_cb=_progress)
                with self._dl_lock:
                    self._downloads[model_id] = {
                        "status": "installed", "progress": 100,
                        "message": "下载完成", "startedAt": task["startedAt"], "error": "",
                    }
            except Exception as exc:
                with self._dl_lock:
                    self._downloads[model_id] = {
                        "status": "failed", "progress": 0,
                        "message": str(exc), "startedAt": task["startedAt"], "error": str(exc),
                    }

        threading.Thread(target=_run, daemon=True).start()
        return self.get_settings()

    def get_download_state(self, model_id: str) -> dict:
        with self._dl_lock:
            t = self._downloads.get(model_id)
        if not t:
            return {"status": "missing", "progress": 0}
        return dict(t)

    def delete_model(self, model_id: str) -> dict:
        with self._dl_lock:
            if self._downloads.get(model_id, {}).get("status") == "downloading":
                raise RuntimeError("模型正在下载，请等待完成后再删除。")
        import shutil
        d = models.model_directory(model_id)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        with self._dl_lock:
            self._downloads.pop(model_id, None)
        # 同时清理该模型的索引
        self._clear_index(model_id)
        return self.get_settings()

    # ── 索引（向量入库）─────────────────────────────────────
    @staticmethod
    def _pack(vec: List[float]) -> bytes:
        return array.array("f", vec).tobytes()

    @staticmethod
    def _unpack(blob: bytes) -> List[float]:
        return list(array.array("f", blob))

    def _indexed_count(self, model_id: str) -> int:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM semantic_index WHERE model_id=?", (model_id,)
            ).fetchone()
            return row["n"] if row else 0
        finally:
            conn.close()

    def _clear_index(self, model_id: str) -> None:
        conn = get_conn()
        try:
            conn.execute("DELETE FROM semantic_index WHERE model_id=?", (model_id,))
            conn.commit()
        finally:
            conn.close()

    def index_predictions(self, model_id: str | None = None, limit: int = 0) -> dict:
        """对存量 prediction 建/重建语义索引。文本 = AI 总结(interpreted_intent) + 预测原文 + 博主 + 视频标题。"""
        model_id = model_id or models.active_model_id()
        if not models.is_installed(model_id):
            raise RuntimeError(f"模型未安装：{model_id}")
        conn = get_conn()
        try:
            sql = (
                "SELECT p.id AS pid, p.raw_text AS raw_text, p.interpreted_intent AS intent, "
                "c.title AS title, cr.name AS creator "
                "FROM prediction p "
                "LEFT JOIN content c ON c.id=p.content_id "
                "LEFT JOIN creator cr ON cr.id=c.creator_id "
                "ORDER BY p.created_at DESC"
            )
            if limit:
                sql += f" LIMIT {int(limit)}"
            rows = conn.execute(sql).fetchall()
        finally:
            conn.close()

        docs = []
        meta = []
        for r in rows:
            # 总结优先作为检索主文本，原话兜底；两者都保留以覆盖不同措辞的检索
            parts = [r["intent"] or r["raw_text"]]
            if r["intent"] and r["raw_text"]:
                parts.append(r["raw_text"])
            if r["creator"]:
                parts.append(r["creator"])
            if r["title"]:
                parts.append(r["title"])
            text = " ".join(str(x) for x in parts if x)
            docs.append(text)
            meta.append((r["pid"], text))

        if not docs:
            self._clear_index(model_id)
            return {"indexed": 0, "model_id": model_id}

        vectors = embedder.encode(model_id, docs)
        dim = models.model_def(model_id).dimension
        conn = get_conn()
        try:
            conn.execute("DELETE FROM semantic_index WHERE model_id=?", (model_id,))
            for (pid, text), vec in zip(meta, vectors):
                conn.execute(
                    "INSERT INTO semantic_index "
                    "(id, target_type, target_id, model_id, dimension, vector, text, created_at) "
                    "VALUES (?, 'prediction', ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), pid, model_id, dim, self._pack(vec), text[:2000], int(time.time())),
                )
            conn.commit()
            return {"indexed": len(docs), "model_id": model_id}
        finally:
            conn.close()

    # ── 视频内容索引（参考 distill 的语义索引：标题+简介+转写分块）──
    @staticmethod
    def _chunk_text(text: str, max_len: int = 512, overlap: int = 64) -> List[str]:
        text = (text or "").strip()
        if not text:
            return []
        if len(text) <= max_len:
            return [text]
        chunks = []
        step = max_len - overlap
        start = 0
        while start < len(text):
            end = min(start + max_len, len(text))
            chunk = text[start:end]
            if end < len(text):
                break_at = max(chunk.rfind("。"), chunk.rfind("！"), chunk.rfind("？"),
                               chunk.rfind("\n"), chunk.rfind("."))
                if break_at > len(chunk) // 2:
                    chunk = chunk[:break_at + 1]
            chunks.append(chunk)
            if end >= len(text):
                break
            start = start + len(chunk)
        return chunks

    def index_contents(self, model_id: str | None = None, limit: int = 0,
                       progress_cb=None) -> dict:
        """对存量 content（视频）建/重建语义索引。文本 = 标题 + 简介 + 简体转写。"""
        model_id = model_id or models.active_model_id()
        if not models.is_installed(model_id):
            raise RuntimeError(f"模型未安装：{model_id}")
        conn = get_conn()
        try:
            sql = (
                "SELECT co.id, co.title, co.raw_meta_json, tr.text_full_simplified AS trans, "
                "cr.name AS creator FROM content co "
                "LEFT JOIN transcript tr ON tr.content_id=co.id "
                "LEFT JOIN creator cr ON cr.id=co.creator_id "
                "ORDER BY co.fetched_at DESC"
            )
            if limit:
                sql += f" LIMIT {int(limit)}"
            rows = conn.execute(sql).fetchall()
        finally:
            conn.close()

        # 组装每个 content 的全文（标题 + 互动 + 简体转写）
        contents = []
        for r in rows:
            parts = [r["title"] or ""]
            try:
                meta = json.loads(r["raw_meta_json"] or "{}")
                stats = meta.get("stats") or {}
                if stats:
                    parts.append("互动: " + json.dumps(
                        {k: stats.get(k) for k in ("digg_count", "comment_count",
                                                   "share_count", "collect_count")
                         if stats.get(k)}, ensure_ascii=False))
            except Exception:
                pass
            if r["trans"]:
                parts.append(r["trans"])
            text = "\n".join(p for p in parts if p)
            if text:
                contents.append((r["id"], r["creator"] or "", text))

        docs = []
        meta_list = []  # (content_id, chunk_index, text)
        for cid, creator, text in contents:
            chunks = self._chunk_text(text)
            for i, chunk in enumerate(chunks):
                docs.append(chunk)
                meta_list.append((cid, i, chunk))

        if not docs:
            self._clear_index(model_id)  # 只清 content 类型
            return {"indexed": 0, "model_id": model_id}

        vectors = embedder.encode(model_id, docs)
        dim = models.model_def(model_id).dimension
        conn = get_conn()
        try:
            ids = {cid for cid, _, _ in meta_list}
            placeholders = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM semantic_index WHERE target_type='content' "
                         f"AND model_id=? AND target_id IN ({placeholders})",
                         (model_id, *ids))
            for (cid, idx, text), vec in zip(meta_list, vectors):
                conn.execute(
                    "INSERT INTO semantic_index "
                    "(id, target_type, target_id, model_id, dimension, vector, text, created_at) "
                    "VALUES (?, 'content', ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), cid, model_id, dim, self._pack(vec), text[:2000],
                     int(time.time())),
                )
            conn.commit()
            return {"indexed": len(docs), "contents": len(contents), "model_id": model_id}
        finally:
            conn.close()

    # ── 检索 ────────────────────────────────────────────────
    def search(self, query: str, limit: int = 10, model_id: str | None = None) -> dict:
        model_id = model_id or models.active_model_id()
        if not query.strip():
            return {"query": query, "results": []}
        if not models.is_installed(model_id):
            raise RuntimeError(f"模型未安装：{model_id}")
        if self._indexed_count(model_id) == 0:
            raise RuntimeError("尚未建索引，请先 POST /api/semantic/index")

        qvec = embedder.encode(model_id, [query])[0]
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT id, target_id, text, vector FROM semantic_index WHERE model_id=?",
                (model_id,),
            ).fetchall()
        finally:
            conn.close()

        scored = []
        for r in rows:
            vec = self._unpack(r["vector"])
            sim = sum(a * b for a, b in zip(qvec, vec))  # 已归一化 → 点积即余弦
            scored.append((sim, r["target_id"], r["text"], r["id"]))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[: max(1, limit)]

        # 补充 prediction / content 展示字段（按 target_type 区分）
        results = []
        conn = get_conn()
        try:
            for sim, target_id, text, idx_id in top:
                ttype = self._target_type_of(target_id, conn)
                if ttype == "content":
                    c = conn.execute(
                        "SELECT co.title, co.url, co.digg_count, co.comment_count, "
                        "cr.name AS creator FROM content co "
                        "LEFT JOIN creator cr ON cr.id=co.creator_id WHERE co.id=?",
                        (target_id,),
                    ).fetchone()
                    results.append({
                        "score": round(sim, 4),
                        "target_type": "content",
                        "target_id": target_id,
                        "indexed_text": text,
                        "title": c["title"] if c else "",
                        "url": c["url"] if c else "",
                        "creator": c["creator"] if c else "",
                        "digg_count": c["digg_count"] if c else None,
                        "comment_count": c["comment_count"] if c else None,
                    })
                else:
                    p = conn.execute(
                        "SELECT p.raw_text, p.interpreted_intent, p.direction, p.status, "
                        "p.due_at, c.id AS cid, c.title, c.url, cr.name AS creator "
                        "FROM prediction p LEFT JOIN content c ON c.id=p.content_id "
                        "LEFT JOIN creator cr ON cr.id=c.creator_id WHERE p.id=?",
                        (target_id,),
                    ).fetchone()
                    results.append({
                        "score": round(sim, 4),
                        "target_type": "prediction",
                        "target_id": target_id,
                        "indexed_text": text,
                        "title": (p["interpreted_intent"] or p["raw_text"]) if p else "",   # 展示主文本=AI总结
                        "raw_text": p["raw_text"] if p else "",
                        "video_title": p["title"] if p else "",
                        "direction": p["direction"] if p else "",
                        "status": p["status"] if p else "",
                        "due_at": p["due_at"] if p else None,
                        "url": p["url"] if p else "",
                        "content_id": p["cid"] if p else "",
                        "creator": p["creator"] if p else "",
                    })
        finally:
            conn.close()
        return {"query": query, "model_id": model_id, "results": results}

    @staticmethod
    def _target_type_of(target_id: str, conn) -> str:
        """判断向量 target_id 是 content 还是 prediction"""
        if conn.execute("SELECT 1 FROM content WHERE id=?", (target_id,)).fetchone():
            return "content"
        return "prediction"
