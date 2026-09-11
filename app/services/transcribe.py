"""转写公共服务：本地 Whisper 兜底（参考 douyin-creator-distill 的本地转写链路）。

链路：
  媒体文件(mp4/webm/m4a)
    -> FFmpeg 提取 16kHz 单声道 wav（提高准确率 + 减少计算）
    -> faster-whisper 转写
    -> 落盘产物：{id}.md（正文） / {id}.json（逐段） / {id}.srt（时间轴）
    -> 返回 {text_full, segments, language, files}

增强点（相对旧版）：
  - FFmpeg 音频预处理（16kHz 单声道），转写更准、更快
  - 产物落盘（正文/分段/字幕），可追溯、可复用
  - 断点续转：已转写视频直接读缓存返回，不重复计算
  - 配置驱动：transcription.whisper 支持 model/device/compute_type/language/retain_media
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from ..config import config


def _find_ffmpeg() -> str | None:
    """找到系统 FFmpeg 可执行文件"""
    import shutil
    try:
        return shutil.which("ffmpeg")
    except Exception:
        return None


def extract_audio(media_path: Path, target_dir: Path) -> Path | None:
    """用 FFmpeg 把媒体提取为 16kHz 单声道 wav，供 Whisper 转写。

    参考 douyin-creator-distill：先提取标准音频，转写更准、占用更小。
    失败（FFmpeg 不可用等）返回 None，交由上层直接转写原文件。
    """
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        return None
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"{media_path.stem}_16k.wav"
    if out.exists() and out.stat().st_size > 0:
        return out
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-i", str(media_path),
             "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(out)],
            capture_output=True, timeout=300,
        )
        if proc.returncode != 0:
            print(f"[whisper] FFmpeg 提取音频失败: {proc.stderr.decode(errors='ignore')[-300:]}")
            return None
        return out if out.exists() else None
    except Exception as exc:
        print(f"[whisper] FFmpeg 提取音频异常: {exc}")
        return None


def _fmt_ts(sec: float) -> str:
    """秒 -> SRT 时间戳 HH:MM:SS,mmm"""
    ms = int(round(sec * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, mms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{mms:03d}"


def _write_artifacts(out_dir: Path, stem: str, text: str,
                     segments: list[dict], language: str) -> dict:
    """落盘 md / json / srt 产物，返回文件路径 dict"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"{stem}.md"
    js = out_dir / f"{stem}.json"
    srt = out_dir / f"{stem}.srt"
    # md 正文
    md.write_text(text, encoding="utf-8")
    # json 逐段（含 text_full，供断点续转直接读取）
    js.write_text(json.dumps({
        "language": language,
        "text_full": text,
        "segments": segments,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    # srt 时间轴
    srt_lines = []
    for i, seg in enumerate(segments, 1):
        srt_lines.append(str(i))
        srt_lines.append(f"{_fmt_ts(seg['start'])} --> {_fmt_ts(seg['end'])}")
        srt_lines.append(seg["text"])
        srt_lines.append("")
    srt.write_text("\n".join(srt_lines), encoding="utf-8")
    return {"md": str(md), "json": str(js), "srt": str(srt)}


def _transcript_cache_key(media_path: Path) -> str:
    """基于文件路径 + 大小 + 修改时间的缓存键（用于断点续转）"""
    try:
        st = media_path.stat()
        return f"{media_path.stem}_{st.st_size}_{int(st.st_mtime)}"
    except Exception:
        return media_path.stem


def whisper_transcribe(media_path: Path,
                       out_dir: Path | None = None) -> dict | None:
    """本地 faster-whisper 转写；失败返回 None。

    out_dir 为产物落盘目录（默认 data/transcripts/whisper/）。
    断点续转：同一媒体（路径+大小+时间）已转写则直接读缓存 json 返回。
    """
    media_path = Path(media_path)
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("[whisper] faster-whisper 未安装，无法本地转写")
        return None

    out_dir = Path(out_dir) if out_dir else (config.data_dir / "transcripts" / "whisper")
    cache_key = _transcript_cache_key(media_path)
    cache_json = out_dir / f"{cache_key}.json"
    # 断点续转：命中缓存直接返回
    if cache_json.exists():
        try:
            cached = json.loads(cache_json.read_text(encoding="utf-8"))
            if cached.get("text_full"):
                print(f"[whisper] 命中缓存 {cache_key}")
                return cached
        except Exception:
            pass

    cfg = config.get("transcription", "whisper", default={})
    try:
        model = WhisperModel(
            cfg.get("model", "small"),
            device=cfg.get("device", "auto"),
            compute_type=cfg.get("compute_type", "int8"),
        )
    except Exception as e:
        print(f"[whisper] 模型加载失败: {e}")
        return None

    # FFmpeg 预处理：提取 16kHz 单声道 wav（失败则直接转原文件）
    audio = extract_audio(media_path, out_dir)
    input_path = audio if audio else media_path
    try:
        language = cfg.get("language") or None
        segments_iter, info = model.transcribe(
            str(input_path), language=language, vad_filter=True,
        )
        segments = [
            {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
            for s in segments_iter if s.text.strip()
        ]
        text = "\n".join(s["text"] for s in segments)
        if not text:
            print("[whisper] 转写结果为空")
            return None
        files = _write_artifacts(out_dir, cache_key, text, segments, info.language)
        # 按配置清理中间音频（默认删除 16k.wav，保留原始视频与转写产物）
        if not cfg.get("retain_media", False) and audio and audio.exists():
            try:
                audio.unlink()
            except Exception:
                pass
        result = {
            "text_full": text, "segments": segments,
            "language": info.language, "files": files,
        }
        return result
    except Exception as e:
        print(f"[whisper] 转写失败: {e}")
        return None


def transcribe_to_content(media_path: Path,
                          out_dir: Path | None = None) -> dict | None:
    """供 ingest/监控调用的转写结果（兼容旧返回结构）。

    在 whisper_transcribe 基础上，把产物文件信息合并进返回。
    """
    result = whisper_transcribe(media_path, out_dir)
    if result:
        result["source"] = "whisper_local"
    return result
