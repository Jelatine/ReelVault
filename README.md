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
- 浏览器在线播放，支持 HTTP Range 拖动、倍速、全屏、原生画中画、键盘快捷键
- A-B 区间循环、按账号记忆此浏览器的倍速/音量/静音与自动续播开关，文件夹与合集播放列表
- 进度条悬停缩略图（雪碧图 + WebVTT）
- 视频库卡片悬停自动播放预览片段
- 按用户保存播放位置、播放次数和最近播放时间，支持续播
- MKV / AVI / HEVC 等浏览器不支持的格式自动生成可播放副本（能无损封装时只做封装）

**编辑**（ffmpeg 后台任务，实时进度，可取消）
- 剪辑：保留一个或多个片段并拼接，支持「精确（重编码）」和「快速（无损，按关键帧）」
- 编辑器 `,` / `.` 按实际帧时间戳逐帧定位，显示帧号与毫秒时间码；快速剪辑显示并记录吸附到关键帧的实际切点
- 编辑预设：保存、应用、修改与删除常用参数；视频库多选后可批量应用，每个视频一个任务
- 旋转 90° / 180° / 270°、水平/垂直翻转，播放器内实时预览
- 合并多个视频：格式一致时无损合并，否则自动统一分辨率/帧率/音频后重编码
- 压缩：H.264 / H.265、画质档位、目标分辨率、帧率上限，或按目标文件大小两遍编码；显示预估大小
- 封面：以任意时间点画面作为封面、上传图片作为封面、把封面写入 MP4 文件
- 裁切画面（预设比例）、变速、静音、提取音频（MP3/M4A）、格式转换（MP4/WebM/MKV）、任意时刻截图
- 详情页展示编辑链、源视频与完整参数，可用相同参数另存重新生成；清空任务不影响编辑记录
- 剪辑与合并提交前可按当前片段顺序连续预览，无需渲染；变速支持播放器实时预览
- 结果可另存为新视频，或替换原视频（原文件进入回收站，可恢复）

