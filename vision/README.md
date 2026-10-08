# 可选本地 CLIP 视觉服务

这是文字／图片搜索和后续 AI 场景标签的独立模型服务。主程序默认不启动、不安装其依赖；当前提交提供模型与 HTTP 协议，视频抽帧索引及搜索界面的接入继续单独实施。

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

## HTTP 协议 v1

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
