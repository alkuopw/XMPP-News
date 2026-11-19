#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XMPP 新闻机器人 —— Telegram 频道聚合
带 /up 限速 + 重启防历史刷屏

连接机制采用经过验证的反刷屏机器人架构：
  - auto_reconnect=False + 外层 while True 新建实例，彻底避免旧状态污染
  - await bot.disconnected 干净等待本次连接结束
  - del bot + gc.collect() 确保旧实例完全释放后再重连
  - 指数退避延迟，连续失败不会狂刷重连
  - asyncio.new_event_loop() + 自定义异常处理器，屏蔽 socket 噪音
  - 跨实例共享 shared_state / shared_last_ids / up_limiter，重连不丢进度
"""
import asyncio
import time
import logging
import re
import json
import os
import gc
import signal
import random
from datetime import datetime
from urllib.parse import urlparse
from typing import Dict, List, Optional
from dataclasses import dataclass, field
import requests
from bs4 import BeautifulSoup
import slixmpp
import feedparser
import hashlib
from html import unescape
# ══════════════════════════════════════════════════════════════
# ★  基础配置（请务必修改）
# ══════════════════════════════════════════════════════════════
BOT_JID      = "bot@xmpp.example.com"                   # ← 修改成你的机器人JID
BOT_PASSWORD = "your_password"                          # ← 修改成密码
ROOM_JID     = "channel@conference.xmpp.example.com"   # ← 修改成群聊JID
ROOM_NICK    = "HinaBot"                                # 机器人在群里的昵称

# ══════════════════════════════════════════════════════════════
# ★  Telegram 频道列表
# ══════════════════════════════════════════════════════════════
TELEGRAM_CHANNELS: List[str] = [
    "zhongguogongrenjiefangbao", 
    "lilaoshibushinilaoshi", "YeHuoVideo", "gong_ge_news"
]
CHANNEL_NAMES: Dict[str, str] = {
    
    "zhongguogongrenjiefangbao": "中国工人解放报",
    "lilaoshibushinilaoshi":     "李老师不是你老师",
    "YeHuoVideo":                "野火",
    "gong_ge_news":              "工人革命报",
}
# ══════════════════════════════════════════════════════════════
# ★ RSS / Atom 订阅列表
# ══════════════════════════════════════════════════════════════

RSS_FEEDS = {
    "竹新社": "https://rsshub.rssforever.com/telegram/channel/tnews365",
    
}

MAX_RSS_CATCHUP_MSGS = 5
RSS_MAX_ITEMS = 100
RSS_SUMMARY_LENGTH = 1200
# ══════════════════════════════════════════════════════════════
# ★  运行参数
# ══════════════════════════════════════════════════════════════
MESSAGE_DELAY        = 10
UPDATE_HOURS         = [2, 10]
MAX_CATCHUP_MSGS     = 5

UP_MAX_COUNT   = 3
UP_TIME_WINDOW = 60
UP_COOLDOWN    = 300

STATE_FILE       = "bot_state.json"
FETCH_TIMEOUT    = 15
JOIN_TIMEOUT     = 20      # join_muc 单次超时（秒）
JOIN_MAX_RETRIES = 5       # join_muc 最大重试次数

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ══════════════════════════════════════════════════════════════
# 数据类 & 限速器
# ══════════════════════════════════════════════════════════════
@dataclass
class TGMessage:
    channel: str
    msg_id: int
    text: str
    urls: List[str]
@dataclass
class RSSMessage:
    source: str
    feed_url: str
    item_id: str
    title: str
    summary: str
    link: str
    published: str
@dataclass
class RateLimiter:
    max_count: int
    time_window: float
    cooldown: float
    _times: Dict[str, List[float]] = field(default_factory=dict)
    _muted: Dict[str, float]       = field(default_factory=dict)

    def check(self, nick: str):
        now = time.time()
        mute_until = self._muted.get(nick, 0)
        if now < mute_until:
            return False, f"你的该指令权限已暂停，请等待 {int(mute_until - now)} 秒后再试。"
        if mute_until and now >= mute_until:
            self._muted.pop(nick, None)
        times = self._times.setdefault(nick, [])
        times[:] = [t for t in times if now - t < self.time_window]
        times.append(now)
        if len(times) > self.max_count:
            self._muted[nick] = now + self.cooldown
            return False, f"检测到频繁触发，该指令已暂停 {int(self.cooldown)} 秒。"
        return True, ""

# ══════════════════════════════════════════════════════════════
# 状态持久化
# ══════════════════════════════════════════════════════════════
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)

                # 兼容旧版本状态文件
                state.setdefault("last_ids", {})
                state.setdefault("rss_seen", {})

                return state

        except Exception as e:
            logging.warning(f"加载状态文件失败: {e}")

    return {
        "last_ids": {},
        "rss_seen": {}
    }

def save_state(state: dict) -> None:
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        logging.error(f"保存状态文件失败: {e}")

# ══════════════════════════════════════════════════════════════
# 自定义异常处理器（屏蔽 socket.send 噪音）
# ══════════════════════════════════════════════════════════════
def custom_exception_handler(loop, context):
    message = context.get("message", "")
    if "socket.send() raised exception" in message or "socket.send" in message:
        logging.debug(f"忽略 socket.send 异常: {context.get('exception')}")
        return
    loop.default_exception_handler(context)

# ══════════════════════════════════════════════════════════════
# XMPP 机器人主体
# ══════════════════════════════════════════════════════════════
class NewsBot(slixmpp.ClientXMPP):
    def __init__(self, shared_state: dict, shared_last_ids: dict,
                 up_limiter: RateLimiter):
        super().__init__(BOT_JID, BOT_PASSWORD)

        # 跨实例共享：重连时新实例直接继承，不丢失推送进度和限速记录
        self._state      = shared_state
        self._last_ids   = shared_last_ids
        self._up_limiter = up_limiter

        self._initialized = False
        self._is_updating = False
        self._is_joined   = False

        # ★ 关键：禁用内置自动重连，由外层 run_bot() 统一管理
        self.auto_reconnect = False

        self.add_event_handler("session_start",     self.on_start)
        self.add_event_handler("groupchat_message", self.on_group_message)
        self.add_event_handler("disconnected",      self.on_disconnect)
        self.add_event_handler("failed_auth",       self.on_failed_auth)
        self.add_event_handler("connection_failed", self.on_connection_failed)

        self.register_plugin("xep_0199")
        self["xep_0199"].enable_keepalive(interval=120, timeout=60)

    # ── 启动 ──────────────────────────────────────────────────
    async def on_start(self, event):
        if self._is_joined:
            logging.warning("on_start 重复触发，跳过")
            return

        self.send_presence()
        await asyncio.sleep(1)

        joined = await self._join_room_with_retry()
        if not joined:
            logging.error("无法加入群聊，等待外层重连…")
            self.disconnect()
            return

        logging.info("机器人已上线并加入群聊，开始静默初始化频道状态…")
        asyncio.ensure_future(self._init_all_channels())
        asyncio.ensure_future(self._run_scheduler())

    # ── join_muc 重试 ─────────────────────────────────────────
    async def _join_room_with_retry(self) -> bool:
        # join_muc 是同步调用，不能 await；用 Event 等待服务器回传的 got_online 确认
        join_event = asyncio.Event()

        def on_muc_online(presence):
            if presence["muc"]["nick"] == ROOM_NICK:
                join_event.set()

        handler_name = f"muc::{ROOM_JID}::got_online"
        self.add_event_handler(handler_name, on_muc_online)

        try:
            for attempt in range(1, JOIN_MAX_RETRIES + 1):
                try:
                    logging.info(f"尝试加入群聊（第 {attempt}/{JOIN_MAX_RETRIES} 次）…")
                    join_event.clear()
                    self.plugin["xep_0045"].join_muc(ROOM_JID, ROOM_NICK)
                    await asyncio.wait_for(join_event.wait(), timeout=JOIN_TIMEOUT)
                    self._is_joined = True
                    logging.info("✅ 成功加入群聊")
                    return True
                except asyncio.TimeoutError:
                    logging.warning(f"加入群聊超时（第 {attempt} 次）")
                except Exception as e:
                    logging.error(f"加入群聊异常（第 {attempt} 次）: {type(e).__name__}: {e}")
                await asyncio.sleep(3 * attempt)
        finally:
            self.del_event_handler(handler_name, on_muc_online)

        logging.error("多次尝试后仍无法加入群聊")
        return False

    # ── 静默初始化 ────────────────────────────────────────────
    async def _init_all_channels(self):
        """
        启动时静默初始化 Telegram 和 RSS 状态。
        已有记录的来源保持原有进度。
        新来源只记录当前文章，不推送历史内容。
        """
        loop = asyncio.get_event_loop()
        changed = False

        # ══════════════════════════════════════════════════════
        # Telegram 静默初始化（保留原有逻辑）
        # ══════════════════════════════════════════════════════

        for channel in TELEGRAM_CHANNELS:

            if channel in self._last_ids:
                logging.info(
                    f"[{channel}] 已有记录 last_id="
                    f"{self._last_ids[channel]}，跳过初始化"
                )
                continue

            try:
                msgs = await loop.run_in_executor(
                    None, scrape_channel, channel, 0
                )

                if msgs:
                    max_id = max(m.msg_id for m in msgs)
                    self._last_ids[channel] = max_id

                    logging.info(
                        f"[{channel}] 静默初始化 last_id={max_id}"
                    )
                else:
                    self._last_ids[channel] = 0

                    logging.warning(
                        f"[{channel}] 未获取到消息，last_id 设为 0"
                    )

                changed = True

            except Exception as e:
                logging.error(
                    f"[{channel}] 静默初始化失败: {e}"
                )

        # ══════════════════════════════════════════════════════
        # RSS 静默初始化（新增）
        # ══════════════════════════════════════════════════════

        rss_seen = self._state.setdefault("rss_seen", {})

        for feed_name, feed_url in RSS_FEEDS.items():

            if feed_name in rss_seen:
                logging.info(
                    f"[RSS:{feed_name}] 已有记录，跳过初始化"
                )
                continue

            try:
                msgs = await loop.run_in_executor(
                    None,
                    scrape_rss,
                    feed_name,
                    feed_url
                )

                rss_seen[feed_name] = [
                    m.item_id for m in msgs
                ][:200]

                logging.info(
                    f"[RSS:{feed_name}] 静默初始化完成，"
                    f"记录 {len(rss_seen[feed_name])} 篇文章"
                )

                changed = True

            except Exception as e:
                logging.error(
                    f"[RSS:{feed_name}] 静默初始化失败: {e}"
                )

        # ══════════════════════════════════════════════════════
        # 保存状态
        # ══════════════════════════════════════════════════════

        if changed:
            self._state["last_ids"] = self._last_ids
            self._state["rss_seen"] = rss_seen
            save_state(self._state)

        self._initialized = True

        logging.info(
            "所有 Telegram 和 RSS 来源初始化完成，"
            "/up 和定时推送已就绪"
        )
    # ── 消息处理 ──────────────────────────────────────────────
    async def on_group_message(self, msg):
        if msg["from"].bare != ROOM_JID:
            return
        nick = msg["mucnick"]
        if not nick or nick == ROOM_NICK:
            return
        body = (msg["body"] or "").strip()
        if not body:
            return

        if body == "/up":
            if not self._initialized:
                self._send_room("正在初始化频道状态，请稍候…")
                return
            allowed, reason = self._up_limiter.check(nick)
            if not allowed:
                self._send_room(f"@{nick} {reason}")
                return
            if self._is_updating:
                self._send_room("正在更新中，请稍候…")
                return
            asyncio.ensure_future(self.do_update(manual=True))

    # ── 定时器 ────────────────────────────────────────────────
    async def _run_scheduler(self):
        last_triggered_hour = -1
        while True:
            if self._initialized:
                now = datetime.now()
                if (now.hour in UPDATE_HOURS
                        and now.minute == 0
                        and last_triggered_hour != now.hour):
                    last_triggered_hour = now.hour
                    logging.info(f"⏰ 定时更新触发: {now.strftime('%H:%M')}")
                    asyncio.ensure_future(self.do_update(manual=False))
                elif now.minute != 0:
                    last_triggered_hour = -1
            await asyncio.sleep(30)

    # ── 更新主逻辑 ────────────────────────────────────────────
    async def do_update(self, manual: bool = False):
        if self._is_updating:
            return
        self._is_updating = True
        total_sent = 0
        loop = asyncio.get_event_loop()

        try:
            for channel in TELEGRAM_CHANNELS:
                after_id = self._last_ids.get(channel, 0)
                msgs = await loop.run_in_executor(
                    None, scrape_channel, channel, after_id
                )

                if not msgs:
                    logging.info(f"[{channel}] 无新消息（after_id={after_id}）")
                    continue

                msgs = [m for m in msgs if m.msg_id > after_id]
                if not msgs:
                    logging.info(f"[{channel}] 过滤后无新消息")
                    continue

                if len(msgs) > MAX_CATCHUP_MSGS:
                    msgs = msgs[-MAX_CATCHUP_MSGS:]

                logging.info(f"[{channel}] 推送 {len(msgs)} 条新消息")
                channel_max_id = after_id

                for m in msgs:
                    self._send_room(format_tg_message(m))
                    total_sent += 1
                    channel_max_id = max(channel_max_id, m.msg_id)
                    await asyncio.sleep(MESSAGE_DELAY)

                if channel_max_id > self._last_ids.get(channel, 0):
                    self._last_ids[channel] = channel_max_id
                    self._state["last_ids"] = self._last_ids
                    save_state(self._state)
                    logging.info(f"[{channel}] last_id 更新为 {channel_max_id}")
            # ══════════════════════════════════════════════════════
            # RSS 更新
            # ══════════════════════════════════════════════════════

            rss_seen = self._state.setdefault("rss_seen", {})

            for feed_name, feed_url in RSS_FEEDS.items():

                try:
                    msgs = await loop.run_in_executor(
                        None,
                        scrape_rss,
                        feed_name,
                        feed_url
                    )

                    if not msgs:
                        logging.info(
                            f"[RSS:{feed_name}] 没有获取到文章"
                        )
                        continue

                    # 获取该订阅源已经处理过的文章 ID
                    seen_list = rss_seen.get(feed_name, [])
                    seen = set(seen_list)

                    # 筛选新文章
                    new_msgs = [
                        m for m in msgs
                        if m.item_id not in seen
                    ]

                    if not new_msgs:
                        logging.info(
                            f"[RSS:{feed_name}] 没有新文章"
                        )
                        continue

                    # 按从旧到新的顺序推送
                    new_msgs.reverse()

                    # 限制单次补发数量
                    if len(new_msgs) > MAX_RSS_CATCHUP_MSGS:
                        new_msgs = new_msgs[-MAX_RSS_CATCHUP_MSGS:]

                    logging.info(
                        f"[RSS:{feed_name}] "
                        f"发现 {len(new_msgs)} 篇新文章"
                    )

                    for m in new_msgs:

                        self._send_room(format_rss_message(m))

                        total_sent += 1

                        # 记录已推送文章
                        if m.item_id not in seen:
                            seen_list.append(m.item_id)
                            seen.add(m.item_id)

                        # 最多保留最近 200 条记录
                        seen_list = seen_list[-200:]

                        rss_seen[feed_name] = seen_list

                        self._state["rss_seen"] = rss_seen
                        save_state(self._state)

                        await asyncio.sleep(MESSAGE_DELAY)

                except Exception as e:
                    logging.error(
                        f"[RSS:{feed_name}] 更新异常: {e}",
                        exc_info=True
                    )

            if total_sent == 0 and manual:
                no_update_replies = [
                    "暂时没有新消息",
                    "没有新消息了呢",
                    "还没看到频道更新",
                    "各个频道都还没动静",
                    "暂时没有更多内容",
                    "还没有新消息，稍后再试试",
                    "现在没有新消息哦",
                ]
                self._send_room(random.choice(no_update_replies))
            elif total_sent > 0:
                logging.info(f"本次更新完成，共推送 {total_sent} 条")

        except Exception as e:
            logging.error(f"更新异常: {e}", exc_info=True)
        finally:
            self._is_updating = False

    # ── 工具方法 ──────────────────────────────────────────────
    def _send_room(self, text: str):
        self.send_message(mto=ROOM_JID, mbody=text, mtype="groupchat")

    def on_disconnect(self, event):
        logging.warning("⚠ 连接断开，等待外层重连…")
        self._is_joined   = False
        self._initialized = False
        self._is_updating = False

    def on_connection_failed(self, event):
        logging.error(f"❌ 连接失败: {event}")

    def on_failed_auth(self, event):
        logging.error("❌ 账号或密码错误，停止重连")
        os._exit(1)

    def safe_disconnect(self):
        try:
            if self.is_connected():
                self.disconnect()
        except Exception:
            pass

# ══════════════════════════════════════════════════════════════
# Telegram 爬取函数
# ══════════════════════════════════════════════════════════════
def _append_unique(lst: List[str], item: str) -> None:
    if item and item not in lst:
        lst.append(item)
def scrape_rss(feed_name: str, feed_url: str):
    try:
        resp = requests.get(
            feed_url,
            headers=HTTP_HEADERS,
            timeout=FETCH_TIMEOUT
        )
        resp.raise_for_status()

        feed = feedparser.parse(resp.content)

        if feed.bozo and not feed.entries:
            logging.error(
                f"[RSS:{feed_name}] 解析失败: {feed.bozo_exception}"
            )
            return []

        messages = []

        for entry in feed.entries[:RSS_MAX_ITEMS]:

            title = unescape(
                BeautifulSoup(
                    entry.get("title", ""),
                    "html.parser"
                ).get_text(" ", strip=True)
            )

            raw_summary = (
                entry.get("summary")
                or entry.get("description")
                or ""
            )

            summary = BeautifulSoup(
                raw_summary,
                "html.parser"
            ).get_text(" ", strip=True)

            summary = unescape(summary)
            summary = summary[:RSS_SUMMARY_LENGTH]

            link = entry.get("link", "")

            item_id = (
                entry.get("id")
                or entry.get("guid")
                or link
            )

            if not item_id:
                raw = title + summary
                item_id = hashlib.sha256(
                    raw.encode("utf-8")
                ).hexdigest()

            published = (
                entry.get("published")
                or entry.get("updated")
                or ""
            )

            messages.append(
                RSSMessage(
                    source=feed_name,
                    feed_url=feed_url,
                    item_id=str(item_id),
                    title=title,
                    summary=summary,
                    link=link,
                    published=published
                )
            )

        return messages

    except Exception as e:
        logging.error(
            f"[RSS:{feed_name}] 获取失败: {e}"
        )
        return []
def format_rss_message(msg: RSSMessage) -> str:
    parts = [
        f"📰 【RSS · {msg.source}】",
        "",
        f"📌 {msg.title}",
    ]

    if msg.published:
        parts.extend([
            "",
            f"🕒 {msg.published}"
        ])

    if msg.summary:
        parts.extend([
            "",
            msg.summary
        ])

    if msg.link:
        parts.extend([
            "",
            f"🔗 {msg.link}"
        ])

    return "\n".join(parts)
def scrape_channel(channel: str, after_id: int = 0) -> List[TGMessage]:
    url = f"https://t.me/s/{channel}"
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        logging.error(f"[{channel}] 请求失败: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    messages: List[TGMessage] = []

    for msg_div in soup.select(".tgme_widget_message[data-post]"):
        try:
            msg_id = int(msg_div.get("data-post", "").rsplit("/", 1)[-1])
        except Exception:
            continue
        if msg_id <= after_id:
            continue

        text_div = msg_div.select_one(".tgme_widget_message_text")
        raw_text = text_div.get_text(separator="").strip() if text_div else ""

        urls: List[str] = []

        if text_div:
            for a in text_div.select("a[href]"):
                href = a.get("href", "")
                if href.startswith(("http://", "https://")):
                    _append_unique(urls, href)

        for u in re.findall(r'https?://[^\s<>"\'）》」，。\n]+', raw_text):
            u = u.rstrip(".,;:!?)")
            if u:
                _append_unique(urls, u)

        messages.append(TGMessage(channel, msg_id, raw_text, urls))

    messages.sort(key=lambda m: m.msg_id)
    return messages

def format_tg_message(msg: TGMessage) -> str:
    ch_name = CHANNEL_NAMES.get(msg.channel, msg.channel)
    parts = [f"📢 【{ch_name}】 https://t.me/{msg.channel}/{msg.msg_id}"]
    if msg.text:
        parts.extend(["", msg.text])
    ext_urls = [u for u in msg.urls if "t.me" not in urlparse(u).netloc]
    if ext_urls:
        parts.extend(["", "🔗 " + "\n   ".join(ext_urls)])
    return "\n".join(parts)

# ══════════════════════════════════════════════════════════════
# 主循环（采用反刷屏机器人的连接架构）
# ══════════════════════════════════════════════════════════════
async def run_bot():
    # 跨实例共享的状态，重连时新实例直接继承，不丢失进度
    shared_state = load_state()

    # 确保 Telegram 和 RSS 状态均存在
    shared_state.setdefault("last_ids", {})
    shared_state.setdefault("rss_seen", {})

    # Telegram 消息 ID
    shared_last_ids = shared_state["last_ids"]

    # /up 指令限速器
    up_limiter = RateLimiter(
        UP_MAX_COUNT,
        UP_TIME_WINDOW,
        UP_COOLDOWN
    )

    reconnect_delay      = 5.0
    max_delay            = 120.0
    consecutive_failures = 0
# ══════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(custom_exception_handler)

    def signal_handler(sig, frame):
        logging.info("收到退出信号，正在关闭…")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    signal.signal(signal.SIGINT,  signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        loop.run_until_complete(run_bot())
    except KeyboardInterrupt:
        logging.info("手动停止，再见。")
    finally:
        loop.close()
