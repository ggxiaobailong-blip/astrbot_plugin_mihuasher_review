# 米画师画师评价监控插件 for AstrBot

[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-blue)](https://astrbot.app)

一个用于获取米画师指定画师评价列表，并支持订阅推送的 AstrBot 插件。

## 功能特性

- ✅ 通过 QQ 命令手动查询画师评价
- ✅ 订阅画师，自动推送新评价（需配置定时任务）
- ✅ 支持在 WebUI 中配置 Cookie（避免硬编码）
- ✅ 评价数据本地缓存，避免重复推送
- ✅ 完全兼容 AstrBot v4.14.0+ 主动任务系统

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

1. 使用 Chrome 浏览器登录米画师网站（https://www.mihuashi.com）
2. 打开开发者工具（F12）→ Network（网络）标签
3. 刷新页面，在请求列表中找到任意一个 `api/v1/` 开头的请求
4. 点击该请求，在 **Request Headers** 中找到 `cookie:` 字段
5. **完整复制** cookie 的值（一长串字符）

### 2. 配置插件
- 在 AstrBot 后台 → 插件配置 → 找到 `astrbot_plugin_mihuasher_review`
- 粘贴 Cookie 到 `米画师 Cookie` 输入框
- （可选）设置默认画师 ID，方便快速查询
- （可选）设置每次展示的最大评价数（默认10条）
- 保存配置，然后重载插件

### 3. 测试
在 QQ 中发送：/check_review 画师id

如果返回评价列表，说明配置成功。

## 命令列表

| 命令 | 说明 | 示例 |
|------|------|------|
| `/check_review [画师ID]` | 手动查询画师评价。不提供ID时使用默认画师 | `/check_review 182276` |
| `/subscribe <画师ID>` | 订阅指定画师的评价更新 | `/subscribe 182276` |
| `/unsubscribe <画师ID>` | 取消订阅 | `/unsubscribe 182276` |
| `/list_sub` | 查看我的订阅列表 | `/list_sub` |

## 定时自动推送设置

本插件支持 AstrBot 主动任务系统，可实现定时检查所有订阅画师的新评价并自动推送。

### 开启步骤：
1. 确保 AstrBot 版本 ≥ 4.14.0
2. 在 AstrBot 后台 → 主动任务 → 启用主动任务
3. 编辑插件 `main.py`，找到 `auto_check_all_subscriptions` 函数前的注释 `# @filter.scheduled`，删除 `#` 启用它
4. 可根据需要修改 Cron 表达式（默认为每30分钟执行一次）
5. 重载插件

### Cron 表达式示例：
- `0 */30 * * * *` → 每30分钟
- `0 0 8,20 * * *` → 每天 8:00 和 20:00
- `*/10 * * * * *` → 每10秒（仅测试用）

## 常见问题

### Q: 命令返回“未找到评价”，但网页上有评价？
A: 可能原因：1) Cookie 失效，请重新获取并更新；2) 画师ID错误，检查URL中的数字。

### Q: 如何更新 Cookie？
A: 在 AstrBot 后台 → 插件配置 → 粘贴新的 Cookie → 保存 → 重载插件。

### Q: 定时任务没有执行？
A: 请确认：1) AstrBot 主动任务已启用；2) 插件中的定时函数未被注释；3) 日志中是否有错误信息。

### Q: 推送消息收不到？
A: 本插件默认使用私聊发送，请确保你的机器人账号与你的QQ号是好友关系，或群聊中机器人有权限 @ 你。

## 数据存储

- 订阅列表保存在插件目录的 `data/subscriptions.json`
- 每个画师的评价缓存保存在 `data/artist_<ID>.json`

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