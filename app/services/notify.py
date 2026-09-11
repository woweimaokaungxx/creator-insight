"""通知服务（V0.4）——订阅新视频 / 自动处理 / 硬预测自动锁定的多渠道通知。

通道：
  local    站内通知：写入 notification 表，前端通知中心展示（默认启用）
  webhook  HTTP POST JSON 到配置的 notify.webhook_url
  email    SMTP 邮件（标准库 smtplib/email，支持 SSL/TLS）

配置（config.json 的 notify 段）：
  notify.enabled       总开关（默认 true）
  notify.channels      启用的通道列表，如 ["local", "webhook"]（默认 ["local"]）
  notify.webhook_url   Webhook 地址（可为空）
  notify.smtp          {host, port, user, password, from_name, to, use_ssl}

触发点（调用 send_notification）：
  订阅发现新视频（category=new_videos）
  自动处理成功 / 失败（category=auto_process）
  硬预测自动锁定（category=auto_applied）
"""
from __future__ import annotations

import json
import smtplib
import time
import uuid
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from ..config import config
from ..db import get_conn, log_event


def _channels() -> list[str]:
    if not config.get("notify", "enabled", default=True):
        return []
    ch = config.get("notify", "channels", default=["local"])
    if isinstance(ch, str):
        ch = [c.strip() for c in ch.split(",") if c.strip()]
    return list(ch)


def send_notification(title: str, body: str, category: str = "general",
                      payload: dict | None = None,
                      channels: list[str] | None = None) -> list[dict]:
    """向每个启用通道发送通知，返回逐通道发送结果（每个元素含 channel/status/error）。"""
    targets = channels if channels is not None else _channels()
    results: list[dict] = []
    for ch in targets:
        if ch == "local":
            results.append(_send_local(title, body, category, payload))
        elif ch == "webhook":
            results.append(_send_webhook(title, body, category, payload))
        elif ch == "email":
            results.append(_send_email(title, body))
        else:
            results.append({"channel": ch, "status": "failed", "error": f"未知通道: {ch}"})
    return results


def send_test() -> list[dict]:
    """发送一条测试通知（人工触发，category=manual）"""
    return send_notification(
        "测试通知",
        "如果你能看到这条消息，说明通知通道工作正常。",
        category="manual",
    )


def send_test_with(title: str, body: str) -> list[dict]:
    """发送自定义标题/内容的测试通知（人工触发，category=manual）"""
    return send_notification(title, body, category="manual")


# ─── 通道实现 ──────────────────────────────────────────────
def _send_local(title: str, body: str, category: str, payload: dict | None) -> dict:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO notification (id, channel, category, title, body, "
            "payload_json, status, created_at) VALUES (?, 'local', ?, ?, ?, ?, 'sent', ?)",
            (str(uuid.uuid4()), category, title, body,
             json.dumps(payload or {}, ensure_ascii=False), int(time.time())),
        )
        conn.commit()
        return {"channel": "local", "status": "sent"}
    finally:
        conn.close()


def _send_webhook(title: str, body: str, category: str, payload: dict | None) -> dict:
    url = config.get("notify", "webhook_url", default="")
    if not url:
        return {"channel": "webhook", "status": "failed", "error": "未配置 notify.webhook_url"}
    try:
        import httpx
        resp = httpx.post(
            url,
            json={
                "title": title,
                "body": body,
                "category": category,
                "payload": payload or {},
                "sent_at": int(time.time()),
            },
            timeout=10,
        )
        resp.raise_for_status()
        return {"channel": "webhook", "status": "sent"}
    except Exception as exc:
        _record_failure("webhook", title, body, category, payload, str(exc))
        return {"channel": "webhook", "status": "failed", "error": str(exc)}


def _send_email(title: str, body: str) -> dict:
    smtp = config.get("notify", "smtp", default={}) or {}
    host = smtp.get("host", "")
    user = smtp.get("user", "")
    to = smtp.get("to", "")
    if not host or not user or not to:
        return {"channel": "email", "status": "failed", "error": "未配置 notify.smtp"}
    try:
        port = int(smtp.get("port", 465))
        use_ssl = bool(smtp.get("use_ssl", True))
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(title, "utf-8")
        msg["From"] = formataddr((str(smtp.get("from_name", "creator-insight")), user))
        msg["To"] = to
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            try:
                server.starttls()
            except Exception:
                pass
        try:
            server.login(user, smtp.get("password", ""))
            server.sendmail(user, [to], msg.as_string())
        finally:
            server.quit()
        return {"channel": "email", "status": "sent"}
    except Exception as exc:
        _record_failure("email", title, body, "manual", None, str(exc))
        return {"channel": "email", "status": "failed", "error": str(exc)}


def _record_failure(channel: str, title: str, body: str, category: str,
                    payload: dict | None, error: str) -> None:
    try:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO notification (id, channel, category, title, body, "
                "payload_json, status, error, created_at) VALUES (?, ?, ?, ?, ?, ?, 'failed', ?, ?)",
                (str(uuid.uuid4()), channel, category, title, body,
                 json.dumps(payload or {}, ensure_ascii=False), error, int(time.time())),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


# ─── 通知中心查询 ──────────────────────────────────────────
def list_notifications(limit: int = 50, unread_only: bool = False) -> dict:
    conn = get_conn()
    try:
        where = "WHERE read_at IS NULL" if unread_only else ""
        rows = conn.execute(
            f"SELECT * FROM notification {where} ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        unread = conn.execute(
            "SELECT COUNT(*) AS n FROM notification WHERE read_at IS NULL"
        ).fetchone()["n"]
        items = []
        for r in rows:
            d = dict(r)
            d["payload"] = _parse_json(d.pop("payload_json"))
            items.append(d)
        return {"items": items, "unread": unread}
    finally:
        conn.close()


def _parse_json(s: str | None) -> dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


def mark_read(notification_id: str | None = None) -> dict:
    """标记已读：传 id 标记单条；不传则全部标记已读"""
    conn = get_conn()
    try:
        now = int(time.time())
        if notification_id:
            conn.execute(
                "UPDATE notification SET read_at=? WHERE id=? AND read_at IS NULL",
                (now, notification_id),
            )
        else:
            conn.execute(
                "UPDATE notification SET read_at=? WHERE read_at IS NULL", (now,)
            )
        conn.commit()
        return {"ok": True, "marked": conn.total_changes}
    finally:
        conn.close()


def clear_notifications() -> dict:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM notification")
        conn.commit()
        return {"ok": True, "cleared": conn.total_changes}
    finally:
        conn.close()
