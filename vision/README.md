# 可选本地 CLIP 视觉服务

这是文字／图片搜索和后续 AI 场景标签的独立模型服务。主程序默认不启动、不安装其模型依赖；启用后可在视频详情生成画面索引，在侧栏以文字或图片搜索并跳转播放。场景标签与人脸聚类仍待实施。

图片使用 `sentence-transformers/clip-ViT-B-32`，文字使用与其对齐的 `clip-ViT-B-32-multilingual-v1`，支持简体中文、繁体中文及英文。输出为归一化的 512 维向量；响应携带模型版本标识，接入方必须拒绝版本不一致的向量。固定模型提交与依赖锁文件随代码维护，不执行模型仓库中的自定义代码，只准备 safetensors 权重。

## 源码部署

需要 Python 3.11–3.13、uv，预留至少 3 GiB 内存与约 3 GiB 安装／模型空间。Linux 安装 CPU 版 PyTorch。首次准备需要访问公开的 Hugging Face 模型仓库，正常启动与推理完全离线，不发送媒体到公网。

```bash
cd vision
uv sync --frozen --no-dev
# 唯一允许下载模型的显式操作；以实际服务用户运行。
uv run --frozen reelvault-vision --cache ./models --prepare
# 请用密码管理器生成并保存至少 32 字符的随机令牌。
export REELVAULT_VISION_TOKEN='替换为32到256字符的随机ASCII令牌'
uv run --frozen reelvault-vision --cache ./models
```

默认只监听 `127.0.0.1:8091`，CPU 使用 2 个线程；可用 `--threads 1` 降低负载。模型缺失或令牌不符合要求会启动失败。不要设置多个 Uvicorn worker，它们会各自复制两个模型。服务同时只执行一次推理；忙时返回 503 和 `Retry-After: 1`，健康检查仍可响应，调用方应在任务取消／暂停检查后有限重试。

## 独立容器

```bash
cd vision
export REELVAULT_VISION_TOKEN='替换为32到256字符的随机ASCII令牌'
docker compose build
# 初次执行：模型卷属于容器中的非 root 服务用户。
docker compose run --rm vision --prepare
docker compose up -d
```

容器仅向主机回环地址暴露端口，模型放在独立持久卷中；主程序容器接入时需要配置私有容器网络。该 Compose 文件独立于主程序的默认启动文件，限制 2 个 CPU 和 3 GiB 内存。不要将端口直接公开；跨主机部署请使用私有网络与 HTTPS，令牌须与调用方一致。

视觉服务独立升级：获取新版发布包中的 `vision/`，重新同步锁定依赖或重建容器，再以相同令牌启动。主程序的一键升级不会自动重启或升级此服务。模型提交改变时需显式准备新模型并重建对应的视频向量索引。

## 主程序接入

在主程序的环境文件或容器环境中设置以下变量，随后重启主程序：

```dotenv
REELVAULT_VISION_ENABLED=true
REELVAULT_VISION_URL=http://127.0.0.1:8091
REELVAULT_VISION_TOKEN=与模型服务一致的32到256字符随机ASCII令牌
REELVAULT_VISION_MAX_FRAMES=240
```

主程序运行在容器中时，其回环地址属于自身容器。应将两个容器接入同一私有 Docker 网络，模型服务的网络别名设为 `vision`，主程序使用 `REELVAULT_VISION_URL=http://vision:8091`；模型服务需监听容器内 `0.0.0.0`，无需公开端口。令牌只通过服务器端请求传递，不向浏览器或元数据备份暴露；恢复备份需重新配置这些变量。

视频详情页可设置抽帧间隔（1–3600 秒）和帧数上限（受部署配置限制），生成、重新生成或移除索引。任务使用现有工作槽，不同视频可并行，同一视频串行；模型服务单次推理的限制仍然有效。暂停会停止抽帧进程，取消会终止进程并清理临时文件；服务中已经开始的单张图片推理会完成。失败或取消重建保留旧索引。

全库搜索每个视频返回最相似的一帧，限定视频搜索返回其全部匹配帧并分页。查询图片最多 10 MiB、4096×4096，主程序将其规范化为单张 512 像素 JPEG 后调用模型，查询结束即清理临时文件。向量存储在普通 SQLite 表中，可随元数据备份恢复；搜索校验模型标识、源文件签名和视频状态。缩略图缺失时重新生成索引即可恢复，原视频不会被改写。

