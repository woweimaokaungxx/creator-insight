"""配置加载：config/config.json（不存在则用 config.example.json 默认值）"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "config.json"
EXAMPLE_FILE = CONFIG_DIR / "config.example.json"


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并，override 优先"""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_json_with_comments(path: Path) -> dict:
    """加载 JSON，忽略行首 # 注释（便于用户阅读配置）"""
    lines = path.read_text(encoding="utf-8").splitlines()
    cleaned = "\n".join(
        line for line in lines
        if not line.lstrip().startswith("#")
    )
    return json.loads(cleaned)


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or CONFIG_FILE
        self._data = self._load()

    def _load(self) -> dict:
        base: dict = {}
        if EXAMPLE_FILE.exists():
            base = _load_json_with_comments(EXAMPLE_FILE)
        if self.path.exists():
            user = _load_json_with_comments(self.path)
            base = _deep_merge(base, user)
        # 环境变量覆盖（便于调试）
        env_prefix = "CI_"
        for key, val in os.environ.items():
            if key.startswith(env_prefix):
                parts = key[len(env_prefix):].lower().split("__")
                node = base
                for p in parts[:-1]:
                    node = node.setdefault(p, {})
                node[parts[-1]] = val
        return base

    def get(self, *path: str, default=None):
        node = self._data
        for p in path:
            if not isinstance(node, dict) or p not in node:
                return default
            node = node[p]
        return node

    @property
    def data(self) -> dict:
        return self._data

    @property
    def host(self) -> str:
        return str(self.get("app", "host", default="127.0.0.1"))

    @property
    def port(self) -> int:
        return int(self.get("app", "port", default=8781))

    @property
    def data_dir(self) -> Path:
        d = Path(str(self.get("app", "data_dir", default="./data")))
        if not d.is_absolute():
            d = ROOT / d
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        return self.data_dir / "creator_insight.db"

    def update_section(self, section: str, values: dict) -> None:
        """更新/写入某个配置段并保存到 config.json"""
        self._data.setdefault(section, {})
        if isinstance(self._data[section], dict):
            self._data[section].update(values)
        else:
            self._data[section] = values
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


config = Config()
