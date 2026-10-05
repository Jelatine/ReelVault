# ReelVault

自托管的视频库：在线播放、后台编辑、视频文件管理。基于 FastAPI + ffmpeg + React，单进程单端口，可直接部署在 Ubuntu，也可用 Docker 运行。

[![CI](https://github.com/Jelatine/ReelVault/actions/workflows/ci.yml/badge.svg)](https://github.com/Jelatine/ReelVault/actions/workflows/ci.yml)

## 功能

**播放**
- 浏览器在线播放，支持 HTTP Range 拖动、倍速、全屏、键盘快捷键
- 进度条悬停缩略图（雪碧图 + WebVTT）
- 视频库卡片悬停自动播放预览片段
- MKV / AVI / HEVC 等浏览器不支持的格式自动生成可播放副本（能无损封装时只做封装）

**编辑**（ffmpeg 后台任务，实时进度，可取消）
- 剪辑：保留一个或多个片段并拼接，支持「精确（重编码）」和「快速（无损，按关键帧）」
- 旋转 90° / 180° / 270°、水平/垂直翻转，播放器内实时预览
- 合并多个视频：格式一致时无损合并，否则自动统一分辨率/帧率/音频后重编码
- 压缩：H.264 / H.265、画质档位、目标分辨率、帧率上限，或按目标文件大小两遍编码；显示预估大小
- 封面：以任意时间点画面作为封面、上传图片作为封面、把封面写入 MP4 文件
- 裁切画面（预设比例）、变速、静音、提取音频（MP3/M4A）、格式转换（MP4/WebM/MKV）、任意时刻截图
- 结果可另存为新视频，或替换原视频（原文件进入回收站，可恢复）

**文件管理**
- 分片断点续传上传（拖拽到页面即可），大文件友好
- 多级文件夹、标签、搜索、排序、网格/列表视图
- 批量移动 / 打标签 / 合并 / 删除，回收站
- 扫描导入服务器上已有的视频目录

**账号与登录**
- 单管理员账号；首次访问在浏览器中创建，或用环境变量预置
- 同一账号可在多台设备同时登录，设置页可查看/重命名/注销任一设备
- 「记住我」：30 天免密登录，使用中自动续期，令牌定期轮换（数据库只存哈希）
- 登录失败限流、HttpOnly + SameSite Cookie、CSRF 防护

## 快速开始

### Docker

```bash
docker run -d --name reelvault --restart unless-stopped \
  -p 8080:8080 -v $PWD/data:/data \
  ghcr.io/jelatine/reelvault:latest
```

或使用仓库中的 `docker-compose.yml`：

```bash
docker compose up -d
```

打开 `http://服务器IP:8080`，首次访问创建管理员账号。

### Ubuntu (22.04 / 24.04)

从 [Releases](https://github.com/Jelatine/ReelVault/releases) 下载发布包：

```bash
VERSION=0.1.0
curl -LO https://github.com/Jelatine/ReelVault/releases/download/v$VERSION/reelvault-$VERSION.tar.gz
tar xzf reelvault-$VERSION.tar.gz && cd reelvault-$VERSION
sudo ./deploy/install.sh
```

安装脚本会安装 ffmpeg 与 [uv](https://github.com/astral-sh/uv)，创建 `reelvault` 系统用户，把程序装到 `/opt/reelvault`，数据放在 `/var/lib/reelvault`，并注册 systemd 服务。再次运行同一脚本即可升级（配置与数据保留）。

```bash
sudo systemctl status reelvault
journalctl -u reelvault -f
sudo nano /etc/reelvault/reelvault.env && sudo systemctl restart reelvault
```

公网访问建议放在 HTTPS 反向代理之后，参考 [`deploy/nginx.conf.example`](deploy/nginx.conf.example)，并设置 `REELVAULT_SECURE_COOKIES=true`。

## 配置

所有配置通过环境变量（或 `.env` 文件）设置：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `REELVAULT_DATA_DIR` | `./data`（Docker 为 `/data`） | 数据目录：视频、缩略图、数据库 |
| `REELVAULT_HOST` / `REELVAULT_PORT` | `0.0.0.0` / `8080` | 监听地址 |
| `REELVAULT_ADMIN_USER` / `REELVAULT_ADMIN_PASSWORD` | 空 | 预置管理员账号（仅在尚无账号时生效） |
| `REELVAULT_WORKERS` | `2` | 同时运行的 ffmpeg 任务数 |
| `REELVAULT_REMEMBER_DAYS` | `30` | 「记住我」有效天数（滑动续期） |
| `REELVAULT_SESSION_IDLE_HOURS` | `12` | 未勾选「记住我」时的闲置过期时间 |
| `REELVAULT_TOKEN_ROTATE_DAYS` | `7` | 长期令牌轮换周期 |
| `REELVAULT_SECURE_COOKIES` | `false` | HTTPS 部署时设为 `true` |
| `REELVAULT_LOGIN_MAX_FAILURES` / `REELVAULT_LOGIN_LOCK_MINUTES` | `5` / `5` | 登录失败锁定策略 |
| `REELVAULT_IMPORT_DIR` | 空 | 可在设置页扫描导入的视频目录 |
| `REELVAULT_FFMPEG` / `REELVAULT_FFPROBE` | `ffmpeg` / `ffprobe` | ffmpeg 可执行文件路径 |

数据目录结构：

```
data/
├── reelvault.db        # SQLite 数据库
├── library/            # 视频文件
├── derived/<id>/       # 封面、预览片段、缩略图雪碧图、可播放副本
├── exports/            # 提取的音频等导出文件
└── tmp/                # 分片上传与任务临时文件
```

## 开发

需要 Python 3.12（由 uv 自动管理）、Node.js 22+、ffmpeg。

```bash
make install   # 安装前后端依赖
make dev       # 后端 :8080 + Vite 开发服务器 :5173（代理 /api）
make test      # pytest + vitest
make lint      # ruff、mypy、oxlint、tsc
make build     # 构建前端并拷贝到 backend/reelvault/static
make package   # 生成 Ubuntu 发布包 dist/reelvault-<version>.tar.gz
make docker    # 构建 Docker 镜像
```

项目结构：

```
backend/reelvault/
├── api/            # REST 接口：auth、videos、folders、jobs、system
├── media/          # ffprobe 解析、派生资源生成、编辑操作的 ffmpeg 命令
├── jobs/           # 后台任务队列与处理器（进度通过 SSE 推送）
├── migrations/     # Alembic 数据库迁移（启动时自动执行）
└── auth.py         # 密码哈希、多设备会话、记住我、令牌轮换
frontend/src/
├── pages/          # 视频库、播放/编辑、任务中心、回收站、设置
├── editor/         # 剪辑、旋转、合并、压缩、封面等编辑面板
└── lib/            # API 客户端、分片上传、任务事件
```

## 发布

更新 `backend/reelvault/__init__.py` 与 `backend/pyproject.toml` 中的版本号，然后推送 tag：

```bash
git tag v0.1.0 && git push origin v0.1.0
```

GitHub Actions 会构建 `linux/amd64`、`linux/arm64` 镜像并推送到 `ghcr.io/jelatine/reelvault`，同时创建 Release 并附带 Ubuntu 安装包与 SHA256 校验文件。

## License

MIT