## HTTP 协议 v1

### 可选人脸模型

人脸特征服务是 AI 场景标签／人脸聚类的基础；主程序分组及人工纠正界面继续实施，相关 TODO 尚未完成。普通 CLIP 部署不会安装 OpenCV、下载或加载人脸模型。源码部署显式执行：

```bash
uv sync --frozen --extra faces
uv run --frozen --extra faces reelvault-vision --cache ./models --prepare-faces
uv run --frozen --extra faces reelvault-vision --cache ./models --faces
```

容器部署使用可选覆盖文件：

```bash
docker compose -f compose.yml -f compose.faces.yml build
docker compose -f compose.yml -f compose.faces.yml run --rm vision --prepare
docker compose -f compose.yml -f compose.faces.yml run --rm vision --prepare-faces
docker compose -f compose.yml -f compose.faces.yml up -d
```

采用固定 OpenCV Zoo 提交的 YuNet 2023mar 检测模型和 SFace 2021dec 特征模型，只显式准备下载；每次准备与启动均校验固定 SHA256 和大小。CPU 推理与 CLIP 共用锁，不增加独立推理进程，健康检查保持可响应；同样执行图片输入大小、格式、EXIF 旋转和尺寸限制。一次最多返回 16 张脸，检测分数最低 0.85、裁剪可见尺寸至少 24 像素；通过五点对齐产生归一化 128 维特征。局部、模糊或较小的人脸可能遗漏，特征相似不保证是同一人，后续聚类需要用户复核。

启用后认证健康响应增加 `face_model` 和 `face_dimension`；`POST /embed/faces` 接收原始 PNG/JPEG/WebP 字节，返回 `faces`（每项含归一化 `box: [x,y,width,height]`、检测 `score` 和 `vector`）及模型标识。关闭时返回 403，未认证仍返回 401，繁忙时返回 503；不返回人物身份或推断属性。坐标对应 EXIF 校正后的图像。

YuNet 的 [MIT 许可](https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/LICENSE) 与 SFace 的 [Apache-2.0 许可](https://github.com/opencv/opencv_zoo/blob/main/models/face_recognition_sface/LICENSE) 随服务包及模型缓存保存，位于 `reelvault_vision/licenses/`。API 用法与模型方法参考 [OpenCV 官方教程](https://docs.opencv.org/4.10.0/d0/dd4/tutorial_dnn_face.html)。

真实人脸回归可设置 `REELVAULT_TEST_FACE_CACHE` 为已准备的模型缓存、`REELVAULT_TEST_FACE_IMAGE` 为包含人脸的测试图片路径，然后执行 `uv run --frozen --extra faces pytest -q`；测试不会自行下载模型或图片。

### CLIP 向量

所有端点需要 `Authorization: Bearer <令牌>`。无浏览器跨域支持，无文档公开端点。请求体至多 1 MiB；一次最多 16 条文字，每条 1–512 字符；图片仅支持静态 PNG/JPEG/WebP，尺寸不超过 1024×1024。

| 请求 | 输入 | 返回 |
| --- | --- | --- |
| `GET /health` | 无 | `protocol`、`model`、`dimension`、`ready` |
| `POST /embed/text` | JSON：`{"texts":["海边的日落"]}` | `protocol`、`model`、`dimension`、`vectors` |
| `POST /embed/image` | 原始图片字节；Content-Type 为图片类型 | 同上，`vectors` 包含一条向量 |

输入非法返回 400/422，超过请求体上限返回 413，类型不支持返回 415，令牌错误返回 401，模型忙返回 503。相似度为向量点积（余弦相似度），不是识别概率；模型近似结果需由用户查看视频确认。索引应保留源文件签名、实际帧时间及模型标识，以避免返回已替换视频的过期画面。

## 验证

```bash
uv sync --frozen
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest -q
# 已准备模型时，额外验证真实英文、简体中文、繁体中文与图片向量匹配。
REELVAULT_TEST_CLIP_CACHE="$PWD/models" uv run --frozen pytest -q
```

模型说明与许可：[多语言文字模型](https://huggingface.co/sentence-transformers/clip-ViT-B-32-multilingual-v1)、[图片模型](https://huggingface.co/sentence-transformers/clip-ViT-B-32)、[原始 CLIP（MIT）](https://github.com/openai/CLIP)。多语言模型标注 Apache-2.0。部署者须保留模型及依赖的原许可声明。
