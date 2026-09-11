"""Obsidian 写出器：frontmatter + AUTO/HUMAN 双层标记，增量更新不覆盖人工区"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import config

AUTO_BEGIN = "<!-- BEGIN AUTO · DO NOT EDIT BELOW MANUALLY · 软件下次同步会覆盖 -->"
AUTO_END = "<!-- END AUTO -->"
HUMAN_BEGIN = "<!-- BEGIN HUMAN · 此区域以下你可自由编辑 · 软件不会覆盖 -->"
HUMAN_END = "<!-- END HUMAN -->"

AUTO_RE = re.compile(rf"({re.escape(AUTO_BEGIN)}.*?{re.escape(AUTO_END)})", re.S)
HUMAN_RE = re.compile(rf"({re.escape(HUMAN_BEGIN)}.*?{re.escape(HUMAN_END)})", re.S)
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)


class ObsidianAdapter:
    def __init__(self):
        self.root = Path(config.get("obsidian", "vault_path", default="")) if config.get("obsidian", "vault_path") else None
        self.folder = str(config.get("obsidian", "root_folder", default="creator-insight"))
        self.enabled = bool(config.get("obsidian", "write_enabled", default=False))
        if self.root and self.root.exists():
            self.enabled = True

    @property
    def active(self) -> bool:
        return self.enabled and self.root is not None

    def _base_dir(self, *parts: str) -> Path:
        if not self.active:
            raise RuntimeError("Obsidian 未启用：请配置 config.json 的 obsidian.vault_path 与 write_enabled")
        d = self.root.joinpath(self.folder, *parts)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ─── frontmatter 工具 ────────────────────────────────────
    @staticmethod
    def _frontmatter(meta: dict) -> str:
        lines = ["---"]
        for k, v in meta.items():
            if isinstance(v, (list, dict)):
                lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
            elif isinstance(v, bool):
                lines.append(f"{k}: {'true' if v else 'false'}")
            elif v is None:
                lines.append(f"{k}: null")
            else:
                lines.append(f"{k}: {v}")
        lines.append("---")
        return "\n".join(lines)

    # ─── 双层结构读写 ─────────────────────────────────────────
    @staticmethod
    def _build_body(auto_body: str, human_body: str) -> str:
        return (f"{AUTO_BEGIN}\n\n{auto_body.strip()}\n\n{AUTO_END}\n\n"
                f"{HUMAN_BEGIN}\n\n{human_body.strip()}\n\n{HUMAN_END}\n")

    @staticmethod
    def _parse_file(content: str) -> tuple[dict, str, str]:
        """返回 (frontmatter_dict, auto_body, human_body)"""
        fm = {}
        m = FRONTMATTER_RE.match(content)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip()
        am = AUTO_RE.search(content)
        auto_body = am.group(1) if am else ""
        hm = HUMAN_RE.search(content)
        human_body = hm.group(1) if hm else ""
        return fm, auto_body, human_body

    # ─── 写 Verification 报告 ─────────────────────────────────
    def write_verification(self, *, creator_name: str, video_title: str, video_url: str,
                           prediction: dict, evidence: list[dict], verification: dict) -> Path:
        if not self.active:
            raise RuntimeError("Obsidian 未启用")
        creator_folder = self._base_dir("Creators", _safe_name(creator_name))
        pred_id = prediction.get("id", "pred")
        filename = creator_folder / f"verification_{pred_id[:8]}.md"

        fm = {
            "prediction_id": prediction.get("id", ""),
            "creator": creator_name,
            "video_title": video_title,
            "verdict": verification.get("final_verdict", "inconclusive"),
            "ai_verdict": verification.get("ai_verdict", ""),
            "human_verdict": verification.get("human_verdict", ""),
            "schema_version": "0.1",
            "auto_generated": "true",
        }

        auto_body = f"""# 验证报告

**博主**：{creator_name}
**视频**：[{video_title}]({video_url})

## 预测原话（不可修改）
> {prediction.get('raw_text', '')}

## AI 判定
- ai_verdict: {verification.get('ai_verdict', '')}
- ai_score: {verification.get('ai_score', '')}

## 证据
"""
        for e in evidence:
            auto_body += f"- [{e.get('source_type', '')}] {e.get('title', '')} — {e.get('url', '')}\n"

        human_body = "## 我的思考\n\n（待补充）\n"

        # 增量更新：保留人工区
        if filename.exists():
            _, _, old_human = self._parse_file(filename.read_text(encoding="utf-8"))
            if old_human:
                human_body = old_human.replace(HUMAN_BEGIN, "").replace(HUMAN_END, "").strip()

        full = self._frontmatter(fm) + "\n\n" + self._build_body(auto_body, human_body)
        filename.write_text(full, encoding="utf-8")
        return filename

    # ─── 写 Prediction 摘要（V0.1 只写关键预测） ───────────────
    def write_prediction(self, *, creator_name: str, content: dict, prediction: dict) -> Path:
        if not self.active:
            raise RuntimeError("Obsidian 未启用")
        creator_folder = self._base_dir("Creators", _safe_name(creator_name))
        pred_id = prediction.get("id", "pred")
        filename = creator_folder / f"prediction_{pred_id[:8]}.md"

        fm = {
            "prediction_id": prediction.get("id", ""),
            "creator": creator_name,
            "status": prediction.get("status", "extracted"),
            "direction": prediction.get("direction", ""),
            "due_at": prediction.get("due_at", ""),
            "schema_version": "0.1",
            "auto_generated": "true",
        }

        auto_body = f"""# 预测：{prediction.get('raw_text', '')}

**博主**：{creator_name}
**视频**：[{content.get('title', '')}]({content.get('url', '')})
**方向**：{prediction.get('direction', '')}
**时间**：{prediction.get('time_expression_raw', '')}（due: {prediction.get('due_at', '')}）
**置信度**：{prediction.get('confidence_raw', '')} → {prediction.get('confidence_score', '')}

## 状态
{prediction.get('status', '')}
"""

        human_body = "## 我的思考\n\n（待补充）\n"

        if filename.exists():
            _, _, old_human = self._parse_file(filename.read_text(encoding="utf-8"))
            if old_human:
                human_body = old_human.replace(HUMAN_BEGIN, "").replace(HUMAN_END, "").strip()

        full = self._frontmatter(fm) + "\n\n" + self._build_body(auto_body, human_body)
        filename.write_text(full, encoding="utf-8")
        return filename


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "unknown"


obsidian = ObsidianAdapter()
