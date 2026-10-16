"""Local smoke test for UI sending and duplicate protection; no Douyin network use."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import spark_sender


FAKE_CHAT = """
<input placeholder="搜索">
<div class="conversationConversationItemtitle" onclick="document.querySelector('.RightPanelHeadertitle').textContent='moonbird'">moonbird</div>
<div class="RightPanelHeadertitle">moonbird</div>
<div contenteditable="true" data-slate-editor="true" data-placeholder="发送消息" oninput="this.removeAttribute('data-placeholder')"></div>
<svg class="e2e-send-msg-btn" onclick="
  const editor=document.querySelector('[contenteditable]');
  if (window.persistMessages !== false) {
    const saved=JSON.parse(localStorage.getItem('messages') || '[]');
    saved.push(editor.textContent);
    localStorage.setItem('messages',JSON.stringify(saved));
  }
  const message=document.createElement('div');
  message.className='MessageItemTextisFromMe';
  const text=document.createElement('span');
  text.className='TextMessageTextpureText';
  text.textContent=editor.textContent;
  message.appendChild(text);
  document.body.appendChild(message);
  editor.textContent='';
  editor.setAttribute('data-placeholder','发送消息');
"></svg>
<script>
for (const value of JSON.parse(localStorage.getItem('messages') || '[]')) {
  const message=document.createElement('div');
  message.className='MessageItemTextisFromMe';
  const text=document.createElement('span');
  text.className='TextMessageTextpureText';
  text.textContent=value;
  message.appendChild(text);
  document.body.appendChild(message);
}
</script>
"""

FAKE_CONTACT = """
<input placeholder="搜索">
<div class="contact"><span>moonbird</span><button onclick="
  document.querySelector('.contact').style.display='none';
  document.querySelector('.panel').style.display='block';
">发消息</button></div>
<div class="panel" style="display:none">
  <div class="RightPanelHeadertitle">moonbird</div>
  <div contenteditable="true" data-slate-editor="true" data-placeholder="发送消息"></div>