**文件管理**
- 分片断点续传上传（拖拽到页面即可），大文件友好
- 合集可排序与连续播放，一个视频可加入多个合集
- Shift 连选、Ctrl/⌘ 多选、框选、Esc 取消；拖到侧栏文件夹移动
- 多级文件夹、标签、FTS5 全文搜索与结果高亮、排序、网格/列表视图
- 高级筛选：时长、分辨率/方向、编码、大小、上传/拍摄日期、格式、标签和所在文件夹（含子文件夹），条件同步到 URL
- 1～5 星评分与收藏，支持筛选与排序，筛选条件保存在 URL 中
- 批量移动 / 打标签 / 合并 / 删除，回收站
- 回收站默认保留 30 天，启动后及每小时清理过期视频；活跃任务使用的文件暂缓清理
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
brew install ffmpeg-full uv node git
```

`ffmpeg-full` 包含字幕烧录所需的 libass。若 Homebrew 提示尚未链接，可用 `brew link ffmpeg-full`；已链接旧版时先 `brew unlink ffmpeg`。也可设置 `REELVAULT_FFMPEG` / `REELVAULT_FFPROBE` 为其完整路径。

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
| `REELVAULT_TRASH_RETENTION_DAYS` | `30` | 回收站自动彻底删除的保留天数；`0` 禁用，修改后重启生效 |
| `REELVAULT_REMEMBER_DAYS` | `30` | 「记住我」有效天数（滑动续期） |
| `REELVAULT_SESSION_IDLE_HOURS` | `12` | 未勾选「记住我」时的闲置过期时间 |
| `REELVAULT_TOKEN_ROTATE_DAYS` | `7` | 长期令牌轮换周期 |
| `REELVAULT_SECURE_COOKIES` | `false` | HTTPS 部署时设为 `true` |
| `REELVAULT_LOGIN_MAX_FAILURES` / `REELVAULT_LOGIN_LOCK_MINUTES` | `5` / `5` | 登录失败锁定策略 |
| `REELVAULT_IMPORT_DIR` | 空 | 可在设置页扫描导入的视频目录 |
| `REELVAULT_CONFIG_FILE` | 未设置 | 从备份恢复的 JSON 配置文件；环境变量与 `.env` 优先 |
| `REELVAULT_FFMPEG` / `REELVAULT_FFPROBE` | `ffmpeg` / `ffprobe` | ffmpeg 可执行文件路径 |
| `REELVAULT_ENCODER` | `software` | 初始编码器：`software` / `auto` / `videotoolbox` / `qsv` / `vaapi` / `nvenc`；设置页保存的选择优先，重启后保留 |
| `REELVAULT_VAAPI_DEVICE` | `/dev/dri/renderD128` | VAAPI 渲染设备路径 |
| `REELVAULT_AUDIO_UPLOAD_MAX_MB` | `128` | 单个音频素材的上传上限（MiB） |
| `REELVAULT_UPDATE_CHECK` | `true` | 是否定期检查新版本 |
| `REELVAULT_UPDATE_CHECK_INTERVAL_HOURS` | `12` | 检查间隔 |
| `REELVAULT_UPDATE_INCLUDE_PRERELEASES` | `false` | 是否提示预发布版本（如 `-rc`） |
| `REELVAULT_ALLOW_SELF_UPDATE` | `true` | 是否允许在页面中一键升级（仅 Ubuntu 安装包部署） |
| `REELVAULT_GITHUB_TOKEN` | 空 | 可选，提高 GitHub API 调用限额 |
| `REELVAULT_UPDATE_REPO` / `REELVAULT_UPDATE_API_URL` | `Jelatine/ReelVault` / `https://api.github.com` | 版本来源（可指向 fork、镜像或 GitHub Enterprise） |
| `REELVAULT_INSTALL_MODE` | `auto` | 部署方式（`package`/`docker`/`source`），默认自动识别 |

### 硬件编码

在「设置 → 编码加速」选择软件编码、自动选择或指定硬件。启动时通过 `ffmpeg -encoders` 探测已编译的 H.264/H.265 编码器；这不保证设备与驱动可用，界面会展示最近任务的实际结果。自动模式依次尝试 VideoToolbox、QSV、VAAPI、NVENC，全部失败后从头使用软件编码；指定硬件失败时直接回退。H.264 回退到 libx264，H.265 回退到 libx265，任务详情显示实际编码器与回退原因。取消任务不会触发重试。

