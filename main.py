import asyncio
import json
import time
import re
from pathlib import Path
from textwrap import dedent

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register, StarTools


@register(
    "astrbot_plugin_mihuasher_review",
    "iv小白龙",
    "获取米画师画师评价并推送",
    "1.0.2",
    "https://github.com/ggxiaobailong-blip/astrbot_plugin_mihuasher_review"
)
class MihuasherReviewPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        config = config or {}
        self.cookie = config.get("cookie", "")
        self.default_artist_id = config.get("default_artist_id", "")
        self.max_display = int(config.get("max_reviews_display", 10))
        self.enable_auto_push = config.get("enable_auto_push", False)
        self.push_cron = config.get("push_cron", "*/30 * * * *")
        self.global_push_target = config.get("push_target", "")

        # 从 WebUI 配置解析画师信息列表
        self.artist_info_map = {}
        artist_info_text = config.get("artist_info_list", "")
        if artist_info_text:
            for line in artist_info_text.strip().split('\n'):
                line = line.strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 2:
                    artist_id, name = parts[0], parts[1]
                    avatar = parts[2] if len(parts) >= 3 else ''
                    self.artist_info_map[artist_id] = {'name': name, 'avatar': avatar}

        # 数据目录
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_mihuasher_review")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.scheduler = None
        self._file_lock = asyncio.Lock()

        logger.info(f"米画师插件已加载，Cookie 已配置: {bool(self.cookie)}")
        if self.default_artist_id:
            logger.info(f"默认画师ID: {self.default_artist_id}")

        if self.enable_auto_push and self.cookie:
            self._init_scheduler()
        elif self.enable_auto_push and not self.cookie:
            logger.warning("自动推送已启用但未配置 Cookie，无法启动调度器")

    def _init_scheduler(self):
        if self.scheduler and self.scheduler.running:
            return
        self.scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        try:
            trigger = CronTrigger.from_crontab(self.push_cron)
            self.scheduler.add_job(
                func=self._auto_check_all_subscriptions,
                trigger=trigger,
                id="mihuasher_auto_push",
                max_instances=1,
                coalesce=True
            )
            self.scheduler.start()
            logger.info(f"[米画师] 自动推送已启用，Cron: {self.push_cron}")
        except Exception as e:
            logger.error(f"[米画师] 启动调度器失败: {e}")

    def _stop_scheduler(self):
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("[米画师] 调度器已停止")

    def _get_storage_file(self, artist_id: str) -> Path:
        return self.data_dir / f"artist_{artist_id}.json"

    async def _load_saved_reviews(self, artist_id: str) -> dict:
        file_path = self._get_storage_file(artist_id)
        try:
            async with self._file_lock:
                if file_path.exists():
                    with open(file_path, 'r', encoding='utf-8') as f:
                        return json.load(f)
        except (json.JSONDecodeError, OSError, IOError) as e:
            logger.warning(f"加载评价缓存失败 ({artist_id}): {e}")
        return {"last_reviews": []}

    async def _save_reviews(self, artist_id: str, reviews: list):
        file_path = self._get_storage_file(artist_id)
        if len(reviews) > 100:
            reviews = reviews[:100]
        data = {"last_reviews": reviews}
        temp_path = file_path.with_suffix(".tmp")
        try:
            async with self._file_lock:
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                temp_path.replace(file_path)
        except Exception as e:
            logger.error(f"保存评价缓存失败 ({artist_id}): {e}")

    async def fetch_reviews(self, artist_id: str) -> dict:
        if not self.cookie:
            logger.error("未配置米画师 Cookie")
            return {"artist_info": {}, "reviews": []}

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Referer': f'https://www.mihuashi.com/profiles/{artist_id}',
            'Origin': 'https://www.mihuashi.com',
            'X-Requested-With': 'XMLHttpRequest',
            'Cookie': self.cookie,
        }

        async with aiohttp.ClientSession() as session:
            # 获取评价列表
            reviews_url = f"https://www.mihuashi.com/api/v1/users/{artist_id}/comments"
            params = {
                'page': 1,
                'perspective': 'third',
                'type': 'employer',
                'only_image': 'false'
            }
            reviews_data = []
            try:
                async with session.get(reviews_url, headers=headers, params=params, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if 'comments' in data and isinstance(data['comments'], list):
                            for item in data['comments']:
                                content = item.get('content', '')
                                raw_time = item.get('created_at', '')
                                time_str = raw_time.split('T')[0] if 'T' in raw_time else raw_time
                                commenter_name = item.get('commenter', {}).get('name', '匿名')
                                if content:
                                    reviews_data.append({
                                        'content': content.strip(),
                                        'time': time_str,
                                        'commenter_name': commenter_name
                                    })
                        logger.info(f"成功获取 {len(reviews_data)} 条评价")
                    else:
                        logger.error(f"评价接口返回 {resp.status}")
            except Exception as e:
                logger.error(f"获取评价失败: {e}")

            # 获取画师信息（优先使用配置列表）
            map_info = self.artist_info_map.get(str(artist_id), {})
            if map_info:
                artist_name = map_info.get("name", str(artist_id))
                artist_avatar = map_info.get("avatar", "")
                logger.info(f"使用配置的画师信息: {artist_id} -> {artist_name}")
            else:
                # 无配置则回退到自动解析（可能失败，但保留）
                artist_name = str(artist_id)
                artist_avatar = ""
                try:
                    profile_url = f"https://www.mihuashi.com/profiles/{artist_id}"
                    html_headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Cookie': self.cookie,
                    }
                    async with session.get(profile_url, headers=html_headers, timeout=10) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            from bs4 import BeautifulSoup
                            soup = BeautifulSoup(html, 'html.parser')
                            name_elem = soup.find('h2', class_='user-profile__name')
                            if name_elem:
                                name_text = name_elem.get_text(strip=True)
                                name_match = re.match(r'^([^\d]+)', name_text)
                                artist_name = name_match.group(1).strip() if name_match else name_text
                                logger.info(f"自动解析画师名字: {artist_name}")
                            avatar_img = soup.find('img', class_='h-full w-full object-cover')
                            if avatar_img and avatar_img.get('src'):
                                avatar_url = avatar_img['src']
                                if avatar_url.startswith('//'):
                                    avatar_url = 'https:' + avatar_url
                                artist_avatar = avatar_url
                                logger.info(f"自动解析画师头像: {artist_avatar[:80]}...")
                        else:
                            logger.warning(f"获取画师主页失败: {resp.status}")
                except Exception as e:
                    logger.warning(f"解析画师信息失败: {e}")

            return {"artist_info": {"name": artist_name, "avatar": artist_avatar}, "reviews": reviews_data}

    async def check_and_notify(self, artist_id: str) -> list:
        data = await self.fetch_reviews(artist_id)
        current_reviews = data.get('reviews', [])
        if not current_reviews:
            return []
        saved_data = await self._load_saved_reviews(artist_id)
        saved_reviews = saved_data.get("last_reviews", [])
        new_reviews = [r for r in current_reviews if r not in saved_reviews]
        if new_reviews:
            await self._save_reviews(artist_id, current_reviews)
        return new_reviews

    async def _send_as_image(self, target_origin: str, artist_id: str, rev: dict):
        content = rev['content']
        time_str = rev['time']
        commenter_name = rev.get('commenter_name', '匿名')
        markdown_text = dedent(f"""\
            # 🎨 米画师新评价
            **画师 ID**: `{artist_id}`

            **甲方**: {commenter_name}

            **📝 评价内容**:
            {content}

            **📅 时间**: {time_str}
        """)
        try:
            image_url = await self.text_to_image(markdown_text)
            if image_url:
                await self.context.send_message(target_origin, MessageChain().image(image_url))
            else:
                raise Exception("text_to_image 返回空")
        except Exception as e:
            logger.warning(f"图片渲染失败，降级为纯文本: {e}")
            fallback_msg = f"📢 画师 {artist_id} 有新评价！\n甲方：{commenter_name}\n📝 {content}\n📅 {time_str}"
            await self.context.send_message(target_origin, MessageChain().message(fallback_msg))

    async def _auto_check_all_subscriptions(self):
        logger.info("[米画师] 定时检查所有订阅画师")
        subs_file = self.data_dir / "subscriptions.json"
        if not subs_file.exists():
            return
        try:
            async with self._file_lock:
                with open(subs_file, 'r', encoding='utf-8') as f:
                    subscriptions = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"读取订阅文件失败: {e}")
            return

        global_target = self.global_push_target.strip()
        if global_target:
            if global_target.isdigit():
                global_origin = f"aiocqhttp:group_{global_target}"
            else:
                global_origin = global_target
            logger.info(f"使用全局推送目标: {global_origin}")

        artist_targets = {}
        for sub in subscriptions:
            aid = sub.get("artist_id")
            if global_target:
                target = global_origin
            else:
                target = sub.get("target_session")
            if aid and target:
                artist_targets.setdefault(aid, set()).add(target)

        for artist_id, targets in artist_targets.items():
            new_reviews = await self.check_and_notify(artist_id)
            if new_reviews:
                for rev in new_reviews[:5]:
                    for target in targets:
                        await self._send_as_image(target, artist_id, rev)
                logger.info(f"画师 {artist_id} 有 {len(new_reviews)} 条新评价，已推送")

    @filter.command("check_review")
    async def check_review(self, event: AstrMessageEvent):
        args = event.message_str.strip().split()
        if len(args) >= 2:
            artist_id = args[1]
        elif self.default_artist_id:
            artist_id = self.default_artist_id
            yield event.plain_result(f"ℹ️ 未指定画师ID，使用默认画师: {artist_id}")
        else:
            yield event.plain_result("请提供画师ID，例如：/check_review 182276，或在插件配置中设置默认画师ID")
            return

        yield event.plain_result(f"🔍 正在检查画师 {artist_id} 的评价...")
        data = await self.fetch_reviews(artist_id)
        artist_info = data.get('artist_info', {})
        artist_name = artist_info.get('name', artist_id)
        artist_avatar = artist_info.get('avatar', '')
        reviews = data.get('reviews', [])

        if not reviews:
            yield event.plain_result(f"❌ 未找到画师 {artist_id} 的评价")
            return

        display_count = min(len(reviews), self.max_display)

        # 构建 Markdown 内容：头像紧靠画师名左侧，尺寸更大
        markdown_lines = [f"# 🎨 米画师评价列表"]
        if artist_avatar:
            # 使用 <span> 包裹，强制行内布局，头像宽度 64px，保持清晰
            avatar_html = f'<span style="display: inline-block; vertical-align: middle; margin-right: 8px;"><img src="{artist_avatar}" width="64" style="border-radius: 50%; display: block;"></span>'
            markdown_lines.append(f"**画师**: {avatar_html} **{artist_name}**")
        else:
            markdown_lines.append(f"**画师**: {artist_name}")

        # 如果没有从配置中找到名字（且不是默认ID），提示用户配置
        if artist_name == artist_id and str(artist_id) not in self.artist_info_map:
            markdown_lines.append("> ⚠️ **提示**：画师名称未配置，请前往插件配置页面 `画师信息列表` 中填写。")

        markdown_lines.extend(["", f"共找到 **{len(reviews)}** 条评价", ""])

        for i, rev in enumerate(reviews[:display_count], 1):
            markdown_lines.append(f"### {i}. 📅 {rev['time']} **甲方**: {rev.get('commenter_name', '匿名')}")
            markdown_lines.append(f"{rev['content'][:300]}")
            markdown_lines.append("")

        markdown_text = "\n".join(markdown_lines)

        try:
            image_url = await self.text_to_image(markdown_text)
            if image_url:
                yield event.image_result(image_url)
            else:
                raise Exception("text_to_image 返回空")
        except Exception as e:
            logger.warning(f"图片渲染失败，降级为纯文本: {e}")
            text_msg = f"✅ 共找到 {len(reviews)} 条评价\n\n"
            for i, rev in enumerate(reviews[:display_count], 1):
                text_msg += f"{i}. {rev['content'][:100]}\n   甲方: {rev.get('commenter_name', '匿名')}\n   📅 {rev['time']}\n\n"
            yield event.plain_result(text_msg)

    @filter.command("subscribe")
    async def subscribe(self, event: AstrMessageEvent):
        args = event.message_str.strip().split()
        if len(args) < 2:
            yield event.plain_result("请提供画师ID，例如：/subscribe 182276")
            return
        artist_id = args[1]
        user_id = event.get_sender_id()
        target_session = event.unified_msg_origin

        subs_file = self.data_dir / "subscriptions.json"
        subscriptions = []
        async with self._file_lock:
            try:
                if subs_file.exists():
                    with open(subs_file, 'r', encoding='utf-8') as f:
                        subscriptions = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"读取订阅文件失败，将重新初始化: {e}")
                subscriptions = []

        new_sub = {
            "artist_id": artist_id,
            "user_id": user_id,
            "target_session": target_session,
            "subscribe_time": int(time.time())
        }
        exists = any(s.get("artist_id") == artist_id and s.get("target_session") == target_session for s in subscriptions)
        if not exists:
            subscriptions.append(new_sub)
            async with self._file_lock:
                temp_path = subs_file.with_suffix(".tmp")
                try:
                    with open(temp_path, 'w', encoding='utf-8') as f:
                        json.dump(subscriptions, f, ensure_ascii=False, indent=2)
                    temp_path.replace(subs_file)
                except Exception as e:
                    logger.error(f"保存订阅失败: {e}")
                    yield event.plain_result("❌ 订阅失败，请稍后重试")
                    return
            yield event.plain_result(f"✅ 已订阅画师 {artist_id} 的评价更新，将推送至当前会话")
        else:
            yield event.plain_result(f"ℹ️ 当前会话已订阅过画师 {artist_id}")

    @filter.command("unsubscribe")
    async def unsubscribe(self, event: AstrMessageEvent):
        args = event.message_str.strip().split()
        if len(args) < 2:
            yield event.plain_result("请提供画师ID，例如：/unsubscribe 182276")
            return
        artist_id = args[1]
        target_session = event.unified_msg_origin

        subs_file = self.data_dir / "subscriptions.json"
        if not subs_file.exists():
            yield event.plain_result("你还没有任何订阅")
            return

        async with self._file_lock:
            try:
                with open(subs_file, 'r', encoding='utf-8') as f:
                    subscriptions = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.error(f"读取订阅文件失败: {e}")
                yield event.plain_result("❌ 读取订阅失败，请稍后重试")
                return

            new_subs = [s for s in subscriptions if not (s.get("artist_id") == artist_id and s.get("target_session") == target_session)]
            if len(new_subs) < len(subscriptions):
                temp_path = subs_file.with_suffix(".tmp")
                try:
                    with open(temp_path, 'w', encoding='utf-8') as f:
                        json.dump(new_subs, f, ensure_ascii=False, indent=2)
                    temp_path.replace(subs_file)
                except Exception as e:
                    logger.error(f"保存取消订阅失败: {e}")
                    yield event.plain_result("❌ 取消订阅失败，请稍后重试")
                    return
                yield event.plain_result(f"✅ 已取消订阅画师 {artist_id}（当前会话）")
            else:
                yield event.plain_result(f"❌ 当前会话没有订阅画师 {artist_id}")

    @filter.command("list_sub")
    async def list_subscriptions(self, event: AstrMessageEvent):
        target_session = event.unified_msg_origin
        subs_file = self.data_dir / "subscriptions.json"
        if not subs_file.exists():
            yield event.plain_result("你还没有任何订阅")
            return
        try:
            with open(subs_file, 'r', encoding='utf-8') as f:
                subscriptions = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"读取订阅文件失败: {e}")
            yield event.plain_result("❌ 读取订阅失败，请稍后重试")
            return
        my_subs = [s for s in subscriptions if s.get("target_session") == target_session]
        if my_subs:
            msg = "📋 当前会话的订阅列表：\n" + "\n".join([f"- 画师ID: {s['artist_id']}" for s in my_subs])
            yield event.plain_result(msg)
        else:
            yield event.plain_result("当前会话没有订阅任何画师")

    async def terminate(self):
        self._stop_scheduler()