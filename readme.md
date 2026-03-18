# AI Notes 使用说明

这个仓库现在有两类能力：

1. 把课程视频/音频整理成普通笔记
2. 把课程视频/音频整理成适合 AI 知识库 / RAG 使用的资料包

如果你只是想要一份课程笔记，用 `make_note.py`。

如果你想要完整转写、知识点整理、RAG chunk、关键帧、总汇总文件，用 `build_knowledge_base.py`。

---

## 一、先搞清楚每个脚本是干什么的

### 1. `make_note.py`

普通版课程笔记脚本。

- 现在统一通过环境变量或仓库根目录 `.env` 读取阿里云配置
- 支持单个 URL
- 支持单个本地文件
- 支持整个文件夹批量生成笔记

### 2. `build_knowledge_base.py`

知识库构建脚本。

它会为每个视频生成一整套资料，而不只是一个笔记：

- 完整转写 `transcript_full.md`
- RAG 专用 chunk `rag_chunks.jsonl`
- 结构化知识整理 `knowledge_base.md`
- 单视频总汇总 `bundle_master.md`
- 单视频 RAG 汇总 `bundle_rag.md`
- 关键帧 / PPT 结果
- 全库总汇总 `library_master.md`
- 全库 RAG 汇总 `library_rag.md`
- 全库 JSONL `library_rag.jsonl`

默认情况下，脚本现在只保留“最终会直接用到”的文件。

如果你确实想保留原始 JSON 和中间文件，可以额外加：

```bash
--keep-intermediate-files
```

---

## 二、先配置环境变量

脚本开源前，最重要的一步就是不要把密钥写死在 Python 文件里。

现在仓库已经改成读取下面这些环境变量：

- `ALIYUN_ACCESS_KEY_ID`
- `ALIYUN_ACCESS_KEY_SECRET`
- `ALIYUN_TINGWU_APP_KEY`
- `ALIYUN_OSS_ENDPOINT`
- `ALIYUN_OSS_BUCKET_NAME`

最省事的做法：

```bash
cd /Users/macute/Downloads/AI_Notes
cp .env.example .env
```

然后打开 `.env`，把里面的占位值改成你自己的真实配置。

脚本会自动读取仓库根目录下的 `.env`，所以不需要每次都手动 `export`。

如果你更习惯用 shell 环境变量，也可以自己执行：

```bash
export ALIYUN_ACCESS_KEY_ID="你的 AccessKey ID"
export ALIYUN_ACCESS_KEY_SECRET="你的 AccessKey Secret"
export ALIYUN_TINGWU_APP_KEY="你的 AppKey"
export ALIYUN_OSS_ENDPOINT="oss-cn-hangzhou.aliyuncs.com"
export ALIYUN_OSS_BUCKET_NAME="你的 Bucket 名称"
```

---

## 三、安装依赖

先进入项目目录：

```bash
cd /Users/macute/Downloads/AI_Notes
```

安装依赖：

```bash
pip install -r requirements.txt
```

如果你已经安装过，也建议重新执行一次，因为知识库脚本现在依赖：

- `imageio`
- `imageio-ffmpeg`

它们用于在通义听悟没有返回关键帧时，本地从视频里兜底截图。

---

## 四、最常见的两种任务

## 任务 A：我要普通课程笔记

如果你只想要一份 Markdown 笔记，用 `make_note.py`。

### 1. 单个本地视频/音频生成笔记

```bash
python make_note.py \
  -l /Users/macute/Downloads/创业管理/创业管理_沈睿_2025_10_22第9_10节.mp4 \
  -f /Users/macute/Downloads/创业管理/notes/2025_10_22.md
```

这里要记住：

- `-l` 表示本地文件
- `-f` 表示输出到一个具体的笔记文件

### 2. 整个文件夹批量生成笔记

```bash
python make_note.py \
  -d /Users/macute/Downloads/创业管理 \
  -o /Users/macute/Downloads/创业管理/notes
```

这里要记住：

- `-d` 表示输入文件夹
- `-o` 表示输出目录
- 文件夹模式下不再用 `-f`