</div>
"""


class SenderTest(unittest.TestCase):
    def test_contact_search_opens_one_to_one_chat(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(FAKE_CONTACT)
                page.locator("body").evaluate("el => el.insertAdjacentHTML('beforeend', '<div class=\"RightPanelHeadertitle\">moonbird</div><div class=\"MessageItemTextisFromOther\">moonbird</div>')")
                choices = spark_sender.visible_search_names(page, "moonbird")
                self.assertEqual(len(choices), 1)
                self.assertEqual(choices[0].evaluate("el => el.tagName"), "SPAN")
                self.assertEqual(
                    spark_sender.send_once(page, "moonbird", "测试", dry_run=True),
                    "recipient_verified",
                )
            finally:
                browser.close()

    def test_browser_profile_keeps_login_cookie_between_runs(self):
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(directory, headless=True)
            context.add_cookies([{
                "name": "session", "value": "saved", "url": "https://example.test",
                "expires": int(time.time()) + 3600,
            }])
            context.close()
            context = playwright.chromium.launch_persistent_context(directory, headless=True)
            try:
                cookies = context.cookies("https://example.test")
                self.assertTrue(any(cookie["name"] == "session" and cookie["value"] == "saved" for cookie in cookies))
            finally:
                context.close()

    def test_manual_send_can_repeat_to_same_recipient(self):
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch.object(spark_sender, "STATE_DIR", base), patch.object(
                spark_sender, "LEDGER_PATH", base / "ledger.json"
            ), sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.route("https://fake.test/chat", lambda route: route.fulfill(body=FAKE_CHAT, content_type="text/html; charset=utf-8"))
                    page.goto("https://fake.test/chat")
                    self.assertEqual(spark_sender.send_once(page, "moonbird", "测试"), "sent")
                    self.assertEqual(page.locator('[data-slate-editor="true"]').count(), 1)
                    self.assertEqual(spark_sender.outgoing_count(page, "测试"), 1)
                    self.assertEqual(spark_sender.send_once(page, "moonbird", "测试"), "sent")
                    self.assertEqual(spark_sender.outgoing_count(page, "测试"), 2)
                finally:
                    browser.close()

    def test_lock_automatically_recovers_from_stale_lock_file(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            lock = base / "sender.lock"
            lock.write_text("stale pid", encoding="utf-8")
            with patch.object(spark_sender, "STATE_DIR", base), patch.object(spark_sender, "LOCK_PATH", lock):
                with spark_sender.single_instance():
                    with self.assertRaisesRegex(spark_sender.SendError, "已有发送任务"):
                        with spark_sender.single_instance():
                            pass
                # A second run can acquire the same file after the first releases it.
                with spark_sender.single_instance():
                    pass

    def test_report_deduplicates_same_summary_but_sends_updated_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            item = {"id": "task-1", "name": "moonbird", "send_at": "09:00"}
            key = spark_sender.schedule_key(item, "2026-10-16")
            ledger_path = base / "send-ledger.json"
            report_path = base / "report-ledger.json"
            smtp = MagicMock()
            smtp.__enter__.return_value = smtp
            with patch.object(spark_sender, "STATE_DIR", base), patch.object(
                spark_sender, "LEDGER_PATH", ledger_path
            ), patch.object(spark_sender, "REPORT_LEDGER_PATH", report_path), patch.object(
                spark_sender, "load_email_settings",
                return_value={"sender": "sender@qq.com", "recipient": "recipient@qq.com", "code": "secret"},
            ), patch.object(spark_sender.smtplib, "SMTP_SSL", return_value=smtp):
                spark_sender.write_ledger({key: {"recipient": "moonbird", "status": "failed", "reason": "未打开会话"}})
                with patch.object(spark_sender, "today", return_value="2026-10-16"):
                    spark_sender.send_batch_report([item])
                    spark_sender.send_batch_report([item])
                    self.assertEqual(smtp.send_message.call_count, 1)
                    spark_sender.write_ledger({key: {"recipient": "moonbird", "status": "sent", "reason": "已确认"}})
                    spark_sender.send_batch_report([item])
                    self.assertEqual(smtp.send_message.call_count, 2)

    def test_matching_existing_draft_is_not_appended(self):
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch.object(spark_sender, "STATE_DIR", base), patch.object(
                spark_sender, "LEDGER_PATH", base / "ledger.json"
            ), sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.route("https://fake.test/chat", lambda route: route.fulfill(body=FAKE_CHAT, content_type="text/html; charset=utf-8"))
                    page.goto("https://fake.test/chat")
                    page.locator('[data-slate-editor="true"]').fill("测试")
                    self.assertEqual(spark_sender.send_once(page, "moonbird", "测试"), "sent")
                    self.assertEqual(spark_sender.outgoing_count(page, "测试"), 1)
                finally:
                    browser.close()

    def test_different_existing_draft_is_preserved(self):
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch.object(spark_sender, "STATE_DIR", base), patch.object(
                spark_sender, "LEDGER_PATH", base / "ledger.json"
            ), sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.route("https://fake.test/chat", lambda route: route.fulfill(body=FAKE_CHAT, content_type="text/html; charset=utf-8"))
                    page.goto("https://fake.test/chat")
                    page.locator('[data-slate-editor="true"]').fill("别的草稿")
                    with self.assertRaisesRegex(spark_sender.SendError, "已有不同的草稿"):
                        spark_sender.send_once(page, "moonbird", "测试")
                    self.assertEqual(page.locator('[data-slate-editor="true"]').inner_text(), "别的草稿")
                    self.assertEqual(spark_sender.outgoing_count(page, "测试"), 0)
                finally:
                    browser.close()

    def test_optimistic_bubble_without_persistence_is_uncertain(self):
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch.object(spark_sender, "STATE_DIR", base), patch.object(
                spark_sender, "LEDGER_PATH", base / "ledger.json"
            ), sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.route("https://fake.test/chat", lambda route: route.fulfill(body=FAKE_CHAT, content_type="text/html; charset=utf-8"))
                    page.goto("https://fake.test/chat")
                    page.evaluate("window.persistMessages = false")
                    with self.assertRaisesRegex(spark_sender.SendError, "刷新会话，仍未确认"):
                        spark_sender.send_once(page, "moonbird", "测试", ledger_key="2026-10-15|task:test")
                    ledger = spark_sender.read_ledger()
                    self.assertEqual(next(iter(ledger.values()))["status"], "uncertain")
                    self.assertEqual(spark_sender.outgoing_count(page, "测试"), 0)
                finally:
                    browser.close()


if __name__ == "__main__":
    unittest.main()
