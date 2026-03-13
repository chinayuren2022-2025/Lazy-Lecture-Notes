# 🎓 水课救星 (ShuiKe-Savior) | AI 自动课程笔记生成器

![Python Version](https://img.shields.io/badge/Python-3.8%2B-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![API](https://img.shields.io/badge/API-%E9%98%BF%E9%87%8C%E4%BA%91%E9%80%9A%E4%B9%89%E5%90%AC%E6%82%9F-orange.svg)

还在为冗长无聊、听得昏昏欲睡的“水课”发愁？快到期末了，看着空白的笔记本感到焦虑？

**ShuiKe-Savior** 是一款专为大学生打造的自动化笔记工具。它通过调用阿里云通义听悟的大模型能力，能够直接“看懂”和“听懂”你的网课，并为你自动生成结构清晰、带考点解析的 Markdown 级“学霸笔记”。

**你只管在课上休息或做自己的硬核项目，记笔记的脏活累活交给 AI！**

## ✨ 核心亮点

- 🚀 **一键“榨干”视频**：只需提供视频链接或本地音频文件，全自动提取课程大纲、详细要点和核心考点。
- 🧠 **内置“学霸” Prompt**：不是简单的语音转文字，而是按照大学期末复习逻辑，分级、分点整理，重点解释专业名词。
- 📁 **本地文件无缝支持**：网课录像存在本地电脑？内置自动上传 OSS -> 处理 -> 阅后即焚清理逻辑，不产生多余云端垃圾。
- 📝 **极佳的 Markdown 排版**：生成的笔记自带标题、列表和强调，可以直接扔进 Obsidian、Notion 或 Typora 里开始期末背诵。

## 🛠️ 准备工作

在运行脚本之前，你需要准备好以下阿里云的配置信息：
1. **AccessKey ID & Secret**：前往 [阿里云 RAM 控制台](https://ram.console.aliyun.com/manage/ak) 获取。
2. **通义听悟 AppKey**：前往 [通义听悟控制台](https://tingwu.console.aliyun.com/) 创建项目并获取。
3. **OSS Bucket（可选）**：如果你需要处理本地文件，请创建一个 OSS Bucket 并获取其 Endpoint 和 Bucket Name。

## 📦 安装与配置

1. 克隆本项目到本地：
   ```bas  git clone [https://github.com/YourUsername/ShuiKe-Savior.git](https://github.com/YourUsername/ShuiKe-Savior.git)
   cd ShuiKe-Savior

```


2. 安装依赖包：

```bash

pip install -r requirements.txt

```


3. 打开 `make_note.py`，在代码顶部填入你的阿里云配置信息：
```python
ACCESS_KEY_ID = "你的_AK_ID"
ACCESS_KEY_SECRET = "你的_AK_SECRET"
APP_KEY = "你的_通义听悟_APP_KEY"

# 如果要用本地文件模式，必须填写以下两项
OSS_ENDPOINT = "oss-cn-xxx.aliyuncs.com"
OSS_BUCKET_NAME = "你的_BUCKET_名称"

```



## 🎮 使用指南

脚本提供两种模式：**公网 URL 模式** 和 **本地文件模式**。这两种模式互斥，每次只能选择一种。

### 模式一：公网 URL（适合已有网课在线链接）

假设你有一节冗长的网课（比如令人头秃的线性代数），并且能直接拿到它的 `.mp4` 或 `.mp3` 公网直链：

```bash
python make_note.py -u [https://example.com/linear_algebra_lesson1.mp4](https://example.com/linear_algebra_lesson1.mp4) -f /Users/me/Notes/线性代数第一讲.md

```

### 模式二：本地文件（适合自己录音或下载的视频）

假设你有一节晦涩难懂的专业课录音存放在了本地（例如量子力学原声录音），需要提取干货：

```bash
python make_note.py -l /Users/me/Downloads/量子力学_第一章.mp3 -f /Users/me/Notes/量子力学笔记.md

```

*(💡 程序会自动将录音上传到你的 OSS，调用大模型分析，并在生成笔记后自动销毁云端的临时录音文件！)*

## 📄 笔记效果预览

生成的 Markdown 笔记大致如下，排版精美，直接背诵即可：

```markdown
---
## 📝 课程笔记 - 2026-03-12 10:00
> 🗂️ 本地文件：`量子力学_第一章.mp3`

### 📋 课程大纲
- **波函数的统计诠释**
- **薛定谔方程的推导与意义**
- **态叠加原理**

### 📌 详细要点
- **核心考点1：波函数的物理意义**：波函数的模平方代表粒子在某处出现的概率密度。注意标准化条件是期末必考计算题。
- **核心考点2：薛定谔方程**：描述微观粒子状态随时间演化的基本方程...
...

```

## ⚠️ 免责声明

本项目初衷为辅助学习、节省整理笔记的时间。**期末考试能不能过，最终还是取决于你自己有没有背这份笔记！** 祝大家期末科科满绩！O(*￣▽￣*)ブ

## 🤝 贡献与反馈

如果你有更好的 Prompt 建议或者发现了 Bug，欢迎提交 Issue 或 Pull Request！