### 3. 如果有子文件夹，也一起处理

```bash
python make_note.py \
  -d /Users/macute/Downloads/创业管理 \
  -o /Users/macute/Downloads/创业管理/notes \
  --recursive
```

### 4. 文件处理顺序

批量模式会按文件名自然排序处理。

例如：

- `第2节` 会排在 `第10节` 前面
- `2025_09_15` 会排在 `2025_10_22` 前面

也就是说，脚本会按你看到的文件名顺序逐个上传、逐个生成，不会乱序。

---

## 任务 B：我要做 AI 知识库 / RAG 资料包

如果你想让视频变成后续可以喂给 AI 的资料，请用 `build_knowledge_base.py`。

### 1. 用单个视频测试

```bash
python build_knowledge_base.py \
  -l /Users/macute/Downloads/创业管理/创业管理_沈睿_2025_10_22第9_10节.mp4 \
  -o /Users/macute/Downloads/创业管理/knowledge_base_test
```

### 2. 用整个文件夹构建知识库

```bash
python build_knowledge_base.py \
  -d /Users/macute/Downloads/创业管理 \
  -o /Users/macute/Downloads/创业管理/knowledge_base
```

### 3. 先只跑前 1 个文件做测试

```bash
python build_knowledge_base.py \
  -d /Users/macute/Downloads/创业管理 \
  -o /Users/macute/Downloads/创业管理/knowledge_base \
  --limit 1
```

### 4. 如果目录里还有子文件夹

```bash
python build_knowledge_base.py \
  -d /Users/macute/Downloads/创业管理 \
  -o /Users/macute/Downloads/创业管理/knowledge_base \
  --recursive
```

### 5. 只想重建汇总文件和 RAG 文件，不想重新上传视频

如果你已经完整跑过一次了，后来只是想：

- 重建 `bundle_master.md`
- 重建 `library_master.md`
- 重建 `bundle_rag.md`
- 重建 `library_rag.md`
- 重建 `library_rag.jsonl`
- 重新补本地兜底关键帧

那么直接运行：

```bash
python build_knowledge_base.py \
  --rebuild-existing /Users/macute/Downloads/创业管理/knowledge_base
```

这个命令不会重新上传视频到云端。

---

## 五、`-f` 和 `-o` 到底怎么区分

这是最容易搞混的地方。

### `-f`

表示输出到“一个具体文件”。

只用于单文件模式。

示例：

```bash
python make_note.py -l input.mp4 -f output.md
```

### `-o`

表示输出到“一个目录”。

用于批量模式，或者知识库模式。

示例：

```bash
python make_note.py -d /path/to/videos -o /path/to/notes
```

```bash
python build_knowledge_base.py -d /path/to/videos -o /path/to/knowledge_base
```

一句话记住：

- 只产出 1 个文件时，用 `-f`
- 会产出很多文件时，用 `-o`

---

## 六、知识库输出目录长什么样

假设你运行了：

```bash
python build_knowledge_base.py \
  -d /Users/macute/Downloads/创业管理 \
  -o /Users/macute/Downloads/创业管理/knowledge_base
```

那么输出大致会像这样：

```text
knowledge_base/
├── library_master.md
├── library_rag.md
├── library_rag.jsonl
└── 创业管理_沈睿_2025_10_22第9_10节/
    ├── metadata.json
    ├── knowledge_base.md
    ├── bundle_master.md
    ├── bundle_rag.md
    ├── transcript_full.md
    ├── rag_chunks.jsonl
    ├── keyframes_manifest.json
    └── keyframes/
```

---

## 七、每个文件应该怎么理解

### 1. 普通阅读最方便

看这两个：

- `knowledge_base.md`
- `bundle_master.md`

适合人直接阅读。

### 2. 全课程总汇总

看这个：

- `library_master.md`

适合把整门课的内容放在一个总 Markdown 里看。

### 3. 更适合喂给 RAG

看这几个：

- `bundle_rag.md`
- `rag_chunks.jsonl`
- `library_rag.md`
- `library_rag.jsonl`

它们的特点是：

