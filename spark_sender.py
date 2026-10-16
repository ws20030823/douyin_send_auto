"""Minimal Douyin web chat sender for one manually selected friend.

The browser is controlled through visible page elements. No private API or
request signing is used. Run `login` once with the same profile before `send`.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import json
import os
import re
import smtplib
import ssl
import select
import sys
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from daily_note import append_daily_note, build_daily_note

CHAT_URL = "https://www.douyin.com/chat"
CHINA_TIME = timezone(timedelta(hours=8), name="Asia/Shanghai")
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
STATE_DIR = Path(__file__).resolve().parent / ".spark-state"
PROFILE_DIR = STATE_DIR / "browser-profile"
LEDGER_PATH = STATE_DIR / "send-ledger.json"
LOCK_PATH = STATE_DIR / "sender.lock"
STATUS_PATH = STATE_DIR / "last-run.json"
REPORT_LEDGER_PATH = STATE_DIR / "report-ledger.json"
LOGIN_SCREENSHOT_PATH = STATE_DIR / "login-qr.png"


class SendError(RuntimeError):
    pass


def today() -> str:
    return datetime.now(CHINA_TIME).date().isoformat()


def schedule_key(item: dict[str, str], day: str | None = None) -> str:
    """One execution per configured task and local day."""
    return f"{day or today()}|task:{item['id']}"


def digest(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def load_schedule() -> list[dict[str, str]]:
    if not CONFIG_PATH.exists():
        raise SendError("找不到 config.json；请复制 config.example.json 并填写好友和发送时间")
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("friends"), list):
        raise SendError("config.json 必须含有 friends 数组")
    result = []
    for item in data["friends"]:
        if not isinstance(item, dict) or not isinstance(item.get("enabled"), bool):
            raise SendError("每位好友都需要 enabled 布尔值")
        if not item["enabled"]:
            continue
        name, message, send_at = item.get("name"), item.get("message"), item.get("send_at")
        if not all(isinstance(value, str) and value.strip() for value in (name, message, send_at)):
            raise SendError("已启用的好友需要 name、message、send_at")
        try:
            parsed = datetime.strptime(send_at, "%H:%M")
        except ValueError as exc:
            raise SendError(f"{name} 的 send_at 必须是 HH:MM 格式") from exc
        normalized_time = parsed.strftime("%H:%M")
        task_id = item.get("id", f"{name.strip().casefold()}@{normalized_time}")
        if not isinstance(task_id, str) or not task_id.strip():
            raise SendError(f"{name} 的任务 id 必须是非空文字")
        result.append({"id": task_id.strip(), "name": name.strip(), "message": message, "send_at": normalized_time})
    if len({item["id"] for item in result}) != len(result):
        raise SendError("配置中存在重复任务 id；同一好友可以配置多条任务，但每条任务需要不同 id")
    return result


def load_email_settings() -> dict[str, str] | None:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    settings = data.get("email", {})
    if not isinstance(settings, dict):
        raise SendError("邮件配置格式错误")
    if not settings.get("enabled", False):
        return None
    sender = settings.get("qq_address", "")
    recipient = settings.get("recipient", "")
    code = settings.get("authorization_code", "")
    if not all(isinstance(value, str) and value.strip() for value in (sender, recipient, code)):
        raise SendError("邮件已启用，但 QQ 邮箱、收件邮箱或授权码未填写")
    if not sender.lower().endswith("@qq.com") or "@" not in recipient:
        raise SendError("QQ 发件邮箱或收件邮箱格式错误")
    return {"sender": sender.strip(), "recipient": recipient.strip(), "code": code.strip()}


def load_daily_note_settings() -> str | None:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    settings = data.get("daily_note", {})
    if not isinstance(settings, dict):
        raise SendError("每日日期天气配置格式错误")
    if not settings.get("enabled", False):
        return None
    city = settings.get("city", "")
    if not isinstance(city, str) or not city.strip():
        raise SendError("已启用每日日期天气，但尚未填写城市")
    return city.strip()


def due_friends(items: list[dict[str, str]], now: datetime | None = None,
                max_lateness_minutes: int | None = None) -> list[dict[str, str]]:
    current = (now or datetime.now(CHINA_TIME)).astimezone(CHINA_TIME)
    if max_lateness_minutes is None:
        current_time = current.strftime("%H:%M")
        return [item for item in items if item["send_at"] <= current_time]
    result = []
    for item in items:
        hour, minute = (int(part) for part in item["send_at"].split(":"))
        scheduled = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        lateness_minutes = (current - scheduled).total_seconds() / 60
        if 0 <= lateness_minutes <= max_lateness_minutes:
            result.append(item)
    return result


def read_ledger() -> dict[str, dict]:
    if not LEDGER_PATH.exists():
        return {}
    data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SendError("发送记录格式错误，请检查 .spark-state/send-ledger.json")
    return data


def uncertain_today(ledger: dict[str, dict] | None = None) -> list[str]:
    entries = ledger if ledger is not None else read_ledger()
    prefix = f"{today()}|task:"
    return [entry.get("recipient", key) for key, entry in entries.items()
            if key.startswith(prefix) and entry.get("status") == "uncertain"]


def write_ledger(data: dict[str, dict]) -> None:
    ensure_private_state_dir()
    temporary = LEDGER_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.name != "nt":
        os.chmod(temporary, 0o600)
    os.replace(temporary, LEDGER_PATH)


def write_status(status: str, detail: str = "") -> None:
    ensure_private_state_dir()
    temporary = STATUS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "at": datetime.now(CHINA_TIME).isoformat(),
        "status": status,
        "detail": detail,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.name != "nt":
        os.chmod(temporary, 0o600)
    os.replace(temporary, STATUS_PATH)


def read_report_ledger() -> dict[str, dict]:
    if not REPORT_LEDGER_PATH.exists():
        return {}
    data = json.loads(REPORT_LEDGER_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SendError("邮件汇总记录格式错误")
    return data


def write_report_ledger(data: dict[str, dict]) -> None:
    ensure_private_state_dir()
    temporary = REPORT_LEDGER_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.name != "nt":
        os.chmod(temporary, 0o600)
    os.replace(temporary, REPORT_LEDGER_PATH)


def ensure_private_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(STATE_DIR, 0o700)


def record_task_error(item: dict[str, str], reason: str) -> None:
    ledger = read_ledger()
    key = schedule_key(item)
    if key in ledger and ledger[key].get("status") == "uncertain":
        ledger[key]["reason"] = reason[:500]
    elif key not in ledger:
        ledger[key] = {"recipient": item["name"], "status": "failed",
                       "reason": reason[:500], "attempted_at": datetime.now(CHINA_TIME).isoformat()}
    write_ledger(ledger)


def send_batch_report(items: list[dict[str, str]]) -> None:
    settings = load_email_settings()
    if settings is None or not items:
        return
    reports = read_report_ledger()
    report_key = f"{today()}|{items[0]['send_at']}"
    ledger = read_ledger()
    rows = []
    counts = {"sent": 0, "failed": 0, "uncertain": 0}
    labels = {"sent": "成功", "failed": "失败", "uncertain": "结果未确认"}
    for item in items:
        entry = ledger.get(schedule_key(item), {})
        status = entry.get("status", "failed")
        if status not in counts:
            status = "failed"
        counts[status] += 1
        reason = entry.get("reason") or ("刷新会话后已确认己方消息" if status == "sent" else "未留下执行记录，请检查任务日志")
        rows.append(f"- {item['name']}：{labels[status]}；{reason}")
    summary_digest = digest("\n".join(
        f"{item['id']}:{ledger.get(schedule_key(item), {}).get('status', 'failed')}"
        for item in items
    ))
    prior_report = reports.get(report_key, {})
    if prior_report.get("summary_sha256") == summary_digest:
        # An uncertain SMTP submission is not resent for the same summary,
        # but a later change in task outcomes produces an updated report.
        return
    title = f"抖音定时消息汇总 {today()} {items[0]['send_at']}"
    body = (f"北京时间 {today()} {items[0]['send_at']} 的发送结果\n"
            f"成功 {counts['sent']}，失败 {counts['failed']}，结果未确认 {counts['uncertain']}，共 {len(items)} 条。\n\n"
            + "\n".join(rows) + "\n\n结果未确认时请先在抖音聊天记录中核对，避免重复发送。\n")
    message = EmailMessage()
    message["Subject"] = title
    message["From"] = settings["sender"]
    message["To"] = settings["recipient"]
    message.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30,
                              context=ssl.create_default_context()) as smtp:
            smtp.login(settings["sender"], settings["code"])
            # Mark immediately before SMTP DATA; later retries could duplicate a delivered email.
            reports[report_key] = {"status": "uncertain", "at": datetime.now(CHINA_TIME).isoformat(),
                                   "summary_sha256": summary_digest}
            write_report_ledger(reports)
            smtp.send_message(message)
    except Exception as exc:
        reports = read_report_ledger()
        if report_key in reports:
            reports[report_key]["reason"] = f"邮件发送未确认：{type(exc).__name__}: {exc}"[:500]
            write_report_ledger(reports)
        raise SendError(f"汇总邮件发送未确认：{type(exc).__name__}: {exc}") from exc
    reports = read_report_ledger()
    reports[report_key] = {"status": "sent", "at": datetime.now(CHINA_TIME).isoformat(),
                           "recipient": settings["recipient"], "summary_sha256": summary_digest}
    write_report_ledger(reports)
    print(f"已发送汇总邮件：{settings['recipient']}；{title}")


@contextmanager
def single_instance():
    ensure_private_state_dir()
    stream = open(LOCK_PATH, "a+", encoding="utf-8")
    try:
        if os.name != "nt":
            os.chmod(LOCK_PATH, 0o600)
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise SendError("已有发送任务正在运行") from exc
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SendError("已有发送任务正在运行") from exc
        stream.seek(0)
        stream.truncate()
        stream.write(str(os.getpid()))
        stream.flush()
        yield
    finally:
        if not stream.closed:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                with contextlib.suppress(OSError):
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                with contextlib.suppress(OSError):
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()


def browser_context():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SendError("尚未安装 Playwright：运行 pip install -r requirements.txt，再运行 playwright install chromium") from exc
    return sync_playwright()


def open_chat(context):
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=45000)
    return page


def ensure_logged_in(page) -> None:
    try:
        page.locator('input[placeholder="搜索"]').wait_for(state="visible", timeout=30000)
    except Exception as exc:
        raise SendError("聊天列表未出现：请运行 login 扫码登录，或检查网页验证/网络状态") from exc


def headless_login_command(page, command: str) -> None:
    """Allow manual verification without exposing the browser or SMS code."""
    parts = command.strip().split()
    if not parts:
        return
    if parts[0] == "click" and len(parts) == 3:
        try:
            x, y = (int(value) for value in parts[1:])
        except ValueError as exc:
            raise SendError("坐标必须是整数，例如 click 640 400") from exc
        if not (0 <= x < 1280 and 0 <= y < 800):
            raise SendError("坐标应在截图范围内：x=0..1279，y=0..799")
        page.mouse.click(x, y)
    elif parts[0] == "type" and len(parts) == 1:
        value = getpass.getpass("输入验证码或文字（不会显示在终端）：")
        if value:
            page.keyboard.type(value, delay=80)
    elif parts[0] == "press" and len(parts) == 2 and parts[1] in {"Enter", "Tab", "Backspace"}:
        page.keyboard.press(parts[1])
    elif parts[0] == "refresh" and len(parts) == 1:
        page.reload(wait_until="domcontentloaded", timeout=45000)
    else:
        raise SendError("可用命令：click X Y、type、press Enter、press Tab、press Backspace、refresh")


def wait_for_headless_login(page, timeout_seconds: int = 600) -> None:
    """Save login screenshots and accept manual clicks/SMS input via terminal."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(STATE_DIR, 0o700)
    deadline = time.monotonic() + timeout_seconds
    print(f"请从服务器下载并扫描登录页面截图：{LOGIN_SCREENSHOT_PATH}", flush=True)
    print("截图每 3 秒更新；二次验证时可输入 click X Y、type、press Enter 或 refresh。", flush=True)
    print("截图尺寸为 1280×800；type 会隐藏所输入的验证码。登录成功后自动退出。", flush=True)
    while time.monotonic() < deadline:
        if page.locator('input[placeholder="搜索"]').is_visible():
            LOGIN_SCREENSHOT_PATH.unlink(missing_ok=True)
            return
        try:
            page.screenshot(path=str(LOGIN_SCREENSHOT_PATH), timeout=10000)
            os.chmod(LOGIN_SCREENSHOT_PATH, 0o600)
        except Exception as exc:
            raise SendError("无法保存登录页截图；检查浏览器页面和 .spark-state 目录权限") from exc
        try:
            ready, _, _ = select.select([sys.stdin], [], [], 3)
        except (OSError, ValueError):
            page.wait_for_timeout(3000)
            continue
        if ready:
            line = sys.stdin.readline()
            if line:
                try:
                    headless_login_command(page, line)
                except SendError as exc:
                    print(f"操作未执行：{exc}", file=sys.stderr, flush=True)
                except Exception as exc:
                    print(f"页面操作失败：{exc}", file=sys.stderr, flush=True)
            else:
                page.wait_for_timeout(3000)
    raise SendError(f"等待扫码登录超时；检查截图 {LOGIN_SCREENSHOT_PATH}，必要时重新运行 login --headless")


