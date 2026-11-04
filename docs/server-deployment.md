# 服务器部署

2026-11-04 已将核心应用部署到 Debian 12 服务器。访问地址：<http://110.42.70.142:8101>，登录账号 `nuannuan`。密码按用户指定设置，本文不保存明文密码。

## 运行结构

- Nginx 监听公网 `8101`，代理到 `127.0.0.1:18795`。
- `douyin-monitor.service` 使用独立系统用户，开机启动、异常自动重启。
- 安装目录 `/opt/douyin-monitor`，Python 环境 `.venv`，业务库 `data/douyin.db`。
- `deploy/server.env` 指定登录配置、公开 Origin 和可信本机代理。应用继续仅监听回环地址。
- Linux 允许地址复用以支持快速重启；Windows 继续禁用地址复用，避免两个实例抢占端口。

## 登录和私有配置

使用正常的 `/login` 页面，没有 HTTP Basic 认证或浏览器认证弹窗。所有业务页面、API、素材及导出均经过会话校验。登录样式、脚本和不包含用户身份的会话状态接口可匿名访问。

`data/auth/settings.json` 保存账号和 scrypt 密码哈希；会话在 `data/auth/sessions.db` 保存随机令牌的哈希。Cookie 使用 HttpOnly 和 SameSite=Lax。退出、密码变更和到期会使旧会话失效。登录失败有限流，跨站写入被拒绝。

TikHub Key 保存在 `data/playback/settings.json`。原有平台配置一并迁移，采用私有文件权限，不经过 Nginx 静态文件映射，不写入 Git。业务 API 只返回密钥是否配置，不返回密钥正文。

当前入口是 HTTP。以后配置 HTTPS 时，应同步修改 `DOUYIN_PUBLIC_ORIGIN`，Cookie 会自动带上 Secure。

## 数据迁移

使用 SQLite 一致性快照迁移了 5 个账号、644 条作品、播放历史、内容和评论、任务记录、732 个本机图片缓存、4 项已有素材及接口配置。迁移时校验了 3,084 个文件，哈希全部匹配。同一视频在两个素材目录采用硬链接去重，保留两个访问路径。

Windows 的 MariaDB 业务库已导出到 `data/mcpro/server-migration/observatory_remix.sql`。独立工作室的源码、配置和已有数据保留；其数据库服务和应用运行环境尚未在此服务器启动。Windows 的数据库二进制、虚拟环境和文档构建依赖不作为 Linux 运行环境。

服务器与本机后续各自持久化数据；正式使用请访问服务器地址。本次没有建立双向实时同步。

## 功能与空间限制

网页登录、工作台、账号管理、作品监控、内容研究、采集中心、设置、已有素材 Range 下载、单作品刷新预览及退出后的访问拦截已通过原版本验收。本次增加发现、公开趋势、粉丝画像、样本比较及定时计划；TikHub 限额、批量接口默认关闭和原有监控设置保持不变。

原生视频解析下载所需的签名、CookieBridge、Downloader 已安装 Python 3.12 隔离环境，并由 `douyin-native@sign`、`douyin-native@cookie`、`douyin-native@downloader` 三个 systemd 服务开机启动。服务仅监听回环地址 8989/8274/8205，不开放公网端口；复用此前已同步的普通平台登录，不索取创作者后台 Cookie。新下载仍受平台登录有效期、平台限制和内容可见性影响。浏览器采集和独立 AI 工作室未在服务器启用。

MediaCrawlerPro-Python 的搜索与热搜运行环境也已安装，按任务启动独立进程，无需长期占用额外公网端口。首次真实采集返回 AI 成片 29、教程 50、趣味 27、漫剧 31 条结果，以及 50 条实时热榜；不同方向之间可能存在重复视频，作品库按 ID 去重。这轮采集前后 TikHub 当日调用数均为 14，未产生新增 TikHub 请求。上述数字仅是首次观测，不保证以后每轮相同。

2026-11-04 运行环境安装时磁盘约有 12GB 可用；随后发布前复核，磁盘已扩到约 64GB，可用约 26GB。磁盘容量变化来自本次工作以外的服务器调整。旧部署的低空间提示已过时。此前用户批准过清理旧归档系统日志；实际清理越过日期边界的问题已单独披露，补存的传统日志副本位于 `/var/log/journal-recovery/`。本次没有继续删除系统日志或其他应用数据。

如需在新 Linux 服务器安装原生下载能力，先按授权方式取得 MediaCrawlerPro 私有源码到 `vendor/MediaCrawlerPro`，再运行 `bash scripts/setup_server_native.sh`。此脚本只准备本项目隔离运行环境；不发布私有源码、配置或 Cookie。安装 `deploy/douyin-native@.service` 后启用以上三个实例。Downloader 的 systemd 依赖应包含 sign 与 cookie 两个实例。

本次线上验收使用两次单页发现请求：低粉爆款返回 19 条有效作品，上升热点返回 20 条，均成功入缓存并可导出；未调用批量视频统计。临时计划的保存、启用、暂停、删除正常，验收计划已删除，没有留下收费定时任务。原参考作品的原生解析下载任务成功，已有文件复用，素材 Range 下载通过。登录后的真实发现页、详情返回、工作台账号、公开画像入口、默认关闭的关键词许可及移动布局通过浏览器验收，未出现 JavaScript 错误。

## 备份与维护

`douyin-monitor-backup.timer` 每天北京时间 03:30 备份 SQLite 快照、任务记录和私有配置，保留最近 7 份，存放于 `data/server-backups`。已有大体积素材不重复装入每日元数据备份。首次备份已成功。

```sh
systemctl status douyin-monitor
systemctl restart douyin-monitor
systemctl status 'douyin-native@*'
systemctl list-timers douyin-monitor-backup.timer
systemctl start douyin-monitor-backup.service
tail -n 80 /opt/douyin-monitor/logs/server.log
nginx -t
```

项目日志通过独立 logrotate 配置轮转。恢复时先停止本项目服务，保存当前库，解压目标备份，确认私有文件所有者为 `douyin-monitor` 后启动。重新部署代码时，不覆盖服务器上的实时业务库和私有配置。
