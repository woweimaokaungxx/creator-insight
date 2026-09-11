"""doctor 诊断：检查 creator-insight 本地运行依赖（参考 douyin-creator-distill 的 npm run doctor）。

检查项：
  1. 配置文件完整性（config/config.json 必需键）
  2. AI 云端 key 是否已配置
  3. FFmpeg 是否可用（本地转写预处理）
  4. faster-whisper 是否可导入 + 本地模型是否就绪
  5. playwright + 系统浏览器（抖音自动 cookie）
  6. yt-dlp（视频下载）
  7. 数据目录可写

用法：
  python scripts/doctor.py
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK = 0
FAIL = 0
WARN = 0


def report(status: str, name: str, detail: str = "") -> None:
    global OK, FAIL, WARN
    if status == "ok":
        OK += 1
        print(f"  ✅ {name}" + (f"  {detail}" if detail else ""))
    elif status == "warn":
        WARN += 1
        print(f"  ⚠️  {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    print("=" * 56)
    print("  creator-insight 运行环境诊断 (doctor)")
    print("=" * 56)

    # 1. 配置文件
    print("\n[1] 配置文件")
    cfg_path = ROOT / "config" / "config.json"
    if not cfg_path.exists():
        report("fail", "config/config.json", "请复制 config.example.json 并填写")
    else:
        report("ok", "config/config.json")
        try:
            import json
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            required = ["ai", "transcription", "platforms"]
            for k in required:
                if k not in cfg:
                    report("fail", f"缺少配置段 {k}")
                else:
                    report("ok", f"配置段 {k}")
        except Exception as exc:
            report("fail", "配置文件解析", str(exc))

    # 2. AI 云端 key
    print("\n[2] AI 云端")
    try:
        from app.config import config
        api_key = config.get("ai", "cloud", "api_key", default="")
        if api_key:
            report("ok", "云端 API key", f"(已配置 {api_key[:6]}...)")
        else:
            report("warn", "云端 API key 未配置", "预测抽取/验证/简体化将不可用")
    except Exception as exc:
        report("fail", "加载 app.config", str(exc))

    # 3. FFmpeg
    print("\n[3] FFmpeg（本地转写预处理）")
    ff = shutil.which("ffmpeg")
    if ff:
        report("ok", "ffmpeg", ff)
    else:
        report("warn", "ffmpeg 未找到", "转写将直接处理原文件（准确率可能下降）")

    # 4. faster-whisper + 模型
    print("\n[4] 本地 Whisper 转写模型")
    if importlib.util.find_spec("faster_whisper"):
        report("ok", "faster-whisper 已安装")
        try:
            cfg_whisper = config.get("transcription", "whisper", default={})
            model_name = cfg_whisper.get("model", "small")
            report("ok", f"配置模型档位: {model_name}")
        except Exception:
            pass
    else:
        report("warn", "faster-whisper 未安装", "本地转写不可用（pip install faster-whisper）")

    # 4b. 本地 LLM 兜底（Ollama）
    print("\n[4b] 本地 LLM 兜底（Ollama）")
    try:
        lf = config.get("ai", "local_fallback", default={})
        base_url = lf.get("base_url", "http://127.0.0.1:11434")
        model_cfg = lf.get("model", "")
        enabled = bool(lf.get("enabled"))
        try:
            import httpx
            resp = httpx.get(f"{base_url}/api/tags", timeout=3)
            if resp.status_code == 200:
                tags = (resp.json() or {}).get("models") or []
                names = [t.get("name", "") for t in tags]
                report("ok", f"Ollama 服务可用 {base_url}", f"已装模型: {', '.join(names) or '无'}")
                if enabled:
                    if model_cfg in names:
                        report("ok", "配置的本地模型就绪", model_cfg)
                    else:
                        report("warn", f"配置模型未拉取: {model_cfg}", f"可用: {', '.join(names) or '无'}")
                else:
                    report("warn", "本地 LLM 兜底未启用", "ai.local_fallback.enabled=false")
            else:
                report("warn", "Ollama 未响应", f"{base_url} ({resp.status_code})")
        except Exception:
            report("warn", "Ollama 连接失败", f"{base_url}（未启动？）")
    except Exception:
        report("warn", "未读取到 local_fallback 配置")

    # 5. playwright + 浏览器
    print("\n[5] 抖音自动 cookie（Playwright）")
    if importlib.util.find_spec("playwright"):
        report("ok", "playwright 已安装")
        browsers = []
        for b in ("C:/Program Files/Google/Chrome/Application/chrome.exe",
                  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                  "C:/Program Files/Microsoft/Edge/Application/msedge.exe"):
            if Path(b).exists():
                browsers.append(b.split("/")[-2])
        if browsers:
            report("ok", "系统浏览器", ", ".join(set(browsers)))
        else:
            report("warn", "未检测到 Chrome/Edge", "自动 cookie 生成不可用")
    else:
        report("warn", "playwright 未安装", "抖音自动 cookie 不可用")

    # 6. yt-dlp
    print("\n[6] yt-dlp（视频下载）")
    if importlib.util.find_spec("yt_dlp"):
        report("ok", "yt-dlp 已安装")
    else:
        report("fail", "yt-dlp 未安装", "视频下载不可用")

    # 7. 数据目录
    print("\n[7] 数据目录")
    try:
        data_dir = config.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        test = data_dir / ".write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        report("ok", f"数据目录可写 {data_dir}")
    except Exception as exc:
        report("fail", "数据目录不可写", str(exc))

    # 8. 本地语义检索（模型可选 + 自动下载，参考 douyin-creator-distill）
    print("\n[8] 本地语义检索（模型可选 + 自动下载）")
    try:
        from app.services.semantic import semantic_service
        rt = semantic_service.runtime()
        if rt["downloader_ready"]:
            report("ok", "下载依赖就绪", "modelscope + huggingface_hub")
        else:
            report("warn", "下载依赖未安装", "pip install modelscope huggingface-hub（自动下载模型所需）")
        if rt["embedder_ready"]:
            report("ok", "向量化就绪", "sentence-transformers 可导入")
        else:
            report("warn", "向量化未安装", "pip install sentence-transformers torch（本地检索所需，CPU 版）")
        for m in semantic_service.get_settings()["models"]:
            if m["status"] == "installed":
                report("ok", f"模型已安装: {m['label']}", f"{m['approximateSize']} @ {m['directory']}")
            elif m["status"] == "downloading":
                report("warn", f"模型下载中: {m['label']}", f"{m.get('progress',0)}%")
            elif m["status"] == "failed":
                report("fail", f"模型下载失败: {m['label']}", m.get("error",""))
            else:
                report("warn", f"模型未下载: {m['label']}", f"设置页点下载（{m['approximateSize']}）")
    except Exception as exc:
        report("fail", "语义检索模块加载失败", str(exc))

    # 9. 后端可启动
    print("\n[9] 后端可启动")
    try:
        import app.main  # noqa: F401
        report("ok", "app.main 可导入")
    except Exception as exc:
        report("fail", "app.main 导入失败", str(exc))

    print("\n" + "=" * 56)
    print(f"  ✅ {OK} 正常 | ⚠️ {WARN} 警告 | ❌ {FAIL} 缺失")
    print("=" * 56)
    print("提示：警告项为可选能力；缺失项（❌）需修复后才能完整工作。")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