编辑输出、入库预览和兼容播放副本均使用所选编码器。硬件的质量参数只近似映射软件 CRF，画质与文件大小可能不同；目标大小压缩继续使用软件两遍编码，无损剪辑、封装转换等直接复制原流。编码器选项参考 [FFmpeg 编码器文档](https://ffmpeg.org/ffmpeg-codecs.html)。

容器部署还需要映射对应 GPU 设备并提供驱动；仅选择硬件不能让容器获得 GPU。VAAPI 需要可访问配置的渲染设备，NVENC 需要 NVIDIA 驱动及容器 GPU 支持。本项目已在 macOS 实机验证 VideoToolbox 的 H.264/H.265 输出及软件回退；QSV、VAAPI、NVENC 已有参数与回退测试，尚未在对应硬件上验证。

### 任务控制

「任务中心」支持低、普通、高三档优先级，按优先级及提交时间调度；仅尚未启动的任务可调整，不中断正在执行的任务。涉及相同源视频的任务串行处理，独立视频仍可并行，编辑提交提示已有未结束任务。

排队任务可暂停，继续后重新参与调度。运行中的任务在 macOS/Linux 上暂停当前 ffmpeg/ffprobe 进程，继续时保留进程及进度；暂停仍占用工作槽位并保护源文件，可直接取消。服务重启后，未启动的暂停任务保持暂停，运行中（含暂停）的中断任务标记失败；从备份恢复的未完成任务也标记失败，避免自动重复编辑。

失败任务可重试为新任务，保留原失败记录及优先级。已有未结束的重试时拒绝重复请求；源视频已删除或任务已经保存输出时拒绝重试。历史重放与回收站入库任务保留其读取已删除源视频的规则。编辑重试重新读取源视频，另存或替换行为沿用原参数。

剩余时间根据执行期间的进度速率估算，至少积累 3 秒及 1% 进度后显示，不包含排队等待；暂停/继续、进度回退后重新估算。多阶段处理和硬件回退可能导致估算变化。

### 音频处理

在视频详情的「更多 → 音频」中调整原声音量（-60～+24 dB）、响度标准化及淡入淡出，或上传 MP3/WAV/M4A/FLAC 等素材替换音轨、混入背景音乐。原声与素材可分别调音量，素材可延迟进入并循环；不循环时短音频结束后补静音，视频时长保持不变。没有原音轨的视频也可添加音乐。

画面流复制，音频输出 AAC 立体声 48 kHz；WebM 输入改为 MKV 封装以容纳 AAC。标准化在混音后通过单遍动态 `loudnorm` 应用，默认目标 -16 LUFS、真峰值 -1.5 dBTP，随后应用淡入淡出和峰值限制。淡入淡出会改变最终平均响度，增益过大时限制器可能降低音量；参考 [FFmpeg 音频滤镜文档](https://ffmpeg.org/ffmpeg-filters.html#loudnorm)。素材可单独试听，主播放器仍播放原视频。

素材持久保存在 `assets/`，可用于预设、批处理及编辑历史重放；被任务、历史（包括回收站视频）或预设引用时不能删除。编辑、重放及恢复时校验素材摘要，文件缺失或变化时拒绝使用。备份 ZIP 只保存素材元数据，完整备份必须另外复制 `assets/`。

数据目录结构：

```
data/
├── reelvault.db        # SQLite 数据库
├── library/            # 视频文件
├── derived/<id>/       # 封面、预览片段、缩略图雪碧图、可播放副本
├── exports/            # 提取的音频等导出文件
├── assets/             # 上传的音频等编辑素材
└── tmp/                # 分片上传与任务临时文件
```

### 字幕

在详情页「更多 → 字幕」上传 SRT、ASS 或 VTT（不超过 5 MiB），选择名称与语言后自动添加外挂轨道。播放器字幕按钮可开关字幕，设置菜单可切换语言；浏览器使用转换后的 WebVTT，ASS 的字体、位置和特效仅在烧录输出中保留。UTF-8 为默认编码，也可指定 UTF-16 或 GB18030/GBK。

MKV 内封文本字幕按轨道列出，支持浏览器显示和烧录；图像字幕只能烧录。烧录重新编码画面，输出 MP4，可选择 CRF 并使用编辑预设、批处理和历史重放。外挂字幕时间轴对应当前视频；剪辑或替换视频后需重新核对字幕时间。

FFmpeg 必须包含 `subtitles` / libass（用 `ffmpeg -hide_banner -h filter=subtitles` 检查）。Docker 与 Ubuntu 安装脚本安装 DejaVu 和 Noto CJK 字体；macOS 使用系统字体，缺少 ASS 指定字体时可能采用替代字体。字幕原文件保存在 `assets/` 并校验摘要，WebVTT 缓存可再生成。备份时复制整个 `assets/`；仍挂在视频上或被任务、预设、历史引用的素材不能删除。

### 水印与文字

在详情页「更多 → 水印」选择图片或文字叠加。图片支持静态 PNG/JPEG/WebP（最大 10 MiB、4096×4096），解码并保存为 PNG，保留透明通道。图片宽度按视频宽度百分比设置，保持比例并限制在画面内，不透明度与图片原有 alpha 相乘。

可选择四角、居中或自定义位置；自定义水平/垂直百分比基于剩余可用空间，0%/100% 对应两端。文字支持繁简中文、英文、换行，设置字号、颜色、描边和底框；内置 Noto Sans CJK 字体及其 [SIL OFL 许可证](backend/reelvault/resources/fonts/OFL.txt)，无需系统安装中文字体。浏览器按需加载同一字体，封面预览用于检查位置与比例，最终排版以 FFmpeg 输出为准。

水印永久叠加到输出画面，重新编码为 H.264/MP4，支持编码加速、编辑预设、批处理和历史重放。图片素材在 `assets/` 内保存并校验摘要，被任务、预设或编辑历史引用时不能删除；完整备份请复制 `assets/`。文字通过 UTF-8 文件交给 `drawtext`，百分号、引号与表达式均按原文字面显示。FFmpeg 需包含 `drawtext`、FreeType 和 HarfBuzz（`ffmpeg -hide_banner -h filter=drawtext` 检查），macOS 使用前述 `ffmpeg-full`。

### GIF / WebP 动图导出

在详情页「更多 → 动图」选取开始/结束时间，或把播放器当前位置设为片段端点。可预览原始片段，设置输出宽度（16–1920 像素）、帧率（1–60 fps）、循环播放和文件名称。输出保持显示比例，包括非方形像素的源视频；GIF/WebP 没有声音，生成后到任务中心下载。

GIF 先对整个片段运行 `palettegen` 生成调色板，再通过 `paletteuse` 生成动图，支持颜色数与抖色方式。GIF 帧间隔以百分之一秒记录，部分帧率会产生舍入。WebP 支持有损画质或无损编码，无损模式的 quality 参数调节压缩力度。长片段、高帧率和大尺寸会显著增加时间与文件体积。滤镜说明见 [FFmpeg 调色板文档](https://ffmpeg.org/ffmpeg-filters.html#palettegen)，WebP 参数见 [FFmpeg libwebp 文档](https://ffmpeg.org/ffmpeg-codecs.html#libwebp)。

参数可保存为预设并批量导出，每个源视频生成一个独立任务和下载文件；不替换原视频。批处理片段结束时间超过视频时长时裁到结尾，开始时间超出时长的任务失败。任务记录实际片段范围，支持失败重试；导出文件保存在 `exports/`，清空已完成任务时会删除对应文件。

### 合并转场

合并面板可选择交叉淡化、淡至黑/白、左右擦除或滑动、溶解、圆形展开。启用转场时重新编码，相邻片段按指定时长重叠；输出时长为所有片段总时长减去每处重叠。转场时长须短于每段视频，中间片段至少为转场时长的两倍，避免两处衔接超出同一片段。

画面统一尺寸、显示比例、帧率与时间基准；不同画面比例使用黑边填充，非方形像素按显示比例缩放。音轨统一为 48 kHz 立体声并交叉淡化，短音轨补静音，无音轨片段补静音；全部无音轨时保持无声。保留源音轨相对起始时间的延迟。渲染使用 [FFmpeg xfade](https://ffmpeg.org/ffmpeg-filters.html#xfade) 与 [acrossfade](https://ffmpeg.org/ffmpeg-filters.html#acrossfade)。

原始片段预览按顺序播放，不模拟转场和尺寸变化；实际转场效果在生成结果中查看。任务与编辑链保存转场类型和时长，可用相同参数重新生成。未开启转场时保留原有自动/无损/重新编码合并方式。

### 画面调整

在详情页「更多 → 画面调整」设置亮度（-1–1）、对比度与饱和度（0–3），默认值分别为 0、1、1。可上传 UTF-8 `.cube` 调色文件（最大 20 MiB）：支持单独的 1D 表（2–65536）或 3D 表（2–64），包括 DOMAIN_MIN/MAX 输入范围；不接受组合表。1D 使用三次插值，3D 使用四面体插值。上传时检查数据行数、有限数值和本机 FFmpeg 解码能力。

降噪强度 0–20，0 为关闭，使用 `hqdn3d` 时空降噪。防抖先由 `vidstabdetect` 分析完整视频，再用 `vidstabtransform` 平滑运动；可调整抖动强度、分析精度、平滑窗口和额外放大，默认自动放大以减少黑边。自动放大关闭后可能出现边缘黑区；移动物体、快速摇镜或纹理很少的画面可能降低防抖效果。

依次执行防抖、降噪、基础调色与 LUT，输出重新编码的 H.264/MP4，保留音轨和时长。播放器显示原视频，效果在生成结果中检查。参数支持预设、批处理、历史重放和公共硬件编码适配。LUT 永久保存在 `assets/` 并校验摘要，仍被引用时不能删除；完整备份须复制 `assets/`。

FFmpeg 需包含 `eq`、`lut1d`、`lut3d`、`hqdn3d`、`vidstabdetect` 和 `vidstabtransform`。macOS 使用前述 `ffmpeg-full`；其他自定义 FFmpeg 构建需启用 libvidstab。可用 `ffmpeg -hide_banner -h filter=vidstabdetect` 检查。滤镜参数见 [FFmpeg 文档](https://ffmpeg.org/ffmpeg-filters.html#vidstabtransform)。

### 片段级效果

在详情页「更多 → 片段效果」选择倒放、插入定格或局部慢动作。可输入时间码或用播放器当前位置设点；区间前后的内容保留。倒放同时反转选中区间内的画面和声音。定格在指定位置插入静止画面与等长静音，然后从原位置继续播放；定格时长为 0.04–3600 秒。局部慢动作支持 0.1–小于 1 倍速，声音保留音调，画面重复原帧，不生成运动插值帧。

时间按源帧率对齐，超出视频时长的区间结尾裁至结尾；短于一帧或起点超出视频的请求失败。输出统一为恒定帧率并记录实际区间与时长；定格时长和慢动作输出也按整帧对齐，误差通常不超过一帧。可选择 CRF、另存/替换、保存预设、批处理和编辑链重放；播放器显示原视频，效果在结果中检查。

使用 FFV1/PCM 无损临时文件再合并编码为 H.264/AAC MP4（音轨为 48 kHz 立体声），避免片段间重复有损编码。倒放分块并从后向前衔接，按画面尺寸和音频估算每块约 128 MiB 的原始帧缓存预算；解码、编码和滤镜另需内存。临时文件包含标准化源及分段结果，可能远大于原视频，长片段/高分辨率需充足临时磁盘空间，完成或取消后自动清理。参考 [FFmpeg reverse 文档](https://ffmpeg.org/ffmpeg-filters.html#reverse) 与 [atempo 文档](https://ffmpeg.org/ffmpeg-filters.html#atempo)。

### 画中画与分屏拼接

在详情页「更多 → 拼接」添加已就绪的视频并调整顺序。画中画使用两个输入，第一个为主画面；横排、竖排和网格支持 2–9 个输入，网格可设 1–3 列。当前视频须保留在输入中，结果另存为新视频。封面预览展示布局、大小、位置和透明度，播放器播放原视频；输出生成后可播放检查。

画布宽高须为偶数，默认 1280×720、30 fps，可设置 CRF、留边颜色及完整显示或填满裁切。按显示宽高比适配，支持非方形像素。画中画大小同时按画布宽高缩放（5%–80%），水平/垂直位置 0%–100% 表示剩余空间的两端，可设置不透明度。网格空格及偶数格子分配后的余边使用留边颜色。

输出时长可选第一个、最长或最短输入。每个输入从自身时间轴开头播放，保留音视频相对起始延迟；短画面延续末帧，短音轨补静音，长输入裁到输出结尾。声音可用任一输入音轨、混合全部有声输入或无声；混音按有声输入数平均，输出 AAC 48 kHz 立体声。选择无音轨输入时输出无声。

使用 overlay/xstack 重新编码为 H.264 MP4，接入公共硬件编码适配。任务与编辑链保存全部输入顺序、参数及实际布局，支持失败重试和历史重放。此操作涉及多个输入，不适用于每视频独立批处理与编辑预设。参考 [FFmpeg overlay 文档](https://ffmpeg.org/ffmpeg-filters.html#overlay) 与 [xstack 文档](https://ffmpeg.org/ffmpeg-filters.html#xstack)。

### 书签与章节

详情页「书签与章节」可输入毫秒时间码，或使用当前播放时间，保存标题、备注与标记类型。支持修改时间/标题/备注、删除和列表跳转；每个用户对每个视频最多保存 1000 个书签与手动章节，列表每页 20 个。标题最多 128 字符，备注最多 2000 字符。普通书签可设在结尾，章节必须在结尾之前。

播放器原有进度条上显示紫色圆形书签与青色方形章节，可点击或用 Tab 聚焦后按 Enter 跳转；悬停显示时间、标题和备注。场景检测的自动章节同样显示在进度条与播放器 Chapters 菜单中，手动章节名称优先于相同时间的自动章节；只有手动章节时，从开头到第一个章节之间显示「开头」。只添加普通书签不会生成章节。

书签与手动章节按用户隔离，保存于数据库（迁移 `0014`），数据库备份包含其时间和备注。源文件名称、大小或修改时间变化后旧书签保留在列表中，但不再提供跳转与进度条标记；调整时间并保存可重新用于当前视频。修改海报不会使个人书签失效。移至回收站后保留，恢复后继续使用；永久删除视频时级联删除书签。

### 场景检测与自动章节

在详情页「剪辑」展开「场景检测与自动章节」，设置变化阈值（0.01–1，默认 0.4）和最小间隔（0.05–600 秒，默认 1 秒），点击「检测镜头切换」。使用 FFmpeg `select=gt(scene,阈值)` 分析原视频；阈值越低越敏感，最小间隔过滤密集切点及过短的首尾章节。闪光、运动和渐变可能影响结果，需要人工检查；检测本身不改写视频。

结果显示切点时间与变化分数，并将从 0 到结尾的区间列为自动章节。可点击章节跳转，选择一个章节作为剪辑片段，将切点设为当前片段起点/终点，或按全部章节设置多段剪辑。超过 50 个章节时需逐个选择，以遵守剪辑任务的片段数量限制；列表每页显示 20 个章节。快速剪辑仍按关键帧吸附，精确剪辑使用检测时间点。

检测是后台任务，支持任务中心的暂停、继续、取消及失败重试；同一源视频与编辑任务串行。结果保存在数据库中（迁移 `0013`），清理任务记录不会删除章节，数据库备份包含检测结果。视频资源版本、源文件大小或修改时间变化后旧结果不再使用，需重新检测；排队或检测过程中发生变化时任务拒绝保存结果。只保存最近一次成功分析，取消/失败保留已有结果，超过 10000 个有效切点时要求提高阈值或最小间隔。参考 [FFmpeg select 文档](https://ffmpeg.org/ffmpeg-filters.html#select_002c-aselect)。

## 开发

需要 Python 3.12（由 uv 自动管理）、Node.js 22+、ffmpeg。macOS 下用 `brew install ffmpeg-full uv node` 安装；FFmpeg 须包含 libass/subtitles 滤镜。

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

浏览器端到端测试：先安装后端依赖（`cd backend && uv sync --frozen`），在 `frontend/` 执行 `npm ci`、`npx playwright install chromium`、`npm run build`、`npm run test:e2e`。需要本机 ffmpeg；测试自动启动端口 18089 的临时后端并使用独立数据库，退出后清理。配置遵循 [Playwright webServer](https://playwright.dev/docs/test-webserver)，CI 安装方式见 [Playwright CI](https://playwright.dev/docs/ci)。失败报告位于 `frontend/playwright-report/`，截图与 trace 位于 `frontend/test-results/`。


备份与恢复：设置页的「导出数据库与配置」生成 ZIP，包含 SQLite 一致性快照、当前配置和 SHA256 校验清单，支持数据库使用 WAL 时在线导出。实现采用 [SQLite backup API](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)。包内包含密码哈希、设备会话记录和配置凭据，请保存在受保护的位置；服务端备份文件权限为 0600。

完整备份时停止服务，另外复制数据目录的 `library/`、`derived/`、`exports/`、`assets/`，然后在后端 Python 环境中导出：

```sh
python -m reelvault.backup export --data-dir /path/data --output /path/reelvault-backup.zip
```

恢复时停止目标服务，先将上述媒体目录复制到目标数据目录（保持文件名和内部路径），再执行：

```sh
python -m reelvault.backup restore --archive /path/reelvault-backup.zip --data-dir /path/data
# 若目标已有数据库，确认替换后追加 --replace
REELVAULT_CONFIG_FILE=/path/data/restored-config.json python -m reelvault
```

恢复会校验 ZIP 内容、摘要、数据库完整性、迁移版本和媒体路径；缺少原视频或播放副本时拒绝应用。支持导入当前程序认识的旧数据库并迁移到当前版本，来自更新程序的未知版本会被拒绝。恢复会清除设备登录会话和未完成上传，将未完成任务标为失败；账号、整理信息与播放记录保留。`restored-config.json` 的库路径改为目标目录，前端目录重新探测；其余配置可通过环境变量或 `.env` 覆盖。Ubuntu 的 `/etc/reelvault/reelvault.env` 中可设置 `REELVAULT_CONFIG_FILE=/var/lib/reelvault/restored-config.json`，Docker 则将该变量设为容器内路径。

一键升级在替换程序前自动备份，启动时若数据库需要迁移也会先备份；恢复替换现有库前会保存安全备份，应用失败会回滚。自动备份位于数据目录的 `backups/`，不会自动删除，请定期复制到其他磁盘并按需清理。恢复命令和运行中的服务使用互斥库锁，恢复前必须停止服务。


编辑链从迁移 `0007` 起记录生成参数与源文件版本，合并保留全部输入顺序。替换原视频时，编辑前版本进入回收站并保留上游关系；重新生成可读取仍在回收站中的源文件，不恢复它的库可见性，结果始终另存。源版本已删除或不存在时会说明原因并禁用重新生成。封面写入会保留编辑前视频及使用的封面，封面变化后拒绝按原记录重放。旧版保留下来的成功任务可回填源 ID 与参数，但没有可靠源版本的旧记录只供查看；已清理的旧任务无法补回历史。


### 首页仪表盘

登录后首页集中显示继续观看、最近添加、最近编辑、收藏和存储概览，每组最多显示最近 8 项。继续观看按当前账号的最近播放时间排序，跳过未就绪、回收站、刚开始或接近结尾的视频；点击卡片会恢复保存的位置并尝试播放，浏览器拒绝自动播放时仍可点击播放器的继续按钮。首页每 30 秒刷新，也可手动刷新；收藏、评分和任务完成会更新首页。

最近编辑按独立的编辑时间排序，改名、评分或整理不会改变编辑时间；替换原视频会保存新时间，编辑前备份保留旧时间。旧库迁移优先使用保留的成功编辑任务时间，无法恢复精确时间时使用原更新时间/添加时间回填。

存储概览分别显示视频原文件、回收站原文件、HLS 缓存和所在磁盘剩余空间；磁盘使用率还包含缩略图、播放副本、临时文件、备份及其他应用，不将这些重复计入原文件分类。左侧「全部视频」和「打开视频库」进入 `/library`；旧 `/?folder=…`、`/?q=…` 等筛选链接自动跳转并保留参数。

### 播放增强

在视频库网格卡片或列表行上右键，可播放、重命名、移动、修改标签、评分、下载原文件或移到回收站；菜单操作针对右键点击的视频。文件夹右键可新建子目录、重命名和删除，删除文件夹仍会将其中视频与子目录移到上一级。也可先 Tab 聚焦卡片、列表行或文件夹，再按 Shift+F10 或菜单键打开菜单；方向键选择菜单项，Esc 关闭并返回原焦点。移动、标签和评分保存失败时保留输入，便于重试。

按 `/` 聚焦并选中搜索框内容，`U` 选择视频上传，`?` 打开快捷键说明（账号菜单中也有入口）。视频库内方向键移动卡片/列表行焦点，上下按实际网格列定位，左右按显示顺序；`Enter` 打开聚焦视频。`Delete` 删除选中视频，没有选择时删除聚焦视频；确认后移到回收站，取消或失败保留选择。输入框、可编辑区域、中文组合输入、菜单及对话框内不触发全局快捷键；浏览器 Ctrl/⌘/Alt 组合和播放器自己的方向键保留。`Esc` 在视频库取消选择，在菜单或对话框中仅关闭当前界面。

详情页「播放增强」可输入 A/B 时间码，或把当前位置设为端点。B 须比 A 至少晚 0.1 秒；开启后在播放时回到 A 重复区间，暂停时仍可自由定位。循环优先于自动续播，切换视频或替换源文件后清除区间；循环由浏览器播放事件驱动，定位精度受浏览器影响。播放器控制栏提供原生画中画，在支持该 API 的浏览器中可进入/退出。

播放器设置菜单的倍速、音量/静音以及「自动播放下一项」开关按账号保存在此浏览器，刷新或换视频后恢复，不随账号同步到其他设备；编辑器变速预览不改变常用倍速。清除浏览器存储后恢复默认值；浏览器拒绝写入存储时，仅在当前页面会话内生效。

「同文件夹播放列表」列出当前目录的全部就绪视频（不包括子文件夹或回收站），按添加时间及 ID 顺序播放；未分类视频也能组成列表。合集按人工排序播放。两种列表都支持搜索选项、上一项/下一项和结束后续播，最后一项停止；列表载入失败或当前视频已移出列表时停止续播。关闭续播开关仍可手动切换，浏览器的自动播放策略可能要求先进行一次点击播放。

### HLS 自适应播放

在「设置 → HLS 自适应码率」开启，默认关闭。打开详情页时，达到自动生成阈值的视频会加入任务队列；默认阈值 256 MiB。也可手动为较小视频生成。根据源分辨率生成不超过原尺寸的 360p/720p/1080p，小于 360p 的视频保留原高度；宽度最多 1920，保持显示比例。采用软件 H.264/AAC 编码、四秒对齐分片；任务支持暂停、继续与取消，不替换原视频。

生成完成且播放器尚未开始时自动使用 HLS；正在播放时可点击「使用自适应播放」，切换原始流与 HLS 时保留位置和播放/暂停状态。自动模式按带宽和播放器大小选择清晰度，也可在播放器「Settings → Quality」中指定档位；原生 HLS 浏览器的控制能力取决于浏览器支持。播放器库随视频页打包，无需访问外部 CDN。生成失败后可手动重试，清理缓存后需手动重新生成。

缓存位于 `derived/<视频 ID>/hls/`，默认总上限 20 GiB；设置页显示占用量，可清理全部或单个视频缓存。生成前及发布缓存前检查磁盘空间与缓存上限，并为排队任务预留估算容量；需要保留至少 64 MiB 余量。活跃任务未结束时拒绝清理，关闭 HLS 会取消生成任务并停止提供 HLS 资源，已有缓存保留。源路径、大小或修改时间变化会使缓存失效；重新生成成功后替换旧缓存，启动时清理未完成的孤立目录。设置保存到数据库，重启后生效。环境变量初始值为 `REELVAULT_HLS_ENABLED=false`、`REELVAULT_HLS_MIN_SIZE_MB=256`、`REELVAULT_HLS_MAX_CACHE_GB=20`。
