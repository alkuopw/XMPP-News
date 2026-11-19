# XMPP News Bot

一个基于 Python 和 Slixmpp 的 XMPP 新闻推送机器人。

支持从 Telegram 公开频道和 RSS 订阅源获取新闻，并自动推送到指定的 XMPP 群聊（MUC）。适合搭建个人新闻聚合、技术资讯和社区信息推送服务。

## 功能特性

- **Telegram 频道订阅**：抓取 Telegram 公开频道的消息。
- **RSS 订阅**：支持多个 RSS/Atom 订阅源。
- **XMPP 群聊推送**：将获取的内容发送到指定 MUC 群聊。
- **定时更新**：每天按照预设时间自动检查订阅源。
- **手动更新**：支持在群聊中使用 `/up` 触发更新。
- **消息去重**：记录 Telegram 消息 ID 和 RSS 文章记录，避免重复推送。
- **断线自动重连**：网络或 XMPP 连接异常时自动尝试重新连接。
- **状态持久化**：通过 `bot_state.json` 保存订阅进度。
- **限制补发数量**：避免机器人长时间离线后一次性推送大量历史消息。

## 项目结构

```text
xmpp-news-bot/
├── botka.py           # 机器人主程序
├── requirements.txt   # Python 依赖
├── .gitignore         # Git 忽略规则
└── bot_state.json     # 运行时状态文件（自动初始化，不应公开提交）
```

实际仓库文件可能会随版本更新而变化。

## 运行环境

推荐环境：

| 项目 | 要求 |
|---|---|
| 操作系统 | Debian 12 / Debian 13 |
| Python | Python 3.10 或更高版本，建议使用系统支持的版本 |
| 内存 | 建议至少 512 MB 可用内存 |
| 磁盘 | 建议预留 500 MB 以上 |
| 网络 | 能够访问 Telegram、RSS 源和 XMPP 服务器 |
| XMPP | 一个可正常登录的 XMPP 账号及目标 MUC |

本项目不需要 Docker，也不需要开放 VPS 入站端口。

---

# VPS 部署教程

以下以 Debian 12/13 为例。

假设：

- Linux 用户：`botuser`
- 安装目录：`/opt/xmpp-news-bot`
- Python 虚拟环境：`/opt/xmpp-news-bot/venv`
- 主程序：`botka.py`
- systemd 服务：`xmpp-news-bot`

请根据自己的服务器情况调整。

## 第一步：更新系统并安装基础工具

使用具有 sudo 权限的账户登录 VPS。

```bash
sudo apt update
sudo apt upgrade -y
```

安装 Git、Python 和虚拟环境支持：

```bash
sudo apt install -y \
    git \
    python3 \
    python3-pip \
    python3-venv \
    ca-certificates
```

检查版本：

```bash
python3 --version
git --version
```

## 第二步：创建专用运行用户

不建议长期使用 root 运行机器人。

创建一个没有交互式登录权限的系统用户：

```bash
sudo useradd --system \
    --create-home \
    --home-dir /opt/xmpp-news-bot \
    --shell /usr/sbin/nologin \
    botuser
```

创建目录并设置权限：

```bash
sudo mkdir -p /opt/xmpp-news-bot
sudo chown botuser:botuser /opt/xmpp-news-bot
sudo chmod 700 /opt/xmpp-news-bot
```

如果用户已经存在，可以跳过创建用户这一步。

## 第三步：从 GitHub 克隆项目

将下面的仓库地址替换成实际 GitHub 仓库地址：

```bash
sudo -u botuser git clone \
    https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git \
    /opt/xmpp-news-bot
```

进入项目目录：

```bash
cd /opt/xmpp-news-bot
```

检查文件：

```bash
ls -la
```

确认存在 `botka.py` 和 `requirements.txt`。

## 第四步：创建 Python 虚拟环境

进入项目目录：

```bash
cd /opt/xmpp-news-bot
```

创建虚拟环境：

```bash
sudo -u botuser python3 -m venv venv
```

虚拟环境可以隔离项目依赖，避免直接修改 Debian 系统 Python 环境。

## 第五步：安装 Python 依赖

如果项目包含 `requirements.txt`：

```bash
sudo -u botuser /opt/xmpp-news-bot/venv/bin/pip install --upgrade pip
```

安装依赖：

```bash
sudo -u botuser /opt/xmpp-news-bot/venv/bin/pip install \
    -r /opt/xmpp-news-bot/requirements.txt
```