def login_with_sms(page) -> None:
    """Use Douyin's visible phone and SMS form; prompt locally for both values."""
    phone = input("请输入抖音绑定的中国大陆手机号（+86）：").strip()
    if not (phone.isdigit() and len(phone) == 11):
        raise SendError("手机号应为 11 位数字")
    try:
        phone_input = page.get_by_placeholder("请输入手机号", exact=True)
        if not phone_input.is_visible():
            page.get_by_text("登录其他账号", exact=True).filter(visible=True).click(timeout=15000)
        phone_input.wait_for(state="visible", timeout=15000)
        phone_input.fill(phone)
        page.get_by_text("获取验证码", exact=True).filter(visible=True).click(timeout=10000)
        print("已点击“获取验证码”；请查看手机短信。", flush=True)
        code = getpass.getpass("请输入短信验证码（输入时不会显示）：").strip()
        if not (code.isdigit() and 4 <= len(code) <= 8):
            raise SendError("验证码应为 4 至 8 位数字")
        page.get_by_placeholder("请输入验证码", exact=True).fill(code)
        page.locator("#douyin_login_comp_btn_id").click(timeout=10000)
        deadline = time.monotonic() + 30
        device_scan = page.get_by_text("使用原设备扫码", exact=True).filter(visible=True)
        while time.monotonic() < deadline:
            if page.locator('input[placeholder="搜索"]').is_visible():
                break
            if device_scan.count() >= 1:
                device_scan.first.click(timeout=10000)
                print("已选择“使用原设备扫码”；请下载持续更新的登录页截图并用原设备扫码。", flush=True)
                wait_for_headless_login(page)
                break
            page.wait_for_timeout(1000)
        ensure_logged_in(page)
        LOGIN_SCREENSHOT_PATH.unlink(missing_ok=True)
    except Exception as exc:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(LOGIN_SCREENSHOT_PATH), timeout=10000)
            if os.name != "nt":
                os.chmod(STATE_DIR, 0o700)
                os.chmod(LOGIN_SCREENSHOT_PATH, 0o600)
        except Exception:
            pass
        raise SendError(f"短信登录未完成；请检查登录页截图 {LOGIN_SCREENSHOT_PATH}，可能需要处理网页验证、验证码错误或页面已改版") from exc


