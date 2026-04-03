import asyncio
import json
import time
import hashlib
from pathlib import Path
from textwrap import dedent
from typing import Optional, Dict, Any, List, Set, Callable, Awaitable

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from bs4 import BeautifulSoup

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register, StarTools


@register(
    "astrbot_plugin_mihuasher_review",
    "iv小白龙",
    "获取米画师画师评价并推送",
    "1.0.7",
    "https://github.com/ggxiaobailong-blip/astrbot_plugin_mihuasher_review"
)
class MihuasherReviewPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        config = config or {}
        self.cookie = config.get("cookie", "")
        self.default_artist_id = config.get("default_artist_id", "")

        try:
            self.max_display = int(config.get("max_reviews_display", 10))
        except (ValueError, TypeError):
            self.max_display = 10
        self.max_display = max(1, min(50, self.max_display))

        try:
            self.max_cached_reviews = int(config.get("max_cached_reviews", 500))
        except (ValueError, TypeError):
            self.max_cached_reviews = 500
        self.max_cached_reviews = max(100, min(2000, self.max_cached_reviews))

        self.enable_auto_push = config.get("enable_auto_push", False)
        self.push_cron = config.get("push_cron", "*/30 * * * *")
        self.global_push_target = config.get("push_target", "")

        # 解析画师信息列表（WebUI配置）
        self.artist_info_map: Dict[str, Dict[str, str]] = {}
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

        self._session: Optional[aiohttp.ClientSession] = None
        self._artist_info_cache: Dict[str, Dict[str, Any]] = {}

        self.scheduler: Optional[AsyncIOScheduler] = None
        self._file_lock = asyncio.Lock()

        logger.info(f"米画师插件已加载，Cookie 已配置: {bool(self.cookie)}")
        if self.default_artist_id:
            logger.info(f"默认画师ID: {self.default_artist_id}")

        if self.enable_auto_push and self.cookie:
            self._init_scheduler()
        elif self.enable_auto_push and not self.cookie:
            logger.warning("自动推送已启用但未配置 Cookie，无法启动调度器")

    # ==================== 辅助方法 ====================
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    @staticmethod
    def _html_escape(text: str) -> str:
        """转义 HTML 特殊字符，防止注入"""
        return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#39;')

    @staticmethod
    def _validate_avatar_url(url: str) -> str:
        """校验头像 URL 安全性，只允许 http/https 协议"""
        if url and (url.startswith('http://') or url.startswith('https://')):
            return url
        return ''

    async def _update_subscriptions(self, update_func: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]) -> bool:
        """原子更新订阅列表：在锁内完成读-修改-写"""
        async with self._file_lock:
            subs_file = self.data_dir / "subscriptions.json"
            subscriptions = []
            try:
                if subs_file.exists():
                    with open(subs_file, 'r', encoding='utf-8') as f:
                        subscriptions = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"读取订阅文件失败，将重新初始化: {e}")
            new_subscriptions = update_func(subscriptions)
            temp_path = subs_file.with_suffix(".tmp")
            try:
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(new_subscriptions, f, ensure_ascii=False, indent=2)
                temp_path.replace(subs_file)
                return True
            except Exception as e:
                logger.error(f"保存订阅文件失败: {e}")
                return False

    async def _load_subscriptions(self) -> List[Dict[str, Any]]:
        """仅读取订阅列表（不加锁，仅用于查询）"""
        subs_file = self.data_dir / "subscriptions.json"
        try:
            if subs_file.exists():
                with open(subs_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"读取订阅文件失败: {e}")
        return []

    def _get_review_key(self, review: Dict[str, Any]) -> str:
        """生成稳定的评论唯一键：优先使用 id，否则使用 sha256(内容+时间+评论者)"""
        if review.get('id'):
            return f"id:{review['id']}"
        content = review.get('content', '')
        time_str = review.get('time', '')
        commenter = review.get('commenter_name', '')
        fp_str = f"{content}|{time_str}|{commenter}"
        fp_hash = hashlib.sha256(fp_str.encode('utf-8')).hexdigest()
        return f"fp:{fp_hash}"

    def _get_global_target(self) -> Optional[str]:
        target = self.global_push_target.strip()
        if not target:
            return None
        if ':' in target:
            return target
        if target.isdigit():
            return f"aiocqhttp:group_{target}"
        return target

    # ==================== 调度器 ====================
    def _init_scheduler(self):
        if self.scheduler and self.scheduler.running:
            return
        if self.scheduler:
            self._stop_scheduler()
        self.scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        try:
            cron_expr = self.push_cron.strip()
            parts = cron_expr.split()
            if len(parts) == 6:
                cron_expr = ' '.join(parts[1:])
                logger.warning(f"[米画师] 6段cron自动转5段: {cron_expr}")
            trigger = CronTrigger.from_crontab(cron_expr)
            self.scheduler.add_job(
                func=self._auto_check_all_subscriptions,
                trigger=trigger,
                id="mihuasher_auto_push",
                max_instances=1,
                coalesce=True
            )
            self.scheduler.start()
            logger.info(f"[米画师] 自动推送已启用，Cron: {cron_expr}")
        except Exception as e:
            logger.error(f"[米画师] 启动调度器失败: {e}")

    def _stop_scheduler(self):
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown()
            self.scheduler = None
            logger.info("[米画师] 调度器已停止")
        elif self.scheduler:
            self.scheduler = None

    # ==================== 评价缓存 ====================
    def _get_storage_file(self, artist_id: str) -> Path:
        return self.data_dir / f"artist_{artist_id}.json"

    async def _load_saved_reviews(self, artist_id: str) -> Dict[str, Any]:
        file_path = self._get_storage_file(artist_id)
        try:
            async with self._file_lock:
                if file_path.exists():
                    with open(file_path, 'r', encoding='utf-8') as f:
                        return json.load(f)
        except (json.JSONDecodeError, OSError, IOError) as e:
            logger.warning(f"加载评价缓存失败 ({artist_id}): {e}")
        return {"last_reviews": []}

    async def _save_reviews(self, artist_id: str, reviews: List[Dict[str, Any]]):
        """保存评价缓存，按时间排序后裁剪"""
        # 先按时间倒序排序（假设API返回未严格排序，确保最新在前）
        sorted_reviews = sorted(reviews, key=lambda x: x.get('time', ''), reverse=True)
        if len(sorted_reviews) > self.max_cached_reviews:
            sorted_reviews = sorted_reviews[:self.max_cached_reviews]
        data = {"last_reviews": sorted_reviews}
        file_path = self._get_storage_file(artist_id)
        temp_path = file_path.with_suffix(".tmp")
        try:
            async with self._file_lock:
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                temp_path.replace(file_path)
        except Exception as e:
            logger.error(f"保存评价缓存失败 ({artist_id}): {e}")

    # ==================== 核心数据获取 ====================
    async def fetch_reviews(self, artist_id: str) -> Dict[str, Any]:
        if not self.cookie:
            return {"artist_info": {}, "reviews": [], "error": "未配置米画师 Cookie"}

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Referer': f'https://www.mihuashi.com/profiles/{artist_id}',
            'Origin': 'https://www.mihuashi.com',
            'X-Requested-With': 'XMLHttpRequest',
            'Cookie': self.cookie,
        }

        session = await self._get_session()
        reviews_url = f"https://www.mihuashi.com/api/v1/users/{artist_id}/comments"
        params = {'page': 1, 'perspective': 'third', 'type': 'employer', 'only_image': 'false'}
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
                                    'id': item.get('id'),
                                    'content': content.strip(),
                                    'time': time_str,
                                    'commenter_name': commenter_name
                                })
                    logger.info(f"成功获取 {len(reviews_data)} 条评价")
                elif resp.status == 403:
                    return {"artist_info": {}, "reviews": [], "error": "Cookie 无效或已过期"}
                else:
                    return {"artist_info": {}, "reviews": [], "error": f"API 返回错误码 {resp.status}"}
        except asyncio.TimeoutError:
            return {"artist_info": {}, "reviews": [], "error": "请求超时"}
        except aiohttp.ClientError as e:
            logger.error(f"网络请求异常: {e}")
            return {"artist_info": {}, "reviews": [], "error": f"网络错误: {str(e)}"}
        except Exception as e:
            logger.error(f"获取评价失败: {e}")
            return {"artist_info": {}, "reviews": [], "error": f"未知错误: {str(e)}"}

        # 获取画师信息
        map_info = self.artist_info_map.get(str(artist_id), {})
        if map_info:
            artist_name = map_info.get("name", str(artist_id))
            artist_avatar = self._validate_avatar_url(map_info.get("avatar", ""))
        else:
            now = time.time()
            cached = self._artist_info_cache.get(artist_id)
            if cached and cached.get('expire', 0) > now:
                artist_name = cached['name']
                artist_avatar = cached['avatar']
            else:
                artist_name = str(artist_id)
                artist_avatar = ""
                try:
                    profile_url = f"https://www.mihuashi.com/profiles/{artist_id}"
                    html_headers = {'User-Agent': headers['User-Agent'], 'Cookie': self.cookie}
                    async with session.get(profile_url, headers=html_headers, timeout=10) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            soup = BeautifulSoup(html, 'html.parser')
                            name_elem = soup.find('h2', class_='user-profile__name')
                            if name_elem:
                                artist_name = name_elem.get_text(strip=True)
                                logger.info(f"自动解析画师名字: {artist_name}")
                            avatar_img = soup.find('img', class_='h-full w-full object-cover')
                            if avatar_img and avatar_img.get('src'):
                                avatar_url = avatar_img['src']
                                if avatar_url.startswith('//'):
                                    avatar_url = 'https:' + avatar_url
                                artist_avatar = self._validate_avatar_url(avatar_url)
                                logger.info(f"自动解析画师头像: {artist_avatar[:80]}...")
                        else:
                            logger.warning(f"获取画师主页失败: {resp.status}")
                except aiohttp.ClientError as e:
                    logger.warning(f"请求画师主页网络错误: {e}")
                except Exception as e:
                    logger.warning(f"解析画师信息失败: {e}")
                self._artist_info_cache[artist_id] = {
                    'name': artist_name,
                    'avatar': artist_avatar,
                    'expire': time.time() + 86400
                }

        return {
            "artist_info": {"name": artist_name, "avatar": artist_avatar},
            "reviews": reviews_data,
            "error": None
        }

    async def check_and_notify(self, artist_id: str) -> List[Dict[str, Any]]:
        result = await self.fetch_reviews(artist_id)
        if result.get("error"):
            logger.error(f"检查画师 {artist_id} 失败: {result['error']}")
            return []
        current_reviews = result.get('reviews', [])
        if not current_reviews:
            return []

        saved_data = await self._load_saved_reviews(artist_id)
        saved_reviews = saved_data.get("last_reviews", [])

        is_cold_start = not saved_reviews

        saved_keys = {self._get_review_key(r) for r in saved_reviews}
        new_reviews = [r for r in current_reviews if self._get_review_key(r) not in saved_keys]

        if new_reviews:
            await self._save_reviews(artist_id, current_reviews)
            if is_cold_start:
                logger.info(f"画师 {artist_id} 首次缓存，已保存 {len(new_reviews)} 条评价，不推送")
                return []
        return new_reviews

    # ==================== 消息发送 ====================
    async def _send_as_image(self, target_origin: str, artist_id: str, rev: Dict[str, Any]):
        content = rev['content']
        time_str = rev['time']
        commenter_name = rev.get('commenter_name', '匿名')
        # 转义内容防止注入
        safe_content = self._html_escape(content)
        safe_commenter = self._html_escape(commenter_name)
        markdown_text = dedent(f"""\
            # 🎨 米画师新评价
            **画师 ID**: `{artist_id}`

            **甲方**: {safe_commenter}

            **📝 评价内容**:
            {safe_content}

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

    # ==================== 定时任务 ====================
    async def _auto_check_all_subscriptions(self):
        logger.info("[米画师] 定时检查所有订阅画师")
        subscriptions = await self._load_subscriptions()
        if not subscriptions:
            return

        global_target = self._get_global_target()
        if global_target:
            logger.info(f"使用全局推送目标: {global_target}")

        artist_targets: Dict[str, Set[str]] = {}
        for sub in subscriptions:
            aid = sub.get("artist_id")
            if global_target:
                target = global_target
            else:
                target = sub.get("target_session")
            if aid and target:
                artist_targets.setdefault(aid, set()).add(target)

        if not artist_targets:
            return

        semaphore = asyncio.Semaphore(5)

        async def check_one(artist_id: str):
            async with semaphore:
                new_reviews = await self.check_and_notify(artist_id)
                return (artist_id, artist_targets[artist_id], new_reviews) if new_reviews else None

        check_tasks = [check_one(aid) for aid in artist_targets.keys()]
        results = await asyncio.gather(*check_tasks)

        push_tasks = []
        for res in results:
            if res is None:
                continue
            artist_id, targets, new_reviews = res
            for rev in new_reviews[:5]:
                for target in targets:
                    push_tasks.append(self._send_as_image(target, artist_id, rev))

        if push_tasks:
            push_sem = asyncio.Semaphore(5)

            async def limited_push(task):
                async with push_sem:
                    await task

            # 使用 return_exceptions=True 确保单个失败不影响其他推送
            results = await asyncio.gather(*[limited_push(t) for t in push_tasks], return_exceptions=True)
            # 记录失败
            for i, res in enumerate(results):
                if isinstance(res, Exception):
                    logger.error(f"推送消息失败: {res}")

            logger.info(f"本次定时检查共推送 {len(push_tasks)} 条消息")

    # ==================== 命令 ====================
    @filter.command("check_review")
    async def check_review(self, event: AstrMessageEvent):
        plain_text = event.message_str
        args = plain_text.strip().split()
        if len(args) >= 2:
            artist_id = args[1]
        elif self.default_artist_id:
            artist_id = self.default_artist_id
            yield event.plain_result(f"ℹ️ 未指定画师ID，使用默认画师: {artist_id}")
        else:
            yield event.plain_result("请提供画师ID，例如：/check_review 182276，或在插件配置中设置默认画师ID")
            return

        if not artist_id.isdigit():
            yield event.plain_result("❌ 画师ID必须是数字")
            return

        yield event.plain_result(f"🔍 正在检查画师 {artist_id} 的评价...")
        result = await self.fetch_reviews(artist_id)

        if result.get("error"):
            yield event.plain_result(f"❌ 获取评价失败：{result['error']}")
            return

        artist_info = result.get('artist_info', {})
        artist_name = artist_info.get('name', artist_id)
        artist_avatar = artist_info.get('avatar', '')
        reviews = result.get('reviews', [])

        if not reviews:
            yield event.plain_result(f"✅ 画师 {artist_id} 暂时没有评价")
            return

        display_count = min(len(reviews), self.max_display)

        # 构建 Markdown 并转义不安全内容
        safe_artist_name = self._html_escape(artist_name)
        markdown_lines = ["# 🎨 米画师评价列表"]
        if artist_avatar:
            avatar_html = (
                f'<span style="display: inline-block; vertical-align: middle; margin-right: 10px;">'
                f'<img src="{artist_avatar}" width="64" style="border-radius: 50%; display: block;"></span>'
            )
            markdown_lines.append(f"**画师**: {avatar_html} **{safe_artist_name}**")
        else:
            markdown_lines.append(f"**画师**: {safe_artist_name}")

        if artist_name == artist_id and str(artist_id) not in self.artist_info_map:
            markdown_lines.append("> ⚠️ **提示**：画师名称未配置，请前往插件配置页面 `画师信息列表` 中填写。")

        markdown_lines.extend(["", f"共找到 **{len(reviews)}** 条评价", ""])

        for i, rev in enumerate(reviews[:display_count], 1):
            safe_content = self._html_escape(rev['content'][:300])
            safe_commenter = self._html_escape(rev.get('commenter_name', '匿名'))
            markdown_lines.append(f"### {i}. 📅 {rev['time']} **甲方**: {safe_commenter}")
            markdown_lines.append(f"{safe_content}")
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
        plain_text = event.message_str
        args = plain_text.strip().split()
        if len(args) < 2:
            yield event.plain_result("请提供画师ID，例如：/subscribe 182276")
            return
        artist_id = args[1]
        if not artist_id.isdigit():
            yield event.plain_result("❌ 画师ID必须是数字")
            return

        user_id = event.get_sender_id()
        target_session = event.unified_msg_origin

        # 原子更新订阅列表
        async def update(subscriptions: List[Dict]) -> List[Dict]:
            exists = any(
                s.get("artist_id") == artist_id and s.get("target_session") == target_session
                for s in subscriptions
            )
            if not exists:
                subscriptions.append({
                    "artist_id": artist_id,
                    "user_id": user_id,
                    "target_session": target_session,
                    "subscribe_time": int(time.time())
                })
            return subscriptions

        success = await self._update_subscriptions(update)
        if not success:
            yield event.plain_result("❌ 订阅失败，请稍后重试")
            return

        # 检查是否已存在（用于返回提示）
        subscriptions = await self._load_subscriptions()
        exists = any(
            s.get("artist_id") == artist_id and s.get("target_session") == target_session
            for s in subscriptions
        )
        if exists:
            yield event.plain_result(f"✅ 已订阅画师 {artist_id} 的评价更新，将推送至当前会话")
        else:
            yield event.plain_result(f"ℹ️ 当前会话已订阅过画师 {artist_id}")

    @filter.command("unsubscribe")
    async def unsubscribe(self, event: AstrMessageEvent):
        plain_text = event.message_str
        args = plain_text.strip().split()
        if len(args) < 2:
            yield event.plain_result("请提供画师ID，例如：/unsubscribe 182276")
            return
        artist_id = args[1]
        if not artist_id.isdigit():
            yield event.plain_result("❌ 画师ID必须是数字")
            return

        target_session = event.unified_msg_origin

        async def update(subscriptions: List[Dict]) -> List[Dict]:
            return [s for s in subscriptions
                    if not (s.get("artist_id") == artist_id and s.get("target_session") == target_session)]

        old_subs = await self._load_subscriptions()
        old_len = len(old_subs)
        success = await self._update_subscriptions(update)
        if not success:
            yield event.plain_result("❌ 取消订阅失败，请稍后重试")
            return

        new_subs = await self._load_subscriptions()
        if len(new_subs) < old_len:
            yield event.plain_result(f"✅ 已取消订阅画师 {artist_id}（当前会话）")
        else:
            yield event.plain_result(f"❌ 当前会话没有订阅画师 {artist_id}")

    @filter.command("list_sub")
    async def list_subscriptions(self, event: AstrMessageEvent):
        target_session = event.unified_msg_origin
        subscriptions = await self._load_subscriptions()
        if not subscriptions:
            yield event.plain_result("你还没有任何订阅")
            return

        my_subs = [s for s in subscriptions if s.get("target_session") == target_session]
        if my_subs:
            msg = "📋 当前会话的订阅列表：\n" + "\n".join([f"- 画师ID: {s['artist_id']}" for s in my_subs])
            yield event.plain_result(msg)
        else:
            yield event.plain_result("当前会话没有订阅任何画师")

    async def terminate(self):
        self._stop_scheduler()
        if self._session:
            await self._session.close()
            logger.info("[米画师] HTTP 会话已关闭")