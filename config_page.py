"""Local configuration page for scheduled Douyin chat messages.

Run ``python config_page.py`` and open http://127.0.0.1:8765/.
The server only listens on this computer and never sends a message.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from spark_sender import CONFIG_PATH

PAGE_PATH = Path(__file__).resolve().parent / "config.html"
MAX_BODY_BYTES = 256 * 1024
TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d\Z")


def validate_config(data: object) -> dict[str, object]:
    if not isinstance(data, dict) or not isinstance(data.get("friends"), list):
        raise ValueError("配置必须包含 friends 列表")
    if len(data["friends"]) > 100:
        raise ValueError("最多保存 100 条任务")

    tasks: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for position, raw in enumerate(data["friends"], start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"第 {position} 条任务格式错误")
        task_id = raw.get("id")
        name = raw.get("name")
        message = raw.get("message")
        send_at = raw.get("send_at")
        enabled = raw.get("enabled")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 100:
            raise ValueError(f"第 {position} 条任务缺少有效 id")
        task_id = task_id.strip()
        if task_id in seen_ids:
            raise ValueError("任务 id 重复，请删除重复任务后重试")
        seen_ids.add(task_id)
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError(f"第 {position} 条任务的好友昵称不能为空，且不能超过 100 字")
        if not isinstance(message, str) or not message.strip() or len(message) > 2000:
            raise ValueError(f"第 {position} 条任务的消息不能为空，且不能超过 2000 字")
        if not isinstance(send_at, str) or not TIME_PATTERN.fullmatch(send_at):
            raise ValueError(f"第 {position} 条任务的发送时间必须是 HH:MM")
        if not isinstance(enabled, bool):
            raise ValueError(f"第 {position} 条任务的启用状态格式错误")
        tasks.append({
            "id": task_id,
            "name": name.strip(),
            "message": message,
            "send_at": send_at,
            "enabled": enabled,
        })
    raw_email = data.get("email", {})
    if not isinstance(raw_email, dict):
        raise ValueError("邮件设置格式错误")
    email_enabled = raw_email.get("enabled", False)
    qq_address = raw_email.get("qq_address", "")
    recipient = raw_email.get("recipient", "")
    authorization_code = raw_email.get("authorization_code", "")
    if not isinstance(email_enabled, bool) or not all(isinstance(value, str) for value in
            (qq_address, recipient, authorization_code)):
        raise ValueError("邮件设置格式错误")
    qq_address, recipient, authorization_code = (value.strip() for value in
                                                  (qq_address, recipient, authorization_code))
    if any(len(value) > 200 for value in (qq_address, recipient, authorization_code)):
        raise ValueError("邮件设置内容过长")
    if email_enabled and (not re.fullmatch(r"[^\s@]+@qq\.com", qq_address, re.I)
                          or not re.fullmatch(r"[^\s@]+@[^\s@]+", recipient)
                          or not authorization_code):
        raise ValueError("启用邮件时，请填写 QQ 发件邮箱、收件邮箱和 QQ 邮箱 SMTP 授权码")
    raw_daily_note = data.get("daily_note", {})
    if not isinstance(raw_daily_note, dict):
        raise ValueError("每日日期天气设置格式错误")
    note_enabled = raw_daily_note.get("enabled", False)
    city = raw_daily_note.get("city", "")
    if not isinstance(note_enabled, bool) or not isinstance(city, str) or len(city) > 80:
        raise ValueError("每日日期天气设置格式错误")
    if note_enabled and not city.strip():
        raise ValueError("启用每日日期天气时，请填写城市")
    return {"friends": tasks, "daily_note": {"enabled": note_enabled, "city": city.strip()},
            "email": {"enabled": email_enabled,
            "qq_address": qq_address, "recipient": recipient,
            "authorization_code": authorization_code}}


class ConfigHandler(BaseHTTPRequestHandler):
    def _allowed_host(self) -> bool:
        port = self.server.server_port
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: object) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if not self._allowed_host():
            self._json(403, {"error": "只允许本机访问"})
            return
        if self.path in {"/", "/config.html"}:
            self._send(200, PAGE_PATH.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/config.js":
            script = PAGE_PATH.with_name("config.js")
            self._send(200, script.read_bytes(), "text/javascript; charset=utf-8")
        elif self.path == "/config.css":
            stylesheet = PAGE_PATH.with_name("config.css")
            self._send(200, stylesheet.read_bytes(), "text/css; charset=utf-8")
        elif self.path == "/api/config":
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {"friends": []}
            except (OSError, json.JSONDecodeError):
                self._json(500, {"error": "无法读取 config.json，请检查文件格式"})
                return
            self._json(200, data)
        else:
            self._json(404, {"error": "页面不存在"})

    def do_POST(self) -> None:
        if not self._allowed_host() or self.path != "/api/config":
            self._json(403, {"error": "请求不允许"})
            return
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}:
            self._json(403, {"error": "来源不允许"})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._json(415, {"error": "请提交 JSON 格式"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY_BYTES:
                raise ValueError("配置文件大小超出限制")
            raw = json.loads(self.rfile.read(length).decode("utf-8"))
            data = validate_config(raw)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        temporary = CONFIG_PATH.with_name("config.json.tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            os.replace(temporary, CONFIG_PATH)
        except OSError:
            self._json(500, {"error": "保存失败，请检查 config.json 的写入权限"})
            return
        self._json(200, {"ok": True, "count": len(data["friends"])})


def main() -> None:
    parser = argparse.ArgumentParser(description="本机抖音定时发信配置页")
    parser.add_argument("--port", type=int, default=8765, help="本机端口，默认 8765")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), ConfigHandler)
    print(f"配置页已启动： http://127.0.0.1:{server.server_port}/")
    print("按 Ctrl+C 关闭配置页。页面只保存配置，不会发送消息。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
