# ReelVault

自托管的视频库：在线播放、后台编辑、视频文件管理。基于 FastAPI + ffmpeg + React，单进程单端口，可直接部署在 Ubuntu，也可用 Docker 运行。

[![CI](https://github.com/Jelatine/ReelVault/actions/workflows/ci.yml/badge.svg)](https://github.com/Jelatine/ReelVault/actions/workflows/ci.yml)

![视频库](docs/screenshots/library.jpg)

## 界面预览

| 播放页：播放器、进度条缩略图、视频信息 | 多片段剪辑：时间轴上高亮保留的片段 |
| --- | --- |
| ![播放页](docs/screenshots/player.jpg) | ![剪辑](docs/screenshots/editor-trim.jpg) |
| **合并多个视频：调整顺序、自动统一分辨率** | **压缩：编码、分辨率、画质与预估大小** |
| ![合并](docs/screenshots/editor-merge.jpg) | ![压缩](docs/screenshots/editor-compress.jpg) |
| **任务中心：后台 ffmpeg 任务实时进度** | **浅色主题 + 列表视图** |
| ![任务中心](docs/screenshots/jobs.jpg) | ![浅色列表](docs/screenshots/library-light.jpg) |
| **多设备登录管理** | **登录页：记住我 30 天免密** |
| ![设备管理](docs/screenshots/devices.png) | ![登录](docs/screenshots/login.png) |

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

### macOS（源码编译运行）

适用于 Apple Silicon 与 Intel Mac。需要 [Homebrew](https://brew.sh)。

**1. 安装依赖**

```bash
brew install ffmpeg uv node git
```

uv 会自动下载项目所需的 Python 3.12，无需单独安装 Python。

**2. 获取源码并编译**

```bash
git clone https://github.com/Jelatine/ReelVault.git
cd ReelVault
make install   # 安装后端 (uv) 与前端 (npm) 依赖
make build     # 构建前端，输出到 backend/reelvault/static
```

**3. 运行**

```bash
cd backend
REELVAULT_DATA_DIR=~/ReelVault uv run reelvault
```

打开 <http://localhost:8080>，首次访问创建管理员账号。同一局域网内的手机、平板可通过 `http://<Mac 的 IP>:8080` 访问（首次运行时 macOS 可能弹出防火墙提示，选择「允许」）。

**4. 后台常驻（可选）**

用 launchd 让 ReelVault 随登录启动、崩溃自动重启：

```bash
./deploy/macos/install-launchd.sh            # 数据目录默认 ~/ReelVault
./deploy/macos/install-launchd.sh ~/Movies/ReelVault   # 或指定数据目录
tail -f ~/ReelVault/reelvault.log            # 查看日志
./deploy/macos/install-launchd.sh --uninstall  # 卸载
```

**升级**：`git pull && make install && make build`，然后重启服务（再次运行 `install-launchd.sh` 即可）。

> 也可以在 Docker Desktop 中直接使用上面的 Docker 方式运行，镜像同时提供 arm64 与 amd64。

公网访问建议放在 HTTPS 反向代理之后，参考 [`deploy/nginx.conf.example`](deploy/nginx.conf.example)，并设置 `REELVAULT_SECURE_COOKIES=true`。

## 版本检查与升级

ReelVault 会定期（默认每 12 小时）检查 [GitHub Releases](https://github.com/Jelatine/ReelVault/releases) 上的最新版本。有新版本时，页面右上角会出现提示，在「设置 → 版本与更新」中可以查看发布说明并升级：

| 部署方式 | 升级方式 |
| --- | --- |
| Ubuntu 安装包（`install.sh`） | **一键升级**：自动下载安装包、校验 SHA256、替换程序、安装依赖，然后自动重启；任何一步失败都会恢复到原版本。有任务正在运行时不允许升级 |
| Docker | 页面给出 `docker compose pull && docker compose up -d` 等命令 |
| 源码运行（含 macOS） | 页面给出 `git pull && make install && make build` 等步骤 |

一键升级依赖 systemd 的 `Restart=on-failure`（安装脚本已配置）：升级完成后程序以退出码 75 退出，由 systemd 拉起新版本。数据库迁移会在新版本启动时自动执行。

不希望服务器访问 GitHub 时，设置 `REELVAULT_UPDATE_CHECK=false` 关闭自动检查；`REELVAULT_ALLOW_SELF_UPDATE=false` 只保留检查、禁用一键升级。

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
| `REELVAULT_UPDATE_CHECK` | `true` | 是否定期检查新版本 |
| `REELVAULT_UPDATE_CHECK_INTERVAL_HOURS` | `12` | 检查间隔 |
| `REELVAULT_UPDATE_INCLUDE_PRERELEASES` | `false` | 是否提示预发布版本（如 `-rc`） |
| `REELVAULT_ALLOW_SELF_UPDATE` | `true` | 是否允许在页面中一键升级（仅 Ubuntu 安装包部署） |
| `REELVAULT_GITHUB_TOKEN` | 空 | 可选，提高 GitHub API 调用限额 |
| `REELVAULT_UPDATE_REPO` / `REELVAULT_UPDATE_API_URL` | `Jelatine/ReelVault` / `https://api.github.com` | 版本来源（可指向 fork、镜像或 GitHub Enterprise） |
| `REELVAULT_INSTALL_MODE` | `auto` | 部署方式（`package`/`docker`/`source`），默认自动识别 |

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

需要 Python 3.12（由 uv 自动管理）、Node.js 22+、ffmpeg。macOS 下用 `brew install ffmpeg uv node` 安装即可。

```bash
make install   # 安装前后端依赖
make dev       # 后端 :8080 + Vite 开发服务器 :5173（代理 /api，前端热更新）
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

## 路线图

后续计划（编辑能力、交互、评分/合集/全文搜索等）见 [TODO.md](TODO.md)。

## License

MIT
