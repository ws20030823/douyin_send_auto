# 抖音自动发消息（续火花）工具

一个使用 Playwright 操作抖音网页版聊天的个人自动化工具，可用于给联系人或群聊定时发送消息、帮助维持抖音聊天火花（续火花）。可以配置聊天对象、消息和每天发送时间，并在发送后核对聊天记录、发送结果邮件汇总。

项目目前仍是基于网页版界面的个人工具。抖音网页结构、登录验证和自动化规则可能变化；请先用少量任务验证登录与发送，再启用定时任务。云服务器部署步骤见 [Linux 部署指南](LINUX_DEPLOY.md)。

## 目录

- [项目简介](#项目简介)
- [功能特性](#功能特性)
- [工作方式与限制](#工作方式与限制)
- [运行环境](#运行环境)
- [安装步骤](#安装步骤)
- [首次登录](#首次登录)
- [配置任务](#配置任务)
- [手动测试与运行](#手动测试与运行)
- [设置每天自动执行](#设置每天自动执行)
- [邮件和天气配置](#邮件和天气配置)
- [发送状态与重试规则](#发送状态与重试规则)
- [常见问题](#常见问题)
- [目录结构](#目录结构)

## 项目简介

程序在已登录的抖音网页版中查找联系人或群聊，按配置发送文字消息，并检查消息是否出现在自己的聊天记录中。适合在个人设备或自己的 Linux 云服务器上，按固定时间给已确认的聊天对象发送固定内容，可用于日常续火花。程序只能核对消息是否出现在聊天记录中，火花是否成功续上以抖音客户端显示为准。

所有时间均按北京时间（Asia/Shanghai）处理。消息发送对象、文本和时间由本地 `config.json` 配置。

## 功能特性

- 支持向联系人单聊和群聊发送文字。
- 每条任务可独立设置聊天名称、消息内容、每天发送时间和启用状态。
- 发送前核对聊天标题与输入框，发送后刷新会话并检查己方消息记录。
- 定时运行时按任务 ID 和北京时间日期去重；同一聊天可以配置多条不同任务。
- 发送结果未确认时自动刷新核对，最多点击发送 5 次；明确在点击发送前失败的任务可在迟到窗口内重试。
- 可将每条消息追加日期、星期和指定城市的天气预报。
- 可在同一发送时间的任务完成后发送 QQ 邮箱汇总，并在结果状态变化时发送更新。
- 支持 Linux `systemd` 定时运行；也可以在 Windows 上手动运行。

## 工作方式与限制

程序通过 Playwright 控制 Chromium 中可见的网页元素，不调用抖音私有接口。它使用固定浏览器配置目录保存登录会话，因此后续运行通常不需要每天扫码；抖音要求重新验证、会话过期或更换服务器时仍需本人重新登录。

这是依赖网页布局的自动化程序。抖音改版、登录验证、网络延迟或页面未及时显示消息，都可能导致任务失败或结果无法确认。结果未确认时可能发生重复消息，启用自动发送前请先接受这一风险，并在聊天记录中核对。

自动化操作可能受到抖音用户协议和平台策略限制，也可能影响账号安全或可用性。请只用于自己的账号和已同意接收消息的对象，控制发送范围，并自行评估账号风险。程序遇到登录验证或页面变化时会停止，不会尝试绕过验证。

## 运行环境

推荐的云端环境：

- Ubuntu 24.04 LTS，x86_64
- 建议至少 2 核 CPU、4 GB 内存
- Python 3.10 或更新版本
- Playwright 与 Chromium，以及 Chromium 所需的系统依赖

## 安装步骤

以下以 Ubuntu 为例。将仓库克隆到服务用户主目录下的 `douyin-spark`，以匹配随附的 systemd 单元文件：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
git clone https://github.com/ws20030823/douyin_send_auto.git ~/douyin-spark
cd ~/douyin-spark
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
sudo .venv/bin/python -m playwright install-deps chromium
.venv/bin/python -m playwright install chromium
```
本地 Windows 安装可在项目目录的 PowerShell 中执行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

## 首次登录

在实际运行发送任务的机器上登录。Linux 服务器可以先尝试短信登录：

```bash
cd ~/douyin-spark
.venv/bin/python spark_sender.py login --sms --headless
```

按终端提示输入手机号和短信验证码。如果抖音要求用原设备扫码，程序会将登录页面截图保存到 `.spark-state/login-qr.png`；详细下载截图和处理二次验证的步骤见 [Linux 部署指南](LINUX_DEPLOY.md)。也可以直接运行 `login --headless` 使用扫码登录。

登录会话保存在 `.spark-state/browser-profile`。请使用同一个 Linux 用户运行登录和定时任务，勿上传或分享 `.spark-state`。如果会话过期或要求重新验证，再运行登录命令并由账号本人完成验证。

## 配置任务

启动本机配置页：

```bash
cd ~/douyin-spark
.venv/bin/python config_page.py
```

然后在服务器本机浏览器打开 `http://127.0.0.1:8765/`。若服务器没有桌面环境，可按[部署指南中的 SSH 端口转发步骤](LINUX_DEPLOY.md#4-配置页面)，从自己的电脑安全访问配置页。不要在云服务器安全组中开放 8765 端口。

配置项示例：

```json
{
  "friends": [
    {
      "id": "moonbird-morning",
      "name": "moonbird",
      "message": "早上好，今天也要开心！",
      "send_at": "09:00",
      "enabled": false
    }
  ],
  "daily_note": {
    "enabled": false,
    "city": "重庆"
  },
  "email": {
    "enabled": false,
    "qq_address": "",
    "recipient": "",
    "authorization_code": ""
  }
}
```

- `id`：每条任务的唯一标识。同一联系人可有多条任务，但 ID 必须不同。
- `name`：网页搜索结果中显示的联系人或群聊名称。群聊只填群名称，不需要在配置中写成员数量。
- `message`：要发送的文字。
- `send_at`：每天的发送时间，使用 24 小时制 `HH:MM`。
- `enabled`：是否启用。新增配置建议先设为 `false`，完成核对后再启用。

若搜索到多个同名结果，程序按页面从上到下选择第一个。请先用 `--dry-run` 核对右侧聊天标题；若无法唯一判断目标，先调整搜索名称，避免发给错误对象。

配置页保存后会写入 `config.json`。它含有消息文本和可能存在的邮箱授权码，应只保存在自己的设备或服务器上。该文件已被 Git 忽略；不要提交到 GitHub、部署 ZIP 或聊天消息中。Linux 服务器可执行 `chmod 600 ~/douyin-spark/config.json` 限制读取权限。

## 手动测试与运行

先进行不发送消息的对象核对：

```bash
.venv/bin/python spark_sender.py send --to 'moonbird' --message '测试消息' --dry-run --headless
```

确认会话标题和输入框正确后，才运行真实发送：

```bash
.venv/bin/python spark_sender.py send --to 'moonbird' --message '测试消息' --headless
```

手动 `send` 每次执行一次发送，不触发汇总邮件，也不受定时任务的每日 ID 去重限制。显示 `sent` 后仍建议在抖音客户端核对消息。

手动执行配置中当前到时的任务：

```bash
.venv/bin/python spark_sender.py run
```

若需模拟定时器的 10 分钟迟到窗口：

```bash
.venv/bin/python spark_sender.py run --max-lateness-minutes 10
```

## 设置每天自动执行

仓库提供了 `deploy/systemd/douyin-spark.service` 和 `.timer`。确认登录和无界面发送已在服务器上手动测试后，以安装项目的普通用户执行：

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/douyin-spark.service deploy/systemd/douyin-spark.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now douyin-spark.timer
sudo loginctl enable-linger "$USER"
systemctl --user list-timers douyin-spark.timer
```

定时器每 5 分钟检查一次任务，只处理设定时间后 10 分钟内到期的任务。服务器在发送时间离线超过 10 分钟时，当天会跳过该任务。登录会话和定时任务应始终使用同一个 Linux 用户。

查看定时器和执行日志：

```bash
systemctl --user status douyin-spark.timer
journalctl --user -u douyin-spark.service -n 50 --no-pager
cat .spark-state/last-run.json
```

停止定时任务：

```bash
systemctl --user disable --now douyin-spark.timer
```

更完整的服务器准备、登录、测试和排错步骤见 [Linux 部署指南](LINUX_DEPLOY.md)。

## 邮件和天气配置

在配置页开启“发送结果邮件”，填写 QQ 发件邮箱、收件邮箱和 QQ 邮箱 SMTP 授权码。需要先在 QQ 邮箱设置中开启 SMTP 并生成授权码；这里不能填写 QQ 登录密码。定时 `run` 会在同一发送时间的任务处理后发送汇总。手动 `send` 不发送邮件。

开启“每日日期与天气”并填写城市后，程序会在自定义消息后附加北京时间日期和星期，以及 Open-Meteo 提供的当天预报。天气接口或城市查询不可用时，仍会附加日期和星期，不附加天气内容。天气属于预报信息。

## 发送状态与重试规则

- 定时任务以 `id` 和北京时间日期去重。同一任务当天确认发送后不会重复执行；不同任务可以向同一聊天发送多条消息。
- 发送后程序会刷新并检查己方消息记录，确认后记为 `sent`。
- 明确在点击发送前失败的任务，会在 10 分钟迟到窗口内由下一次定时运行重试。
- 点击后无法确认时，程序会刷新核对并最多尝试 5 次；仍不确定时记录为 `uncertain`，应先人工检查聊天记录再决定是否重发。
- 网络延迟或网页未显示已发消息时，重复尝试可能造成重复消息。
- 汇总邮件按结果状态去重；若某项结果之后发生变化，会发送更新汇总。邮件提交结果本身不明确时，不会自动重复发送同一份结果汇总。

最近一次运行状态保存在 `.spark-state/last-run.json`，定时任务发送记录保存在 `.spark-state/send-ledger.json`。Linux 上这些状态文件仅当前运行用户可读写。

## 常见问题

### 提示聊天列表未出现或登录失效

在同一台机器、同一个系统用户下重新运行 `login` 并完成扫码或平台要求的验证。脚本使用自己的 `.spark-state/browser-profile`，不会共享其他浏览器的登录状态。

### 提示未能打开单聊或群聊

先在网页版搜索并确认目标名称。检查结果是否有同名对象、群聊人数后缀是否变化，以及右侧标题是否对应预期聊天。可以先用 `send --dry-run` 测试；不要在目标不明确时真实发送。

### 邮件没有收到

检查 `config.json` 中邮件是否启用，发件地址、收件地址和 SMTP 授权码是否正确；确认服务器可连接 `smtp.qq.com:465`。同时查看 `journalctl` 日志和邮箱垃圾箱。邮件提交状态不明确时，先检查邮箱再决定后续操作。

### 天气没有追加

日期和星期仍会添加；天气查询失败时程序会在运行日志输出原因。请检查服务器网络、城市名称和 Open-Meteo 服务状态。

### 如何检查是否运行到时任务

查看 `systemctl --user list-timers douyin-spark.timer`、服务日志和 `.spark-state/last-run.json`。配置中的 `send_at` 使用北京时间 `HH:MM`；测试时确认服务器和程序时区设置正确。

## 目录结构

```text
.
├── spark_sender.py             # 登录、收件人定位、发送和定时执行
├── config_page.py              # 本地配置页服务
├── config.html                 # 配置页面结构
├── config.css                  # 配置页面样式
├── config.js                   # 配置页面交互
├── daily_note.py               # 日期、星期和天气附加
├── config.example.json         # 不含真实凭据的配置示例
├── requirements.txt            # Python 依赖
├── deploy/systemd/              # systemd 服务和定时器
├── config.json                 # 本机配置，不应上传
└── .spark-state/                # 登录资料和运行状态，不应上传
```