项目使用的主要 Python 库包括：

- `slixmpp`
- `requests`
- `beautifulsoup4`
- `lxml`
- `feedparser`

如果仓库没有提供 `requirements.txt`，可以暂时使用：

```bash
sudo -u botuser /opt/xmpp-news-bot/venv/bin/pip install \
    slixmpp requests beautifulsoup4 lxml feedparser
```

## 第六步：配置机器人

打开主程序：

```bash
sudo nano /opt/xmpp-news-bot/botka.py
```

找到代码中的配置区域，按照注释填写自己的信息。

需要配置的内容通常包括：

| 配置项目 | 说明 |
|---|---|
| XMPP 服务器 | XMPP 服务器地址 |
| XMPP 账号 | 机器人登录账号 |
| XMPP 密码 | 机器人登录密码 |
| MUC 地址 | 目标群聊地址 |
| 群聊昵称 | 机器人在群聊中的昵称 |
| Telegram 频道 | 需要订阅的公开频道用户名 |
| RSS_FEEDS | RSS 名称与订阅地址 |
| UPDATE_HOURS | 每日自动更新时间 |
| MESSAGE_DELAY | 消息推送间隔 |
| MAX_CATCHUP_MSGS | Telegram 历史补发数量限制 |

注意：

1. Telegram 公开频道使用频道用户名，不需要填写 `https://t.me/` 前缀。
2. RSS 地址应当是实际可访问的 RSS 或 Atom 订阅地址。
3. 自动更新时间使用 VPS 的系统时区。
4. XMPP 账号、密码和其他私密配置不能提交到公开 GitHub 仓库。

**安全提醒：** 如果当前版本将账号密码直接写在 `botka.py` 中，请确保公开版本中的凭据已经替换为占位符。更推荐后续将私密配置迁移到独立配置文件或环境变量中，并将其加入 `.gitignore`。

## 第七步：检查代码语法

运行：

```bash
sudo -u botuser \
    /opt/xmpp-news-bot/venv/bin/python \
    -m py_compile /opt/xmpp-news-bot/botka.py
```

没有输出通常表示语法检查通过。

## 第八步：首次前台启动测试

在正式配置 systemd 之前，先测试机器人能否正常运行：

```bash
sudo -u botuser bash -c \
    'cd /opt/xmpp-news-bot && exec ./venv/bin/python -u botka.py'
```

检查：

- 是否成功连接 XMPP 服务器。
- 是否成功进入目标 MUC 群聊。
- Telegram 频道是否能够正常抓取。
- RSS 订阅源是否能够正常解析。
- 是否能够接收 `/up` 指令。
- 是否能够正常推送消息。

首次启动时，机器人可能会执行静默初始化，记录已有消息，避免将全部历史内容当作新消息推送。

测试完成后按 `Ctrl+C` 退出。

## 第九步：创建 systemd 服务

创建服务文件：

```bash
sudo nano /etc/systemd/system/xmpp-news-bot.service
```

写入：

```ini
[Unit]
Description=XMPP News Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple

User=botuser
Group=botuser

WorkingDirectory=/opt/xmpp-news-bot

ExecStart=/opt/xmpp-news-bot/venv/bin/python -u /opt/xmpp-news-bot/botka.py

Restart=always
RestartSec=10

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

ReadWritePaths=/opt/xmpp-news-bot

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

保存后执行：

```bash
sudo systemctl daemon-reload
```

设置开机自启动并立即启动：

```bash
sudo systemctl enable --now xmpp-news-bot
```

查看服务状态：

```bash
sudo systemctl status xmpp-news-bot --no-pager
```

如果状态显示 `active (running)`，说明服务正在运行。

## 第十步：限制日志占用

机器人日志默认交由 systemd-journald 管理，不需要另外创建日志文件。

创建 journald 配置目录：

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
```

创建配置文件：

```bash
sudo nano /etc/systemd/journald.conf.d/60-xmpp-news-bot.conf
```

写入：

```ini
[Journal]
SystemMaxUse=200M
SystemKeepFree=1G
SystemMaxFileSize=20M
MaxRetentionSec=14day
```

应用：

```bash
sudo systemctl restart systemd-journald
```

注意：这些参数限制的是整个系统的 journal 日志，而不只是本机器人。请根据 VPS 的磁盘容量和其他服务的日志需求调整。

检查日志占用：

```bash
sudo journalctl --disk-usage
```

