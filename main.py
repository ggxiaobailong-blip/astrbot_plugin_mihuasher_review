from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import requests
import json
import os
import time
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

REVIEW_HTML_TEMPLATE = """
<div style="font-family: 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif; max-width: 600px; margin: 0 auto; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 20px; padding: 20px; color: white; box-shadow: 0 8px 20px rgba(0,0,0,0.2);">
    <div style="text-align: center; margin-bottom: 15px;">
        <span style="font-size: 32px;">🎨</span>
        <h2 style="margin: 5px 0 0; font-weight: 600;">米画师评价监控</h2>
    </div>
    <div style="background: rgba(255,255,255,0.15); border-radius: 16px; padding: 15px; margin: 15px 0;">
        <p style="margin: 0 0 8px;"><strong>🖌️ 画师 ID：</strong> {{ artist_id }}</p>
        <p style="margin: 0;"><strong>📝 最新评价：</strong></p>
        <p style="margin: 8px 0 0; font-size: 15px; line-height: 1.5; background: rgba(0,0,0,0.2); padding: 10px; border-radius: 12px;">
            {{ content }}
        </p>
        <p style="text-align: right; margin: 12px 0 0; font-size: 12px; opacity: 0.9;">📅 {{ time }}</p>
    </div>
    <div style="font-size: 11px; text-align: center; opacity: 0.7;">✨ 来自你的专属画师监控小助手 ✨</div>
</div>
"""

