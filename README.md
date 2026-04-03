# 米画师画师评价监控插件 for AstrBot

[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-blue)](https://astrbot.app)

一个用于获取米画师指定画师评价列表，并支持订阅推送的 AstrBot 插件。

## 功能特性

- ✅ 通过 QQ 命令手动查询画师评价（支持图片输出）
- ✅ 订阅画师，自动推送新评价到订阅的群聊或私聊
- ✅ 支持 WebUI 中配置画师名字和头像（每画师独立）
- ✅ 评价数据本地缓存，避免重复推送
- ✅ 定时检查（Cron 表达式可自定义）
- ✅ 全局推送目标（可选，统一发送到指定群）

## 安装方法

### 方法一：从插件市场安装（推荐）
1. 打开 AstrBot 后台 → 插件市场
2. 搜索 `米画师` 或 `mihuasher`
3. 点击安装

### 方法二：手动安装
1. 下载本插件源码
2. 将文件夹放入 `AstrBot/data/plugins/` 目录
3. 在 AstrBot 后台 → 插件 → 启用插件

## 初次配置

### 1. 获取米画师 Cookie（必须）

插件需要通过 Cookie 访问米画师 API，请按以下步骤获取：

1. 使用 Chrome 浏览器登录米画师网站：https://www.mihuashi.com
2. 打开开发者工具（F12）→ Network（网络）标签
3. 刷新页面，在请求列表中找到任意一个 `api/v1/` 开头的请求
4. 点击该请求，在 **Request Headers** 中找到 `cookie:` 字段
5. **完整复制** cookie 的值（一长串字符）

### 2. 配置插件

在 AstrBot 后台 → 插件配置 → 找到 `astrbot_plugin_mihuasher_review`，填写以下信息：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `cookie` | 米画师 Cookie（必填） | `aliyungf_tc=...` |
| `default_artist_id` | 默认画师 ID（可选） | `1093248` |
| `max_reviews_display` | 手动查询时最多显示几条评价 | `10` |
| `enable_auto_push` | 是否启用定时自动推送 | `true` / `false` |
| `push_cron` | 定时推送的 Cron 表达式（5段） | `*/30 * * * *` |
| `push_target` | 全局推送目标群号（可选） | `123456789` |
| `artist_info_list` | 画师信息列表，一行一个画师 | 见下方说明 |

#### `artist_info_list` 填写格式

每行一个画师，格式为：画师ID,画师名,头像URL

示例：
1093248,冰宫Asylum,https://image-assets.mihuashi.com/permanent/1093248|-2023/12/23/00/FoEuzgtPojt0dM0nwTm23OzuH9un.jpg
182276,小企鹅呐,https://image-assets.mihuashi.com/default_avatar/employer3.jpg

> **如何获取画师头像 URL？**  
> 打开画师主页（如 `https://www.mihuashi.com/profiles/1093248`），右键点击头像 → “复制图片地址”，即可获得头像 URL。

### 3. 测试

在 QQ 中发送：/check_review 1093248

如果返回评价列表图片，说明配置成功。

## 命令列表

| 命令 | 说明 | 示例 |
|------|------|------|
| `/check_review [画师ID]` | 手动查询画师评价（不填ID则使用默认画师） | `/check_review 1093248` |
| `/subscribe <画师ID>` | 订阅指定画师的评价更新（在群聊中发送则推送到该群，在私聊中发送则推送到私聊） | `/subscribe 1093248` |
| `/unsubscribe <画师ID>` | 取消订阅 | `/unsubscribe 1093248` |
| `/list_sub` | 查看当前会话的订阅列表 | `/list_sub` |

## 定时自动推送设置

1. 在插件配置中启用 `enable_auto_push`。
2. 根据需要修改 `push_cron`（默认每30分钟检查一次）。
3. 插件会自动启动调度器，无需额外配置。

### Cron 表达式示例（5段）
- `*/30 * * * *` → 每30分钟
- `0 8,20 * * *` → 每天 8:00 和 20:00
- `*/5 * * * *` → 每5分钟（测试用）

## 常见问题

### Q: 命令返回“未找到评价”，但网页上有评价？
A: 可能原因：1) Cookie 失效，请重新获取并更新；2) 画师ID错误。

### Q: 如何更新 Cookie？
A: 在 AstrBot 后台 → 插件配置 → 粘贴新的 Cookie → 保存 → 重载插件。

### Q: 定时任务没有执行？
A: 请检查：1) `enable_auto_push` 已设为 `true`；2) `push_cron` 表达式正确；3) 日志中是否有 `[米画师] 自动推送已启用` 的信息。

### Q: 推送消息收不到？
A: 本插件会推送到**订阅时所在的会话**（群聊或私聊）。请确保：
- 如果是在群聊中订阅，机器人有权限在群里发言。
- 如果是在私聊中订阅，请确保机器人账号与你的QQ号是好友关系。

### Q: 头像不显示或位置不对？
A: 请确认 `artist_info_list` 中填写的头像 URL 可以正常访问（在浏览器中打开试试）。如果 URL 有效但仍不显示，可能是渲染器兼容问题，插件会自动降级为纯文本消息。

## 数据存储

- 订阅列表保存在插件数据目录的 `subscriptions.json`
- 每个画师的评价缓存保存在 `artist_{ID}.json`
- 数据目录通过 `StarTools.get_data_dir()` 获取，位于 AstrBot 数据目录下

## 更新日志

### v1.0.4 (2026-04-03)
- 🚀 **性能优化**：定时检查时并发处理多个画师（最多5个并发），大幅提升推送效率。
- 🔒 **并发安全**：所有订阅文件操作均使用原子锁，避免数据丢失。
- 🆔 **去重增强**：使用评论ID或内容指纹进行去重，防止重复推送。
- 🗄️ **智能缓存**：画师信息（名字/头像）缓存1天，减少网络请求。
- 🛡️ **健壮性提升**：
  - 画师ID格式校验（必须为数字）
  - 错误提示细化（区分Cookie失效、网络超时等）
  - `max_reviews_display` 限制在1-50之间
- 🔧 **兼容性**：自动将6段Cron表达式转换为5段，避免旧配置报错。
- 🧊 **冷启动保护**：首次运行只保存评价，不推送，避免消息轰炸。

## 开发与贡献

欢迎提交 Issue 和 Pull Request。

### 本地开发调试
1. 克隆本仓库到 `AstrBot/data/plugins/`
2. 修改代码后，在 AstrBot 后台点击“重载插件”
3. 查看日志输出

## 许可证

MIT License

## 致谢

- [AstrBot](https://github.com/Soulter/AstrBot) 提供的机器人框架
- 米画师平台（仅供个人学习使用，请勿滥用）
