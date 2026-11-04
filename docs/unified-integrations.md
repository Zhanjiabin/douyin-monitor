# 观潮统一工作空间

> 2026-11-04 界面整理：默认进入 `/home` 账号卡片工作台；日常使用 `/screen`、`/research`、`/collect`、`/assets`、`/settings`。独立 AI 工作室与旧采集入口收纳到设置的高级工具；`/workspace#remix`、`#vox` 等原入口跳转到新研究页。下表保留底层能力与高级入口说明。服务器迁移条件见 [部署评估](server-deployment.md)。


源码入口：`scripts/start_stack.py`；Windows 双击根目录 `启动.bat`。主页面是 `http://127.0.0.1:8795/home`，其余功能共用侧栏。启动器会复用已健康运行的服务；关闭主窗口后，独立引擎仍可在“引擎管理”中停止。

## 接入范围

MediaCrawlerPro 的 7 个功能项目均保留完整源码与原有功能入口，`.github` 作为资料归档，按要求不拉取 Go 项目。源码版本见 `integrations/mcpro.lock.json`。用途、桥接方式、依赖及验证边界见 `outputs/01a1a206-0159-7d73-836a-2c0d96ac52d4/项目接入清单.xlsx`。

| 项目 | 入口 | 接入内容 |
| --- | --- | --- |
| MediaCrawlerPro-Python | `/workspace#crawl` | 8 平台、搜索/详情/主页/推荐/热榜、评论及回复、断点；统一任务与内容库 |
| MediaCrawlerPro-SignSrv | `/workspace#engines` | 8989，本机签名依赖；Windows 使用 Node 实际路径及 stdin 传入组合签名参数 |
| MediaCrawlerPro-Downloader | `/workspace#parse` | 4 平台作品解析、账号资料、分页作品、视频/图集下载、断点文件、B站 DASH 音视频合并 |
| MediaCrawlerPro-CookieBridge | `/workspace#engines` → 平台登录 | 8274，保留完整浏览器扩展与服务，账号池共享；现有抖音登录可同步，其他平台可加载扩展或填写 Cookie |
| ContentRemixAgent | `/workspace#remix` | 完整原生工作室、分析/对话/文案/转写/模式/模型配置；可直接选择监控作品带入；共用下载解析服务 |
| PicTacticAgent | `/workspace#pictactic` | 完整原生工作室、199 条模板、服务商、生成/编辑/迭代/历史及上游视频功能 |
| VoxAgent | `/workspace#vox` | 完整原生研究工作室、研究历史、规划/执行/标注/证据/报告/断点；与采集器共用源数据库 |
| TikHub | `/tikhub`、`/workspace#playback` | [1,058 个接口、51 组](tikhub-api.md)，动态表单、JSON 请求体、批量 / 分页、完整响应与导出；Key 与限额共用，支持播放、作品与评论回写 |
| 现有 DouK | `/studio` | 原有 26 个抖音 / TikTok 入口、下载、去重、导出及监控导入继续保留 |

三个原生工作室以本机独立服务运行，通过统一导航、自动本机会话、紧凑样式与作品选择桥接接入。模型服务商、原有生成参数与高级功能保留在各自工作室内。

## 数据与口径

- 主监控使用 `data/douyin.db`。新增 `content_library`、`content_metrics`、`content_comments` 用平台 + 原始 ID 区分内容。
- MediaCrawlerPro 与 Vox 共用 `data/mcpro/crawler.db`。支持采集协议的平台按任务 ID 导入本次实际观测，其余平台按本次更新时间导入。上游作者匿名化保留；只用精确作品 ID 关联已知抖音作者，不根据昵称猜测账号归属。
- 下载、分析产物、图片及评论报告在 `/workspace#assets` 汇总。只列出明确的素材目录和文档/媒体扩展名，配置和凭据不进入素材列表。
- 播放量为 0 或缺失均不作为有效播放量覆盖已知值。MediaCrawlerPro 的当前抖音模型没有播放量字段；集成该采集器不能凭空补齐播放。
- TikHub 单条 App 统计标为“自然播放（供应商口径）”；星图为“含投流总播放”；批量接口的投流范围未注明，单独标识。不跨来源或口径计算增长。预览不访问 TikHub；执行前确认，网络失败不自动重试，重复任务须重新预览。
- 2026-11-04 已配置本机 TikHub Key，实测单视频统计和批量统计：共 5 次 HTTP 200 请求补齐原来缺失的 190 条播放量，当前监控库 644/644 条有有效播放。其余付费接口尚未逐个实测。模型分析/生成仍需要用户自己的服务商配置；统计接口验证不代表模型调用已通过。

## 本机配置与安装

当前 Windows 工作区已装好运行环境、3 个前端构建与本机 MariaDB。新环境按顺序执行（需要已授权的 GitHub CLI 账号）：

```powershell
python -m pip install -r requirements.txt
python scripts/sync_mcpro.py
python scripts/setup_mcpro.py
python scripts/setup_mcpro_web.py
python scripts/setup_mcpro_database.py
python scripts/setup_douk.py
python scripts/start_stack.py
```

MediaCrawlerPro Python 环境隔离在各项目 `.venv`，使用 Python 3.12；Node 需 22 或更新。主应用使用根目录 `.venv`。MariaDB 是项目内可移植版本，不安装系统服务，绑定 127.0.0.1:13306。

| 服务 | 端口 |
| --- | --- |
| 观潮 | 8795 |
| MariaDB | 13306 |
| 签名 / 登录同步 / 下载解析 | 8989 / 8274 / 8205 |
| 内容分析 API / 页面 | 8001 / 5373 |
| 图片 API / 页面 | 8019 / 3019 |
| 评论 API / 页面代理 | 4318 / 4319 |

GitHub 凭据、数据库密码、工作室 JWT、本机 Cookie 和 TikHub Key 位于被忽略的 `data/` 下，不回显到页面或任务导出。私有订阅源码整个 `vendor/MediaCrawlerPro/` 已被忽略；各项目原许可证和版权声明保留。

## 验证

```powershell
python -m unittest discover -s tests
python scripts/check_screen.py
python scripts/check_studio.py
python scripts/check_workspace.py
```

前两个浏览器回归使用隔离测试库；最后一个验收已运行的本机服务，检查统一导航、登录、跨工作室作品带入、手机布局与未配置 Key 时的离线预览，不执行付费查询。真实详情和 Downloader 解析已用作品 `7703151458754472787` 验证。其他平台需要相应登录，模型输出和 TikHub 付费结果不在无 Key 验收范围内。

最终实测：同一作品成功采集详情与精确 2 条评论；Downloader 下载文件 96,469,548 字节，经 ffprobe 核验时长 390.047 秒，并通过 HTTP Range 范围播放测试。本机 TUN 代理的 Fake-IP CDN 需经独立公开 DNS 验证后使用，内网地址仍拒绝。