@register(
    "astrbot_plugin_mihuasher_review",
    "iv小白龙",
    "获取米画师画师评价并推送",
    "1.0.0",
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
        self.push_cron = config.get("push_cron", "0 */30 * * * *")
        self.global_push_target = config.get("push_target", "")

        self.data_dir = os.path.join(os.path.dirname(__file__), "data")
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)

        self.scheduler = None
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

    def get_storage_file(self, artist_id):
        return os.path.join(self.data_dir, f"artist_{artist_id}.json")

    def load_saved_reviews(self, artist_id):
        file_path = self.get_storage_file(artist_id)
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"last_reviews": []}

    def save_reviews(self, artist_id, reviews):
        file_path = self.get_storage_file(artist_id)
        if len(reviews) > 100:
            reviews = reviews[:100]
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump({"last_reviews": reviews}, f, ensure_ascii=False, indent=2)

    def fetch_reviews(self, artist_id):
        if not self.cookie:
            logger.error("未配置米画师 Cookie，请在插件配置中设置")
            return []
        api_url = f"https://www.mihuashi.com/api/v1/users/{artist_id}/comments"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Referer': f'https://www.mihuashi.com/profiles/{artist_id}',
            'Origin': 'https://www.mihuashi.com',
            'X-Requested-With': 'XMLHttpRequest',
            'Cookie': self.cookie,
        }
        params = {
            'page': 1,
            'perspective': 'third',
            'type': 'employer',
            'only_image': 'false'
        }
        try:
            response = requests.get(api_url, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            reviews = []
            if 'comments' in data and isinstance(data['comments'], list):
                for item in data['comments']:
                    content = item.get('content', '')
                    raw_time = item.get('created_at', '')
                    time_str = raw_time.split('T')[0] if 'T' in raw_time else raw_time
                    if content:
                        reviews.append({'content': content.strip(), 'time': time_str})
            logger.info(f"成功获取 {len(reviews)} 条评价")
            return reviews
        except Exception as e:
            logger.error(f"获取评价失败: {str(e)}")
            return []

    def check_and_notify(self, artist_id, target_user=None):
        current_reviews = self.fetch_reviews(artist_id)
        if not current_reviews:
            return []
        saved_data = self.load_saved_reviews(artist_id)
        saved_reviews = saved_data.get("last_reviews", [])
        new_reviews = [r for r in current_reviews if r not in saved_reviews]
        if new_reviews:
            self.save_reviews(artist_id, current_reviews)
        return new_reviews

    async def _send_as_image(self, target_origin: str, artist_id: str, content: str, time_str: str):
        from textwrap import dedent
        markdown_text = dedent(f"""\
            # 🎨 米画师新评价
            **画师 ID**: `{artist_id}`

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
            fallback_msg = f"📢 画师 {artist_id} 有新评价！\n📝 {content}\n📅 {time_str}"
            await self.context.send_message(target_origin, MessageChain().message(fallback_msg))

    async def _auto_check_all_subscriptions(self):
        logger.info("[米画师] 定时检查所有订阅画师")
        subs_file = os.path.join(self.data_dir, "subscriptions.json")
        if not os.path.exists(subs_file):
            return
        with open(subs_file, 'r', encoding='utf-8') as f:
            subscriptions = json.load(f)

        global_target = self.global_push_target.strip()
        if global_target:
            if global_target.isdigit():
                global_origin = f"aiocqhttp:group_{global_target}"
            else:
                global_origin = global_target
            logger.info(f"使用全局推送目标: {global_origin}")

        artist_users = {}
        for sub in subscriptions:
            aid = sub.get("artist_id")
            if global_target:
                target = global_origin
            else:
                target = sub.get("target_session")
            if aid and target:
                artist_users.setdefault(aid, set()).add(target)

        for artist_id, targets in artist_users.items():
            new_reviews = self.check_and_notify(artist_id, None)
            if new_reviews:
                for rev in new_reviews[:5]:
                    for target in targets:
                        await self._send_as_image(target, artist_id, rev['content'], rev['time'])
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
        reviews = self.fetch_reviews(artist_id)

        # 调试日志（现在 reviews 已经定义）
        logger.info(f"reviews 内容示例：{reviews[0] if reviews else '空'}")

        if not reviews:
            yield event.plain_result(f"❌ 未找到画师 {artist_id} 的评价")
            return

        display_count = min(len(reviews), self.max_display)
        # 构建 Markdown 格式的消息
        markdown_lines = [
            f"# 🎨 米画师评价列表",
            f"**画师 ID**: `{artist_id}`",
            "",
            f"共找到 **{len(reviews)}** 条评价",
            "",
        ]

        for i, rev in enumerate(reviews[:display_count], 1):
            markdown_lines.append(f"### {i}. 📅 {rev['time']}")
            markdown_lines.append(f"{rev['content'][:300]}")
            markdown_lines.append("")  # 空行分隔

        markdown_text = "\n".join(markdown_lines)

        # 渲染图片
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
                text_msg += f"{i}. {rev['content'][:100]}\n   📅 {rev['time']}\n\n"
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

        subs_file = os.path.join(self.data_dir, "subscriptions.json")
        subscriptions = []
        if os.path.exists(subs_file):
            with open(subs_file, 'r', encoding='utf-8') as f:
                subscriptions = json.load(f)

        new_sub = {
            "artist_id": artist_id,
            "user_id": user_id,
            "target_session": target_session,
            "subscribe_time": int(time.time())
        }
        exists = any(s.get("artist_id") == artist_id and s.get("target_session") == target_session for s in subscriptions)
        if not exists:
            subscriptions.append(new_sub)
            with open(subs_file, 'w', encoding='utf-8') as f:
                json.dump(subscriptions, f, ensure_ascii=False, indent=2)
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

        subs_file = os.path.join(self.data_dir, "subscriptions.json")
        if not os.path.exists(subs_file):
            yield event.plain_result("你还没有任何订阅")
            return

        with open(subs_file, 'r', encoding='utf-8') as f:
            subscriptions = json.load(f)
        new_subs = [s for s in subscriptions if not (s.get("artist_id") == artist_id and s.get("target_session") == target_session)]
        if len(new_subs) < len(subscriptions):
            with open(subs_file, 'w', encoding='utf-8') as f:
                json.dump(new_subs, f, ensure_ascii=False, indent=2)
            yield event.plain_result(f"✅ 已取消订阅画师 {artist_id}（当前会话）")
        else:
            yield event.plain_result(f"❌ 当前会话没有订阅画师 {artist_id}")

    @filter.command("list_sub")
    async def list_subscriptions(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        subs_file = os.path.join(self.data_dir, "subscriptions.json")
        if not os.path.exists(subs_file):
            yield event.plain_result("你还没有任何订阅")
            return
        with open(subs_file, 'r', encoding='utf-8') as f:
            subscriptions = json.load(f)
        target_session = event.unified_msg_origin
        my_subs = [s for s in subscriptions if s.get("target_session") == target_session]
        if my_subs:
            msg = "📋 当前会话的订阅列表：\n" + "\n".join([f"- 画师ID: {s['artist_id']}" for s in my_subs])
            yield event.plain_result(msg)
        else:
            yield event.plain_result("当前会话没有订阅任何画师")

    async def terminate(self):
        self._stop_scheduler()