## 日常管理

### 查看服务状态

```bash
sudo systemctl status xmpp-news-bot
```

### 查看实时日志

```bash
sudo journalctl -u xmpp-news-bot -f
```

### 查看最近 100 条日志

```bash
sudo journalctl -u xmpp-news-bot -n 100 --no-pager
```

### 启动机器人

```bash
sudo systemctl start xmpp-news-bot
```

### 停止机器人

```bash
sudo systemctl stop xmpp-news-bot
```

### 重启机器人

```bash
sudo systemctl restart xmpp-news-bot
```

### 禁止开机启动

```bash
sudo systemctl disable xmpp-news-bot
```

### 重新启用开机启动

```bash
sudo systemctl enable xmpp-news-bot
```

---

# 数据与备份

机器人使用 `bot_state.json` 保存运行状态。

主要包括：

- `last_ids`：已处理的 Telegram 消息 ID。
- `rss_seen`：已经处理的 RSS 文章记录。

该文件用于消息去重和断线恢复。

**不要随意删除 `bot_state.json`。** 删除后机器人可能重新初始化订阅源，具体行为取决于当前版本的初始化逻辑。

备份状态文件：

```bash
sudo cp \
    /opt/xmpp-news-bot/bot_state.json \
    /opt/xmpp-news-bot/bot_state.json.bak
```

建议定期备份：

- `bot_state.json`
- 私密配置
- 自己修改过的代码

备份文件不要上传到公开仓库。

---

# 更新程序

更新前先备份状态文件：

```bash
sudo cp \
    /opt/xmpp-news-bot/bot_state.json \
    /opt/xmpp-news-bot/bot_state.json.bak
```

停止机器人：

```bash
sudo systemctl stop xmpp-news-bot
```

进入项目目录：

```bash
cd /opt/xmpp-news-bot
```

拉取最新代码：

```bash
sudo -u botuser git pull
```

更新依赖：

```bash
sudo -u botuser /opt/xmpp-news-bot/venv/bin/pip install \
    -r /opt/xmpp-news-bot/requirements.txt
```

检查代码：

```bash
sudo -u botuser \
    /opt/xmpp-news-bot/venv/bin/python \
    -m py_compile /opt/xmpp-news-bot/botka.py
```

重新启动：

```bash
sudo systemctl start xmpp-news-bot
```

检查运行状态：

```bash
sudo systemctl status xmpp-news-bot --no-pager
```

注意：如果你在服务器上直接修改了被 Git 跟踪的文件，更新前应先妥善保存自己的修改，避免 `git pull` 产生冲突或覆盖配置。

---

# 常见问题

### 1. 服务启动后立即退出

检查：

```bash
sudo journalctl -u xmpp-news-bot -n 100 --no-pager
```

重点检查：

- Python 语法错误。
- 依赖缺失。
- 配置错误。
- 文件权限。
- XMPP 登录失败。

### 2. RSS 返回 HTTP 403

这通常表示目标网站拒绝了 HTTP 请求。

可以尝试：

- 检查 RSS 地址是否正确。
- 使用 curl 单独测试订阅地址。
- 检查目标站点是否限制服务器 IP 或 User-Agent。
- 更换可靠的 RSS 来源。

HTTP 200 也不代表 RSS 内容一定能被正常解析。

### 3. 消息重复推送

检查 `bot_state.json` 是否存在，以及运行用户是否具有读写权限：

```bash
sudo ls -l /opt/xmpp-news-bot/bot_state.json
```

### 4. 无法加入 XMPP 群聊

检查：

- XMPP 账号和密码。
- MUC 地址是否正确。
- 群聊是否允许该账号加入。
- 群聊昵称是否已被占用。
- 网络连接是否正常。

---

# 安全建议

- 不要使用 root 直接运行机器人。
- 不要公开 XMPP 密码或其他认证凭据。
- 不要上传 `bot_state.json` 和私人配置。
- 不要将私钥、Token、密码提交到 GitHub。
- 使用独立 Linux 用户运行服务。
- 定期更新系统和 Python 依赖。
- 定期备份状态文件。
- 不需要为了运行本机器人而开放额外的入站端口。

## 免责声明

本项目用于个人新闻聚合与 XMPP 信息推送。

订阅内容的版权、使用条件及访问限制由各原始来源决定。使用者应遵守相关网站的服务条款和适用法律。

欢迎提交 Issue、反馈问题和改进建议。