- 每个 chunk 都带时间范围
- 每个 chunk 都带章节标题
- 每个 chunk 都带关键词
- 更适合做检索、向量化、问答

如果你后面要接：

- OpenAI embeddings
- 向量数据库
- RAG 检索系统

优先使用 `rag_chunks.jsonl` 或 `library_rag.jsonl`。

### 4. 如果你想保留原始中间文件

默认不会保留这些中间产物：

- `raw/`
- `transcript_paragraphs.jsonl`
- `retrieval_chunks.jsonl`
- `library_index.json`
- `library_index.jsonl`

如果你确实需要调试或排查问题，可以运行时加上：

```bash
python build_knowledge_base.py \
  -d /Users/macute/Downloads/创业管理 \
  -o /Users/macute/Downloads/创业管理/knowledge_base \
  --keep-intermediate-files
```

---

## 八、关键帧为什么有时会是空的

知识库脚本会先尝试使用通义听悟自己的 `PptExtraction`。

但有些视频即使开启了这个功能，返回结果里也可能没有真正的 PPT 帧或 PDF。

现在脚本已经做了兜底：

1. 先尝试下载通义听悟返回的关键帧
2. 如果云端返回为空
3. 再根据章节时间点，从本地视频里自动截图

所以你现在看到的关键帧可能有两种来源：

- 云端提取
- 本地兜底截图

这个状态会写在：

- `keyframes_manifest.json`

如果里面的 `status` 是：

- `tingwu_success`：说明通义听悟直接给了关键帧
- `tingwu_empty`：说明通义听悟没给出有效关键帧
- `local_fallback`：说明已经改用本地截图补上了

---

## 九、推荐你现在直接怎么用

如果你当前主要处理的是 `创业管理` 这个目录，我建议这样：

### 方案 1：只要普通笔记

```bash
python make_note.py \
  -d /Users/macute/Downloads/创业管理 \
  -o /Users/macute/Downloads/创业管理/notes
```

### 方案 2：要完整知识库

```bash
python build_knowledge_base.py \
  -d /Users/macute/Downloads/创业管理 \
  -o /Users/macute/Downloads/创业管理/knowledge_base
```

### 方案 3：已经跑完过，只想补最新汇总 / RAG 文件

```bash
python build_knowledge_base.py \
  --rebuild-existing /Users/macute/Downloads/创业管理/knowledge_base
```

---

## 十、常见问题

### Q1：我到底该用哪个脚本？

普通笔记：

- `make_note.py`

知识库 / RAG：

- `build_knowledge_base.py`

### Q2：知识库脚本会不会重新上传视频？

会，除非你用的是：

```bash
--rebuild-existing
```

这个模式只会对已经生成好的目录重新整理，不会重新上传。

### Q3：知识库脚本用的是哪个配置？

知识库脚本和普通笔记脚本现在都读取同一组环境变量 / `.env` 配置。

所以如果知识库脚本报权限或配置错误，请先检查：

- `.env`
- 你当前 shell 里的 `ALIYUN_*` 环境变量

### Q4：为什么批量模式不用 `-f`？

因为批量模式会生成很多文件，必须给一个输出目录，所以用 `-o`。

---

## 十一、一个最简单的入门流程

如果你现在完全不想研究细节，就按下面三步走：

### 第一步：安装依赖

```bash
cd /Users/macute/Downloads/AI_Notes
pip install -r requirements.txt
```

### 第二步：构建知识库

```bash
python build_knowledge_base.py \
  -d /Users/macute/Downloads/创业管理 \
  -o /Users/macute/Downloads/创业管理/knowledge_base
```

### 第三步：看结果

先看这两个文件就够了：

- `/Users/macute/Downloads/创业管理/knowledge_base/library_master.md`
- `/Users/macute/Downloads/创业管理/knowledge_base/library_rag.md`

如果你后面要拿去做 RAG，再看：

- `/Users/macute/Downloads/创业管理/knowledge_base/library_rag.jsonl`

---

如果你愿意，下一步我也可以继续帮你补一个“最傻瓜式的一键命令清单”，专门只针对你当前的 `创业管理` 目录。  
