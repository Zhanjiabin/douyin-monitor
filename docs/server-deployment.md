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

已验证网页登录、工作台、账号管理、作品监控、内容研究、采集中心、设置、已有素材 Range 下载、单作品刷新预览及退出后的访问拦截。TikHub 限额、批量接口默认关闭和原有定时监控设置保持不变。部署验收没有新增付费 API 请求。

新的原生视频解析下载依赖 MediaCrawlerPro 的签名、CookieBridge、Downloader 服务及 Linux/Python 3.12 环境，当前尚未启动；浏览器采集和独立 AI 工作室也未启用。已有素材下载可用。下载预览会提示空间或环境不足，避免创建必然失败的任务。

部署后磁盘只剩约 55MB。14 天以前的归档系统日志约占 462MB，清理许可正在等待用户确认，未删除系统日志。继续安装下载引擎或保存更多视频前，需要释放空间或扩容。

## 备份与维护

`douyin-monitor-backup.timer` 每天北京时间 03:30 备份 SQLite 快照、任务记录和私有配置，保留最近 7 份，存放于 `data/server-backups`。已有大体积素材不重复装入每日元数据备份。首次备份已成功。

```sh
systemctl status douyin-monitor
systemctl restart douyin-monitor
systemctl list-timers douyin-monitor-backup.timer
systemctl start douyin-monitor-backup.service
tail -n 80 /opt/douyin-monitor/logs/server.log
nginx -t
```

项目日志通过独立 logrotate 配置轮转。恢复时先停止本项目服务，保存当前库，解压目标备份，确认私有文件所有者为 `douyin-monitor` 后启动。重新部署代码时，不覆盖服务器上的实时业务库和私有配置。
