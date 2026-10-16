# Linux 服务器部署（Ubuntu 24.04 LTS）

建议选 x86_64、4 GB 内存的 Ubuntu 24.04 LTS 云服务器。项目使用 Playwright Chromium；网页版在云服务器的无界面模式下能否维持登录和发信，仍须在实际服务器上验证。先完成下面的手动发信检查，再启用定时器。

如果购买时选到了 OpenCloudOS 镜像，请先核对 `/etc/os-release`。下述 `apt` 命令适用于 Ubuntu；Playwright 官方列出的 Linux 支持环境是 Ubuntu 和 Debian，OpenCloudOS 不在该列表中。对于尚未部署项目的新服务器，建议在腾讯云控制台选择“重装系统”，切换到 Ubuntu 24.04 LTS 后再按此文档操作。重装会清除服务器现有数据，先确认实例上没有需要保留的文件。

## 1. 准备项目

通过 SSH 登录服务器，使用一个普通用户（不要用 root）把项目放在 `~/douyin-spark`。可上传项目生成的 `douyin-spark-linux.zip`，在该用户主目录执行 `mkdir -p ~/douyin-spark && cd ~/douyin-spark && unzip ~/douyin-spark-linux.zip`。上传包只包含源码、配置样例和部署文件，不含本机 `config.json`、`.venv` 或 `.spark-state`。若另行复制 `config.json`，先将所有任务设为 `"enabled": false`。

在服务器上运行：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip unzip
cd ~/douyin-spark
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
sudo .venv/bin/python -m playwright install-deps chromium
.venv/bin/python -m playwright install chromium
```

安装浏览器及系统依赖的命令来自 [Playwright 官方文档](https://playwright.dev/python/docs/browsers)。浏览器以安装它的普通用户运行，因此扫码和定时任务也必须使用这个用户。

## 2. 登录（短信或扫码）

如果抖音账号绑定的是中国大陆手机号，优先直接使用短信登录：

```bash
cd ~/douyin-spark
.venv/bin/python spark_sender.py login --sms --headless
```

程序会询问手机号，自动点击“登录其他账号”“获取验证码”，然后在终端询问短信验证码并提交。验证码输入不会显示在终端。如果短信提交后出现身份验证页，程序会自动选择“使用原设备扫码”，每 3 秒更新 `.spark-state/login-qr.png`；从腾讯云文件管理下载最新截图，用原来登录抖音的设备扫码，终端会等待最多 10 分钟。成功时会保存浏览器会话并退出。如果网站要求滑块、图形或其他验证，程序会停止并将页面保存在 `.spark-state/login-qr.png` 供检查；不会绕过验证。此时可以改用下面的截图扫码路径或临时图形桌面。

### 截图扫码备选方式

在第一个 SSH 窗口运行：

```bash
cd ~/douyin-spark
.venv/bin/python spark_sender.py login --headless
```

程序每 3 秒更新 `.spark-state/login-qr.png`。在本机**另开一个终端**，将截图下载后用抖音手机端扫码：

```powershell
scp 用户名@服务器公网IP:~/douyin-spark/.spark-state/login-qr.png .\login-qr.png
```

如果截图里尚未出现二维码或二维码过期，等几秒后再次下载。扫码后若出现短信二次验证，重新下载截图，按原图像素坐标在第一个终端输入 `click X Y` 点选短信验证及验证码输入框；输入 `type` 后，在隐藏输入提示中填写验证码；需要提交时输入 `press Enter`，或用 `click X Y` 点页面按钮。截图原始尺寸为 1280×800，每次操作后重新下载查看。`refresh` 可刷新登录页。验证码仅在服务器终端输入，不要发给别人。登录成功时第一个窗口会自动显示成功并退出；等待上限为 10 分钟。截图位于含登录资料的私有目录，不要将它或 `.spark-state/browser-profile` 上传到仓库或公开网站。以后会复用此目录中的会话；抖音要求重新验证、会话过期或目录丢失时，需要重新扫码，不能保证永久免扫。

如果截图始终没有可扫码的登录页，或验证页面无法通过坐标点击操作，这条无桌面登录路径在该服务器上不可用。先不要启用定时器；可临时配置图形桌面远程登录并运行不带 `--headless` 的 `login`（需要另装完整 Chromium），或者改用 Windows Server。

## 3. 实机核对无界面发信

登录成功后，先只核对收件人，再由你决定是否发送一条真实测试消息：

```bash
.venv/bin/python spark_sender.py send --to 'moonbird' --message '云服务器发信测试' --dry-run --headless
.venv/bin/python spark_sender.py send --to 'moonbird' --message '云服务器发信测试' --headless
```

第二条命令会真的发送，请把昵称和文字换成你的测试对象，并在手机端核对送达及火花变化。若登录失效或网页要求验证，停止自动运行并本人重新扫码；若发送结果不明，先查看聊天记录，再决定是否重发。

## 4. 配置页面

`config_page.py` 只监听服务器的 `127.0.0.1:8765`。在服务器上运行：

```bash
cd ~/douyin-spark
.venv/bin/python config_page.py
```

在本机另一终端建立 SSH 端口转发：

```powershell
ssh -L 8765:127.0.0.1:8765 用户名@服务器公网IP
```

本机浏览器打开 `http://127.0.0.1:8765/`，填写昵称、文字和发送时间，保存后在服务器检查 `config.json`。不要在云服务器安全组中开放 8765 端口。定时任务按**北京时间**判断；同一好友可以配置多个不同任务 `id`，但每个任务每天只自动尝试一次。手动 `send` 不受这个限制。