def normalized_name(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def group_base(value: str) -> str | None:
    """Douyin can append the current member count to a group title."""
    match = re.fullmatch(r"(.+?)[（(]\d+[）)]", normalized_name(value))
    return match.group(1).strip() if match else None


def title_matches(expected: str, actual: str) -> bool:
    expected, actual = normalized_name(expected), normalized_name(actual)
    if actual == expected:
        return True
    expected_base, actual_base = group_base(expected), group_base(actual)
    return (expected_base is not None and actual in (expected_base, expected)) or (
        expected_base is not None and actual_base == expected_base) or (
        actual_base is not None and actual_base == expected)


def visible_search_names(page, query: str) -> list:
    """Only return exact text candidates in the left search-results region."""
    result = []
    search_box = page.locator('input[placeholder="搜索"]').bounding_box()
    viewport = page.locator("body").evaluate("el => ({width: innerWidth, height: innerHeight})")
    if search_box is None:
        return result
    left_limit = min(viewport["width"] * 0.62, search_box["x"] + max(320, search_box["width"] * 1.7))
    names = page.get_by_text(query, exact=True).filter(visible=True).all()
    if group_base(query) is None:
        group_pattern = re.compile(r"^" + re.escape(query) + r"[（(][0-9]+[）)]$")
        names += page.get_by_text(group_pattern).filter(visible=True).all()
    for name in names:
        box = name.bounding_box()
        if box is None or box["y"] + box["height"] / 2 <= search_box["y"] + search_box["height"] or box["y"] >= viewport["height"]:
            continue
        if box["x"] >= left_limit:
            continue
        if name.evaluate("el => Boolean(el.closest('.RightPanelHeadertitle, .MessageItemTextisFromMe, .MessageItemTextisFromOther'))"):
            continue
        result.append(name)
    return result


def first_search_choice(choices: list):
    """Use the first visible result from top to bottom, as requested by the user."""
    positioned = []
    for index, choice in enumerate(choices):
        box = choice.bounding_box()
        if box is not None:
            positioned.append((box["y"], box["x"], index, choice))
    if not positioned:
        raise SendError("搜索结果已出现，但无法读取其页面位置")
    return min(positioned, key=lambda item: item[:3])[3]


def click_search_name(name, query: str) -> None:
    """A contact has a nearby 发消息 action; a group row opens by clicking its name."""
    row = name
    for _ in range(4):
        row = row.locator("xpath=..")
        actions = row.get_by_text("发消息", exact=True).filter(visible=True)
        if actions.count() == 1 and row.get_by_text(query, exact=True).filter(visible=True).count() == 1:
            actions.first.click(timeout=10000)
            return
    name.click(timeout=10000)


def wait_for_recipient_header(page, recipient: str) -> None:
    expected = normalized_name(recipient)
    base = group_base(recipient) or expected
    try:
        page.wait_for_function(
            "({expected, base}) => { const title = document.querySelector('.RightPanelHeadertitle')?.textContent?.trim(); "
            "return title === expected || title === base || (title?.startsWith(base) && "
            "/^[（(][0-9]+[）)]$/.test(title.slice(base.length))); }",
            arg={"expected": expected, "base": base}, timeout=12000,
        )
    except Exception as exc:
        titles = page.locator(".RightPanelHeadertitle").filter(visible=True)
        actual = titles.first.inner_text().strip() if titles.count() else "未打开会话"
        raise SendError(f"点击搜索结果后标题未匹配：预期 {recipient!r}，实际 {actual!r}") from exc
    try:
        page.locator('[contenteditable="true"][data-slate-editor="true"]').wait_for(
            state="visible", timeout=10000)
    except Exception as exc:
        raise SendError(f"已打开 {recipient!r} 的会话，但消息输入框未出现") from exc


def select_recipient(page, recipient: str) -> None:
    search = page.locator('input[placeholder="搜索"]')
    search.wait_for(state="visible", timeout=15000)
    terms = [normalized_name(recipient)]
    base = group_base(recipient)
    if base and base not in terms:
        terms.append(base)
    problems = []
    for term in terms:
        for attempt in range(2):
            try:
                if attempt:
                    page.reload(wait_until="domcontentloaded", timeout=45000)
                    ensure_logged_in(page)
                    search = page.locator('input[placeholder="搜索"]')
                search.fill("")
                search.fill(term)
                deadline = time.monotonic() + 12
                choices = []
                while time.monotonic() < deadline:
                    choices = visible_search_names(page, term)
                    if choices:
                        break
                    page.wait_for_timeout(300)
                if not choices:
                    raise SendError(f"搜索 {term!r} 后没有出现精确匹配的会话、联系人或群聊")
                selected = first_search_choice(choices)
                if len(choices) > 1:
                    print(f"搜索 {term!r} 找到 {len(choices)} 个匹配项，按页面顺序选择第一个：{normalized_name(selected.inner_text())!r}")
                if selected.evaluate("el => Boolean(el.closest('.conversationConversationItemtitle'))"):
                    selected.click(timeout=10000)
                else:
                    click_search_name(selected, term)
                wait_for_recipient_header(page, recipient)
                return
            except SendError as exc:
                problems.append(str(exc))
            except Exception as exc:
                problems.append(f"搜索 {term!r} 第 {attempt + 1} 次失败：{type(exc).__name__}: {str(exc)[:120]}")
            finally:
                try:
                    search.fill("")
                except Exception:
                    pass
    raise SendError(f"未能打开 {recipient!r} 的会话；" + "；".join(problems[-4:]))


def outgoing_count(page, message: str) -> int:
    return page.locator(".MessageItemTextisFromMe .TextMessageTextpureText").get_by_text(
        message, exact=True
    ).count()


def send_once(page, recipient: str, message: str, *, dry_run: bool = False,
              ledger_key: str | None = None, max_attempts: int = 1) -> str:
    if not recipient.strip() or not message.strip():
        raise SendError("好友昵称和消息文字不能为空")
    if max_attempts < 1 or max_attempts > 5:
        raise SendError("最多只能尝试发送 5 次")
    ensure_logged_in(page)
    select_recipient(page, recipient)
    # data-placeholder is present only while the editor is empty; Slate removes
    # it as soon as text is entered.
    editor = page.locator('[contenteditable="true"][data-slate-editor="true"]')
    if editor.count() != 1:
        raise SendError("无法唯一定位消息输入框，页面可能已改版")
    if dry_run:
        return "recipient_verified"

    ledger = read_ledger() if ledger_key is not None else None
    previous = ledger.get(ledger_key) if ledger_key is not None else None
    if previous and previous.get("status") not in {"uncertain", "failed"}:
        raise SendError(f"这条定时任务今天已执行，状态={previous['status']}")
    if previous and previous.get("status") == "uncertain" and (
                     previous.get("message_sha256") != digest(message)
                     or not isinstance(previous.get("baseline_count"), int)):
        raise SendError("旧的待核对记录缺少本次消息基线；请先人工核对，避免重复发送")
    # A failed entry is written only when no click was recorded. It is safe to
    # retry within the lateness window and starts a fresh reconciliation record.
    if previous and previous.get("status") == "failed":
        previous = None
    attempts_done = int(previous.get("attempts", 0)) if previous else 0
    if attempts_done >= max_attempts:
        raise SendError(f"今天已达到 {max_attempts} 次发送上限，请人工核对")
    before = previous["baseline_count"] if previous else outgoing_count(page, message)

    def mark_sent() -> str:
        if ledger_key is not None:
            current = read_ledger()
            current[ledger_key]["status"] = "sent"
            current[ledger_key]["confirmed_at"] = datetime.now(CHINA_TIME).isoformat()
            current[ledger_key]["reason"] = f"刷新会话后已确认己方消息；共尝试 {current[ledger_key]['attempts']} 次"
            write_ledger(current)
        return "sent"

    # A previous click may have succeeded just before a crash. Reconcile it
    # after a fresh page load before considering another click.
    if previous:
        page.reload(wait_until="domcontentloaded", timeout=45000)
        ensure_logged_in(page)
        select_recipient(page, recipient)
        if outgoing_count(page, message) > before:
            return mark_sent()

    last_reason = ""
    for attempt in range(attempts_done + 1, max_attempts + 1):
        if attempt > attempts_done + 1:
            page.wait_for_timeout(2000)
            page.reload(wait_until="domcontentloaded", timeout=45000)
            ensure_logged_in(page)
            select_recipient(page, recipient)
            if outgoing_count(page, message) > before:
                return mark_sent()
        editor = page.locator('[contenteditable="true"][data-slate-editor="true"]')
        if editor.count() != 1:
            raise SendError("无法唯一定位消息输入框，页面可能已改版")
        draft = editor.inner_text().replace("\u200b", "").replace("\ufeff", "").strip()
        if draft and draft != message:
            raise SendError("输入框已有不同的草稿，已停止发送；请先人工处理草稿")
        if not draft:
            editor.click()
            page.keyboard.insert_text(message)
        actual = editor.inner_text().replace("\u200b", "").replace("\ufeff", "").strip()
        if actual != message:
            raise SendError("输入框内容与预设文字不一致，已停止发送")
        if not title_matches(recipient, page.locator(".RightPanelHeadertitle").inner_text()):
            raise SendError("发送前会话标题发生变化，已停止发送")
        if ledger_key is not None:
            current = read_ledger()
            current[ledger_key] = {
                "recipient": recipient, "message_sha256": digest(message),
                "rendered_message": message,
                "baseline_count": before, "status": "uncertain", "attempts": attempt,
                "attempted_at": datetime.now(CHINA_TIME).isoformat(),
            }
            write_ledger(current)
        try:
            page.locator(".e2e-send-msg-btn").click(timeout=10000)
            page.wait_for_function(
                "({text, before}) => Array.from(document.querySelectorAll('.MessageItemTextisFromMe .TextMessageTextpureText')).filter(el => el.textContent.trim() === text).length > before",
                arg={"text": message, "before": before}, timeout=12000,
            )
        except Exception as exc:
            last_reason = f"点击后页面未出现可确认的己方消息：{type(exc).__name__}: {str(exc)[:120]}"
        # The immediate bubble may be optimistic. Check the persisted chat.
        try:
            page.reload(wait_until="domcontentloaded", timeout=45000)
            ensure_logged_in(page)
            select_recipient(page, recipient)
            page.wait_for_function(
                "({text, before}) => Array.from(document.querySelectorAll('.MessageItemTextisFromMe .TextMessageTextpureText')).filter(el => el.textContent.trim() === text).length > before",
                arg={"text": message, "before": before}, timeout=15000,
            )
            return mark_sent()
        except Exception as exc:
            last_reason = f"点击后刷新会话，仍未确认消息已保存：{type(exc).__name__}: {str(exc)[:120]}"
            if ledger_key is not None:
                current = read_ledger()
                current[ledger_key]["reason"] = f"{last_reason}；已尝试 {attempt}/{max_attempts} 次"
                write_ledger(current)
    raise SendError(f"{last_reason}；已尝试 {max_attempts} 次，结果仍未确认")


def run_daily(*, headed: bool, max_lateness_minutes: int | None) -> int:
    with single_instance():
        due = due_friends(load_schedule(), max_lateness_minutes=max_lateness_minutes)
        if not due:
            print("当前没有到时的发送任务")
            write_status("idle", "当前没有到时的发送任务")
            return 0
        ledger = read_ledger()
        pending = [item for item in due if schedule_key(item) not in ledger or (
            ledger[schedule_key(item)].get("status") == "failed"
            or (ledger[schedule_key(item)].get("status") == "uncertain"
                and int(ledger[schedule_key(item)].get("attempts", 5)) < 5))]
        if pending:
            city = load_daily_note_settings()
            note = ""
            if city:
                note, weather_warning = build_daily_note(city, datetime.now(CHINA_TIME))
                if weather_warning:
                    print(weather_warning, file=sys.stderr)
            try:
                with browser_context() as playwright:
                    context = playwright.chromium.launch_persistent_context(
                        str(PROFILE_DIR), headless=not headed,
                        viewport={"width": 1280, "height": 800},
                        locale="zh-CN", timezone_id="Asia/Shanghai")
                    try:
                        page = open_chat(context)
                        for item in pending:
                            try:
                                entry = read_ledger().get(schedule_key(item), {})
                                message = entry.get("rendered_message") or (
                                    append_daily_note(item["message"], note) if note else item["message"])
                                result = send_once(page, item["name"], message,
                                                   ledger_key=schedule_key(item), max_attempts=5)
                                print(f"结果：{result}；好友：{item['name']}")
                            except Exception as exc:
                                record_task_error(item, str(exc))
                                print(f"失败：好友 {item['name']}：{exc}", file=sys.stderr)
                    finally:
                        context.close()
            except Exception as exc:
                for item in pending:
                    record_task_error(item, f"浏览器或页面启动失败：{exc}")
                print(f"浏览器运行失败：{exc}", file=sys.stderr)
        report_error = None
        for send_at in sorted({item["send_at"] for item in due}):
            group = [item for item in due if item["send_at"] == send_at]
            try:
                send_batch_report(group)
            except Exception as exc:
                report_error = str(exc)
                print(f"邮件通知失败：{exc}", file=sys.stderr)
        ledger = read_ledger()
        failed = [item["name"] for item in due if ledger.get(schedule_key(item), {}).get("status") == "failed"]
        uncertain = [item["name"] for item in due if ledger.get(schedule_key(item), {}).get("status") == "uncertain"]
        if report_error:
            write_status("report_failed", report_error)
            return 1
        if uncertain:
            write_status("needs_review", f"发送结果未确认：{', '.join(uncertain)}")
            return 1
        if failed:
            write_status("failed", f"发送失败：{', '.join(failed)}")
            return 1
        write_status("sent", f"已确认 {len(due)} 条定时任务的发送结果")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抖音网页版单次发信测试")
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="打开独立浏览器配置文件，扫码登录")
    login.add_argument("--headless", action="store_true", help="无桌面服务器：周期保存登录页截图，供远程扫码")
    login.add_argument("--sms", action="store_true", help="使用手机号和短信验证码登录")
    send = sub.add_parser("send", help="给已有会话的好友发送一次文字")
    send.add_argument("--to", required=True, help="好友聊天标题，必须完全匹配")
    send.add_argument("--message", required=True, help="要发送的文字")
    send.add_argument("--dry-run", action="store_true", help="仅核对好友和输入框，不发信")
    send.add_argument("--headless", action="store_true", help="使用无界面浏览器")
    run = sub.add_parser("run", help="执行 config.json 中今天已到时间且尚未发送的任务")
    run.add_argument("--headed", action="store_true", help="使用可见浏览器，需要图形桌面")
    run.add_argument("--max-lateness-minutes", type=int, help="仅执行已到时间且不超过指定迟到分钟数的任务")
    args = parser.parse_args(argv)
    if args.command == "run" and args.max_lateness_minutes is not None and args.max_lateness_minutes < 0:
        parser.error("--max-lateness-minutes 不能是负数")

    if args.command == "run":
        try:
            return run_daily(headed=args.headed, max_lateness_minutes=args.max_lateness_minutes)
        except (Exception, KeyboardInterrupt) as exc:
            write_status("failed", str(exc))
            print(f"失败：{exc}", file=sys.stderr)
            return 1

    try:
        with single_instance(), browser_context() as playwright:
            headless = args.headless
            context = playwright.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                headless=headless,
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            try:
                page = open_chat(context)
                if args.command == "login":
                    if args.sms:
                        login_with_sms(page)
                    elif args.headless:
                        wait_for_headless_login(page)
                    else:
                        print("请在打开的浏览器中扫码登录，看到聊天列表后回到终端按 Enter。")
                        input()
                        ensure_logged_in(page)
                    print("登录会话已保存在本机 .spark-state/browser-profile")
                elif args.command == "send":
                    result = send_once(page, args.to.strip(), args.message, dry_run=args.dry_run)
                    print(f"结果：{result}；好友：{args.to}")
            finally:
                context.close()
    except (Exception, KeyboardInterrupt) as exc:
        if args.command == "run":
            write_status("failed", str(exc))
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