配置页下方可以开启“发送结果邮件”。QQ 发件邮箱填写完整 `xxx@qq.com`，收件邮箱可与发件邮箱相同。先到 QQ 邮箱网页设置中开启 SMTP 并生成**授权码**，然后把授权码填入配置页；不要填 QQ 登录密码，也不要在聊天中发送授权码。保存后 `config.json` 包含授权码，请只通过安全连接传到服务器，并在服务器执行 `chmod 600 ~/douyin-spark/config.json`。每个设定时间的所有任务处理完后，程序通过 `smtp.qq.com:465` 发送一封汇总，列出成功、失败、结果未确认及可判断的原因。QQ 邮箱的 SMTP 地址、端口和授权码说明可参考[腾讯云文档](https://main.qcloudimg.com/raw/document/product/pdf/1270_46586_cn.pdf)。

更新搜索与群聊发送逻辑时，只需将新版 ZIP 上传到服务器主目录并在 `~/douyin-spark` 中执行 `unzip -o ~/douyin-spark-linux.zip`。部署包不含 `config.json` 和登录资料，已有配置与浏览器会话保留；定时器下次启动服务时会使用新代码。

“每日日期与天气”可在自定义消息后追加北京时间日期、星期和指定城市当日天气。天气使用 [Open-Meteo 的地名搜索](https://open-meteo.com/en/docs/geocoding-api)与[天气预报接口](https://open-meteo.com/en/docs)；无法获取时只追加日期和星期。天气是预报，不保证与实况一致。

定时任务点击发送后若无法确认，程序会刷新并重新检查会话；仍未确认时最多点击发送 5 次，发送次数记录在 `.spark-state/send-ledger.json`。刷新后如果确认了前一次消息，就不会继续点击。网络延迟或页面漏显时仍可能重复发送，请接受这个风险后再启用定时器；达到 5 次仍未确认会在汇总邮件中标为“结果未确认”，需要人工核对。手动 `send` 仍只尝试一次。

定时邮件仅由 `run` 发送，手动 `send` 不发汇总。若 SMTP 连接或认证在发信前失败，下次定时器触发时会重试；若邮件已进入提交阶段但结果不明，程序会把该汇总及其结果摘要记到 `.spark-state/report-ledger.json`，同一份摘要不会自动重复发送。迟到窗口内结果状态发生变化时（例如失败任务重试后成功），会再发送一封更新汇总。汇总邮件显示“结果未确认”时，仍需在抖音聊天记录中人工核对。配置更新后，需将新的项目代码和 `config.json` 上传到服务器，才能在云端生效。

明确在发送按钮点击前失败的任务会在 10 分钟迟到窗口内随下一次定时器运行重试；点击后状态不明确的任务则通过刷新聊天核对，并最多点击 5 次。Linux 上 `.spark-state` 和状态文件使用当前用户私有权限；发送互斥锁由内核管理，进程崩溃后自动释放，不需人工清理旧锁文件。

## 5. 启用 systemd 定时器

项目路径必须是 `~/douyin-spark`；如果放在别处，先修改 `deploy/systemd/douyin-spark.service` 的 `WorkingDirectory` 和 `ExecStart`。在确认服务器上登录及无界面发信都正常之后，再用**同一个普通用户**运行：

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/douyin-spark.service deploy/systemd/douyin-spark.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now douyin-spark.timer
sudo loginctl enable-linger "$USER"
systemctl --user list-timers douyin-spark.timer
```

定时器每 5 分钟启动一次 `run --max-lateness-minutes 10`；程序只处理已到时间、不超过设定时间 10 分钟、当天尚未完成或仍在最多 5 次尝试范围内的任务。这样在下午首次启用 09:00 的任务时，不会当天补发；若服务器在设定时间离线超过 10 分钟，当天也会跳过。`enable-linger` 让用户服务在退出 SSH 后继续运行，并在开机时启动。查看状态：

```bash
systemctl --user status douyin-spark.timer
journalctl --user -u douyin-spark.service -n 50 --no-pager
cat .spark-state/last-run.json
```

`needs_review` 或 `uncertain` 表示结果不明，需要先人工核对。停止定时器：

```bash
systemctl --user disable --now douyin-spark.timer
```

云服务器安全组只需按你的管理需要允许 SSH；给 SSH 设置可信来源 IP。网页版页面结构或登录策略变动后，程序可能需要调整。服务器重启不会主动清理登录目录，但是否保持会话由抖音决定。
