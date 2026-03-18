#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_note.py — 阿里云通义听悟 API 课程笔记自动生成工具（支持本地文件上传）
============================================================================
【完整使用说明】

  模式一：公网 URL（无需上传，直接调用听悟 API）
    python make_note.py -u <视频/音频公网URL> -f <目标MD文件绝对路径>

  模式二：本地文件（自动上传到 OSS → 调用听悟 API → 完成后删除 OSS 文件）
    python make_note.py -l <本地视频/音频文件路径> -f <目标MD文件绝对路径>

  模式三：本地文件夹（批量处理文件夹内所有音视频文件）
    python make_note.py -d <本地文件夹路径> [-o <输出目录>] [--recursive]

  -u、-l 和 -d 三者必选其一，不可同时使用。

【依赖安装】
    pip install alibabacloud_tingwu20230930 alibabacloud_tea_openapi oss2 requests
"""

import argparse
import os
import re
import sys
import time
import threading
import uuid
import requests                  # 用于下载 API 返回的结果 URL
from datetime import datetime

# ============================================================
# ⚠️  运行前请通过环境变量或仓库根目录 .env 提供以下配置：
#     ALIYUN_ACCESS_KEY_ID
#     ALIYUN_ACCESS_KEY_SECRET
#     ALIYUN_TINGWU_APP_KEY
#     ALIYUN_OSS_ENDPOINT        （本地文件 / 文件夹模式需要）
#     ALIYUN_OSS_BUCKET_NAME     （本地文件 / 文件夹模式需要）
# ============================================================
ENV_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
ENV_VAR_LABELS = {
    "ALIYUN_ACCESS_KEY_ID": "阿里云 AccessKey ID",
    "ALIYUN_ACCESS_KEY_SECRET": "阿里云 AccessKey Secret",
    "ALIYUN_TINGWU_APP_KEY": "通义听悟 AppKey",
    "ALIYUN_OSS_ENDPOINT": "OSS Endpoint，例如 oss-cn-hangzhou.aliyuncs.com",
    "ALIYUN_OSS_BUCKET_NAME": "OSS Bucket 名称",
}
BASE_ENV_VARS = (
    "ALIYUN_ACCESS_KEY_ID",
    "ALIYUN_ACCESS_KEY_SECRET",
    "ALIYUN_TINGWU_APP_KEY",
)
UPLOAD_ENV_VARS = (
    "ALIYUN_OSS_ENDPOINT",
    "ALIYUN_OSS_BUCKET_NAME",
)


def load_env_file(env_path: str = ENV_FILE_PATH):
    """从仓库根目录 .env 加载环境变量；已存在的系统环境变量优先。"""
    if not os.path.isfile(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ.setdefault(key, value)


def refresh_runtime_config():
    """把当前环境变量同步到模块级配置，便于脚本和其他模块复用。"""
    global ACCESS_KEY_ID, ACCESS_KEY_SECRET, APP_KEY, OSS_ENDPOINT, OSS_BUCKET_NAME

    ACCESS_KEY_ID = os.getenv("ALIYUN_ACCESS_KEY_ID", "").strip()
    ACCESS_KEY_SECRET = os.getenv("ALIYUN_ACCESS_KEY_SECRET", "").strip()
    APP_KEY = os.getenv("ALIYUN_TINGWU_APP_KEY", "").strip()
    OSS_ENDPOINT = os.getenv("ALIYUN_OSS_ENDPOINT", "").strip()
    OSS_BUCKET_NAME = os.getenv("ALIYUN_OSS_BUCKET_NAME", "").strip()


def missing_env_vars(variable_names: tuple[str, ...]) -> list[str]:
    """返回当前尚未配置的环境变量名。"""
    refresh_runtime_config()
    return [name for name in variable_names if not os.getenv(name, "").strip()]


def ensure_runtime_config(require_oss: bool = False):
    """校验运行所需环境变量是否齐全，不齐时给出明确提示。"""
    required_vars = list(BASE_ENV_VARS)
    if require_oss:
        required_vars.extend(UPLOAD_ENV_VARS)

    missing = missing_env_vars(tuple(required_vars))
    if not missing:
        return

    lines = ["缺少以下环境变量："]
    for name in missing:
        lines.append(f"  - {name}: {ENV_VAR_LABELS[name]}")
    lines.append("")
    lines.append("请先在 shell 中 export，或在仓库根目录的 .env 文件中填写。")
    raise RuntimeError("\n".join(lines))


load_env_file()
refresh_runtime_config()

# 通义听悟服务接入点（无需修改）
TINGWU_ENDPOINT = "tingwu.cn-beijing.aliyuncs.com"

# 任务轮询间隔（秒）
POLL_INTERVAL = 20
SUPPORTED_MEDIA_EXTENSIONS = {
    ".aac", ".avi", ".flac", ".m4a", ".m4v", ".mkv",
    ".mov", ".mp3", ".mp4", ".mpeg", ".mpg", ".wav",
    ".webm", ".wma",
}


# ============================================================
# Spinner —— 控制台旋转等待动画
# ============================================================
class Spinner:
    """在终端显示旋转等待动画，给用户友好的进度反馈。"""

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = "处理中..."):
        self.message = message
        self._running = False
        self._thread = None
        self._frame_idx = 0

    def _spin(self):
        while self._running:
            frame = self.FRAMES[self._frame_idx % len(self.FRAMES)]
            sys.stdout.write(f"\r  {frame}  {self.message}")
            sys.stdout.flush()
            self._frame_idx += 1
            time.sleep(0.1)

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self, final_msg: str = ""):
        self._running = False
        if self._thread:
            self._thread.join()
        sys.stdout.write(f"\r  ✅  {final_msg}\n")
        sys.stdout.flush()

    def update(self, message: str):
        self.message = message


# ============================================================
# OSS 上传：将本地文件上传到 OSS，返回公网 URL 和 object_key
# ============================================================
def upload_to_oss(local_path: str):
    """
    将本地音视频文件上传到阿里云 OSS。
    使用 UUID 重命名防止冲突，resumable_upload 支持大文件分片上传。
    返回 (public_url, object_key)。
    """
    try:
        import oss2
    except ImportError:
        print("\n❌  缺少 oss2 依赖，请先执行：pip install oss2\n")
        sys.exit(1)

    try:
        ensure_runtime_config(require_oss=True)
    except RuntimeError as exc:
        print(f"\n❌  {exc}\n")
        sys.exit(1)

    if not os.path.isfile(local_path):
        print(f"\n❌  本地文件不存在：{local_path}\n")
        sys.exit(1)

    ext = os.path.splitext(local_path)[1] or ".mp4"
    unique_name = f"tingwu_temp_{uuid.uuid4().hex}{ext}"
    object_key = f"tingwu-temp/{unique_name}"

    file_size_mb = os.path.getsize(local_path) / 1024 / 1024
    print(f"\n  📤  开始上传本地文件到 OSS...")
    print(f"      本地路径：{local_path}")
    print(f"      文件大小：{file_size_mb:.2f} MB")
    print(f"      OSS 目标：{OSS_BUCKET_NAME}/{object_key}")

    auth = oss2.Auth(ACCESS_KEY_ID, ACCESS_KEY_SECRET)
    bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET_NAME)

    def progress_callback(consumed_bytes, total_bytes):
        pct = consumed_bytes / total_bytes * 100
        filled = int(30 * consumed_bytes / total_bytes)
        bar = "█" * filled + "░" * (30 - filled)
        sys.stdout.write(
            f"\r  📊  上传进度：[{bar}] {pct:.1f}%"
            f"  ({consumed_bytes/1024/1024:.1f}/{total_bytes/1024/1024:.1f} MB)"
        )
        sys.stdout.flush()
        if consumed_bytes >= total_bytes:
            sys.stdout.write("\n")
            sys.stdout.flush()

    oss2.resumable_upload(
        bucket, object_key, local_path,
        progress_callback=progress_callback,
        multipart_threshold=10 * 1024 * 1024,
        part_size=5 * 1024 * 1024,
    )

    public_url = f"https://{OSS_BUCKET_NAME}.{OSS_ENDPOINT}/{object_key}"
    print(f"  ✅  上传完成！OSS URL：{public_url}")
    return public_url, object_key


# ============================================================
# OSS 删除："阅后即焚"——清理临时文件
# ============================================================
def delete_from_oss(object_key: str):
    """从 OSS 删除临时文件，防止产生额外存储费用。"""
    try:
        import oss2
    except ImportError:
        print("  ⚠️   oss2 未安装，跳过云端清理（请手动删除 OSS 临时文件）")
        return

    try:
        ensure_runtime_config(require_oss=True)
    except RuntimeError as exc:
        print(f"  ⚠️   跳过云端清理：{exc}")
        return

    auth = oss2.Auth(ACCESS_KEY_ID, ACCESS_KEY_SECRET)
    bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET_NAME)
    try:
        bucket.delete_object(object_key)
        print("  🧹  临时视频文件已从云端清理，未产生多余存储费用。")
    except Exception as e:
        print(f"  ⚠️   OSS 文件删除失败（请手动删除 {object_key}）：{e}")


# ============================================================
# 阿里云通义听悟 SDK 初始化
# ============================================================
def _build_client():
    """构建通义听悟 SDK 客户端。"""
    try:
        ensure_runtime_config()
    except RuntimeError as exc:
        print(f"\n❌  {exc}\n")
        sys.exit(1)

    try:
        from alibabacloud_tingwu20230930 import client as tingwu_client
        from alibabacloud_tea_openapi import models as open_api_models
    except ImportError:
        print("\n❌  缺少依赖，请先执行：")
        print("    pip install alibabacloud_tingwu20230930 alibabacloud_tea_openapi\n")
        sys.exit(1)

    config = open_api_models.Config(
        access_key_id=ACCESS_KEY_ID,
        access_key_secret=ACCESS_KEY_SECRET,
    )
    config.endpoint = TINGWU_ENDPOINT
    return tingwu_client.Client(config)


# ============================================================
# 提交离线转写 + 摘要任务
# ============================================================
def submit_task(client, video_url: str) -> str:
    """
    向通义听悟提交离线（Offline）任务。
    严格按照官方文档（2023-09-30版本）构造参数。
    -------------------------------------------------------
    开启功能：
      - 转写 + 说话人区分（diarization_enabled）
      - 章节速览（auto_chapters_enabled）
      - 大模型摘要（summarization_enabled），类型：Paragraph
      - 会议纪要（meeting_assistance_enabled），类型：KeyInformation + Actions
    -------------------------------------------------------
    返回任务 TaskId。
    """
    from alibabacloud_tingwu20230930 import models as tingwu_models

    # 输入源：公网 URL（OSS上传后的URL或用户直传的URL均走这里）
    input_config = tingwu_models.CreateTaskRequestInput(
        source_language="cn",
        file_url=video_url
    )

    # 参数：严格遵循官方文档字段名
    parameters = tingwu_models.CreateTaskRequestParameters(
        # 转写参数：开启说话人区分
        transcription=tingwu_models.CreateTaskRequestParametersTranscription(
            diarization_enabled=True
        ),
        # 章节速览（AI 自动分段并生成章节标题）
        auto_chapters_enabled=True,
        # 大模型摘要：必须显式 enabled=True 且指定 types
        summarization_enabled=True,
        summarization=tingwu_models.CreateTaskRequestParametersSummarization(
            types=["Paragraph"]          # Paragraph = 章节速览与大纲
        ),
        # 会议纪要：关键信息 + 待办事项
        meeting_assistance_enabled=True,
        meeting_assistance=tingwu_models.CreateTaskRequestParametersMeetingAssistance(
            types=["KeyInformation", "Actions"]
        ),
        # 开启自定义提示词功能
        custom_prompt_enabled=True,
        custom_prompt=tingwu_models.CreateTaskRequestParametersCustomPrompt(
            contents=[
                tingwu_models.CreateTaskRequestParametersCustomPromptContents(
                    name="详细笔记",
                    prompt="你现在是一个极其擅长做笔记的大学霸。请结合以下课程转写全文，帮我总结这节课的详细要点。\n\n转写内容：\n{Transcription}\n\n要求：1. 极其详细，按照课程逻辑分级、分点（使用 Markdown 列表）罗列；2. 提取并重点解释课程中出现的所有核心考点、专业名词；3. 整体字数在500字左右，排版清晰，适合直接用于期末背诵复习。",
                    trans_type="default"
                )
            ]
        )
    )

    request = tingwu_models.CreateTaskRequest(
        app_key=APP_KEY,             # 通义听悟项目标识，缺少此项将报 BRK.InvalidAppKey
        type="offline",
        input=input_config,
        parameters=parameters
    )

    response = client.create_task(request)
    body = response.body

    if not body or not body.data or not body.data.task_id:
        raise RuntimeError(f"提交任务失败，响应内容：{body}")

    task_id = body.data.task_id
    print(f"\n  🚀  任务提交成功！TaskId：{task_id}")
    return task_id


# ============================================================
# 轮询任务状态，直到完成
# ============================================================
def wait_for_completion(client, task_id: str) -> dict:
    """
    每隔 POLL_INTERVAL 秒查询一次任务状态。
    状态变为 COMPLETED 后返回原始结果 map；FAILED/CANCELLED 则抛出异常。
    """
    from alibabacloud_tingwu20230930 import models as tingwu_models

    spinner = Spinner("正在等待任务完成（每 20 秒轮询一次）...")
    spinner.start()

    elapsed = 0
    while True:
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

        try:
            response = client.get_task_info(task_id)
            body = response.body
            status = body.data.task_status if body and body.data else "UNKNOWN"
        except Exception as e:
            spinner.update(f"查询出错（{e}），稍后重试...")
            continue

        spinner.update(f"任务状态：{status}，已等待 {elapsed} 秒...")

        if status == "COMPLETED":
            spinner.stop("任务已完成！开始解析结果...")
            return body.to_map()

        if status in ("FAILED", "CANCELLED"):
            spinner.stop(f"任务异常终止，状态：{status}")
            error_code = body.data.error_code if body and body.data else 'UNKNOWN'
            error_msg = body.data.error_message if body and body.data else 'UNKNOWN'
            raise RuntimeError(f"任务失败，TaskId={task_id}，状态={status}，错误码={error_code}，错误信息={error_msg}")

        # RUNNING / QUEUEING：继续等待


# ============================================================
# 下载结果 URL，返回 JSON 数据
# ============================================================
def _fetch_result_url(url: str, label: str) -> dict:
    """
    通义听悟离线任务完成后，Result 字典中的每个字段值是一个 HTTP 下载链接，
    而不是直接的 JSON 数据。本函数负责从该链接下载并解析真实内容。

    Args:
        url:   API 返回的结果下载链接
        label: 模块名称（仅用于打印错误信息）
    Returns:
        解析后的 JSON dict，失败时返回空 dict
    """
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  ⚠️   {label} 结果下载失败（{e}），该部分将跳过。")
        return {}


# ============================================================
# 解析下载后的真实数据，组装 Markdown 笔记
# ============================================================
def build_markdown(raw_result: dict, video_url: str, source_label: str = "") -> str:
    """
    根据官方文档"协议解析"规范处理结果：
      1. 从 raw_result 中取出 Result 字典（包含各功能模块的下载 URL）
      2. 下载 Summarization URL → 提取关键词、大纲、详细要点
      3. 下载 MeetingAssistance URL → 提取关键信息/待办事项（可选追加）
      4. 按规定格式组装 Markdown 文本块
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []

    # ---------- 标题 + 来源 ----------
    lines.append("\n---\n")
    lines.append(f"## 📝 课程笔记 - {now_str}\n")
    if source_label:
        lines.append(f"> 🗂️ 本地文件：`{source_label}`\n")
    lines.append(f"> 🔗 来源 URL：{video_url}\n")

    # ---------- 取出 Result 字典 ----------
    # 路径：Data -> Result（包含各模块的下载 URL）
    try:
        result_map = raw_result.get("Data", {}).get("Result", {})
    except Exception:
        result_map = {}

    if not result_map:
        lines.append("\n> ⚠️ 未能获取到任何结果数据，请检查任务状态或 API 响应。\n")
        return "".join(lines)

    # ---------- 下载 Summarization 真实数据 ----------
    # result_map["Summarization"] 的值是一个 HTTP URL，需要 GET 下载
    summarization_data = {}
    summ_url = result_map.get("Summarization") or result_map.get("summarization")
    if summ_url and isinstance(summ_url, str) and summ_url.startswith("http"):
        print("  🌐  正在下载摘要数据...")
        summarization_data = _fetch_result_url(summ_url, "Summarization")
    else:
        print("  ⚠️   未找到 Summarization 下载链接，跳过摘要部分。")

    # ---------- 下载 MeetingAssistance 真实数据（可选）----------
    meeting_data = {}
    meet_url = result_map.get("MeetingAssistance") or result_map.get("meeting_assistance")
    if meet_url and isinstance(meet_url, str) and meet_url.startswith("http"):
        print("  🌐  正在下载会议纪要数据...")
        meeting_data = _fetch_result_url(meet_url, "MeetingAssistance")

    # ---------- 下载 CustomPrompt 真实数据 ----------
    custom_prompt_data = {}
    cp_url = result_map.get("CustomPrompt") or result_map.get("custom_prompt")
    if cp_url and isinstance(cp_url, str) and cp_url.startswith("http"):
        print("  🌐  正在下载自定义提示词笔记数据...")
        custom_prompt_data = _fetch_result_url(cp_url, "CustomPrompt")

    # ---------- 组装 关键词 ----------
    # 从 MeetingAssistance 结果中获取 Keywords
    keywords = []
    if meeting_data and "MeetingAssistance" in meeting_data:
        keywords = meeting_data["MeetingAssistance"].get("Keywords", [])
    
    lines.append("### 🏷️ 关键词\n")
    if keywords:
        lines.append(", ".join(keywords) + "\n\n")
    else:
        lines.append("_（未获取到关键词，请确认任务已启用该功能）_\n\n")

    # ---------- 组装 课程大纲（章节速览） ----------
    # 从 AutoChapters 获取大标题和大纲
    auto_chapters = []
    auto_url = result_map.get("AutoChapters") or result_map.get("auto_chapters")
    if auto_url and isinstance(auto_url, str) and auto_url.startswith("http"):
        print("  🌐  正在下载章节速览数据...")
        chapters_data = _fetch_result_url(auto_url, "AutoChapters")
        if chapters_data and "AutoChapters" in chapters_data:
            auto_chapters = chapters_data["AutoChapters"]
            
    lines.append("### 📋 课程大纲\n")
    if auto_chapters:
        for ch in auto_chapters:
            headline = ch.get("Headline", "未命名章节")
            lines.append(f"- **{headline}**\n")
        lines.append("\n")
    else:
        lines.append("_（未获取到大纲数据）_\n\n")

    # ---------- 组装 详细要点（CustomPrompt Content 或者 Fallback） ----------
    lines.append("### 📌 详细要点\n")
    
    custom_content = ""
    # "CustomPrompt":[{"Name": "详细笔记", "Content": "实际生成的500字Markdown笔记..."}]}
    if custom_prompt_data and "CustomPrompt" in custom_prompt_data:
        cp_list = custom_prompt_data.get("CustomPrompt", [])
        if cp_list and isinstance(cp_list, list):
            for item in cp_list:
                if item.get("Name") == "详细笔记" or item.get("name") == "详细笔记":
                    custom_content = item.get("Result") or item.get("result") or item.get("Content") or item.get("content") or ""
                    break
            # 如果没找到名字匹配的，直接取第一个
            if not custom_content and cp_list:
                custom_content = cp_list[0].get("Result") or cp_list[0].get("result") or cp_list[0].get("Content") or cp_list[0].get("content") or ""
                
    if custom_content:
        lines.append(f"{custom_content}\n\n")
    else:
        # Fallback 到旧逻辑
        # 从 Summarization 结果中获取 ParagraphSummary
        paragraph_summary = ""
        if summarization_data and "Summarization" in summarization_data:
            paragraph_summary = summarization_data["Summarization"].get("ParagraphSummary", "")

        if paragraph_summary:
            lines.append(f"{paragraph_summary}\n\n")
        else:
            # 如果没有 ParagraphSummary，使用章节速览的具体内容
            if auto_chapters:
                for ch in auto_chapters:
                    headline = ch.get("Headline", "未命名章节")
                    summary = ch.get("Summary", "")
                    lines.append(f"**{headline}**\n")
                    lines.append(f"{summary}\n\n")
            else:
                lines.append("_（未获取到详细要点数据）_\n\n")

    return "".join(lines)


# ============================================================
# 追加 Markdown 到目标文件
# ============================================================
def append_to_file(filepath: str, content: str):
    """
    将笔记内容追加（Append）到目标 Markdown 文件末尾。
    若文件不存在，则自动创建。
    """
    parent_dir = os.path.dirname(filepath)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    with open(filepath, "a", encoding="utf-8") as f:
        f.write(content)

    print(f"\n  📄  笔记已追加至：{filepath}")


def is_supported_media_file(path: str) -> bool:
    """判断文件是否为支持处理的音视频格式。"""
    return os.path.splitext(path)[1].lower() in SUPPORTED_MEDIA_EXTENSIONS


def natural_sort_key(text: str):
    """按人类直觉拆分数字片段，避免第10节排在第2节前面。"""
    parts = re.split(r"(\d+)", text)
    return [int(part) if part.isdigit() else part.lower() for part in parts]


def sort_media_files(paths: list[str], root_dir: str) -> list[str]:
    """按文件名自然排序；同名文件再按相对路径排序。"""
    return sorted(
        paths,
        key=lambda path: (
            natural_sort_key(os.path.basename(path)),
            natural_sort_key(os.path.relpath(path, root_dir)),
        ),
    )


def collect_local_media_files(directory: str, recursive: bool = False) -> list[str]:
    """收集文件夹中的音视频文件路径。"""
    if not os.path.isdir(directory):
        raise ValueError(f"本地文件夹不存在：{directory}")

    media_files = []
    if recursive:
        for root, dirnames, filenames in os.walk(directory):
            dirnames.sort()
            for filename in sorted(filenames):
                full_path = os.path.join(root, filename)
                if os.path.isfile(full_path) and is_supported_media_file(full_path):
                    media_files.append(full_path)
    else:
        for entry in sorted(os.scandir(directory), key=lambda item: item.name):
            if entry.is_file() and is_supported_media_file(entry.path):
                media_files.append(entry.path)

    return sort_media_files(media_files, directory)


def build_batch_output_path(input_dir: str, local_path: str, output_dir: str) -> str:
    """为批量模式生成输出 Markdown 路径，保留原目录结构。"""
    relative_path = os.path.relpath(local_path, input_dir)
    note_relative_path = os.path.splitext(relative_path)[0] + ".md"
    return os.path.join(output_dir, note_relative_path)


def process_remote_source(client, video_url: str, md_file: str, source_label: str = ""):
    """处理单个可访问的视频/音频来源并生成笔记。"""
    task_id = submit_task(client, video_url)
    raw_result = wait_for_completion(client, task_id)
    note_content = build_markdown(raw_result, video_url, source_label=source_label)
    append_to_file(md_file, note_content)


def process_local_file(client, local_path: str, md_file: str):
    """处理单个本地文件：上传 OSS、生成笔记、清理临时对象。"""
    source_label = os.path.basename(local_path)
    oss_object_key = None

    try:
        video_url, oss_object_key = upload_to_oss(local_path)
        process_remote_source(client, video_url, md_file, source_label=source_label)
    finally:
        if oss_object_key:
            delete_from_oss(oss_object_key)


def process_local_directory(client, local_dir: str, output_dir: str, recursive: bool = False):
    """批量处理本地文件夹中的音视频文件。"""
    media_files = collect_local_media_files(local_dir, recursive=recursive)
    if not media_files:
        raise ValueError(
            f"文件夹中未找到支持的音视频文件：{local_dir}\n"
            f"支持格式：{', '.join(sorted(SUPPORTED_MEDIA_EXTENSIONS))}"
        )

    print(f"  🎯  共发现 {len(media_files)} 个可处理文件")
    print("  🧾  处理顺序（按文件名排序）：")
    for index, local_path in enumerate(media_files, start=1):
        print(f"      {index:>2}. {os.path.basename(local_path)}")
    success_count = 0
    failures = []

    for index, local_path in enumerate(media_files, start=1):
        md_file = build_batch_output_path(local_dir, local_path, output_dir)

        print("\n" + "-" * 60)
        print(f"  [{index}/{len(media_files)}] 开始处理")
        print(f"  🗂️  源文件：{local_path}")
        print(f"  📄  输出笔记：{md_file}")
        print("-" * 60)

        try:
            process_local_file(client, local_path, md_file)
            success_count += 1
        except KeyboardInterrupt:
            raise
        except Exception as e:
            failures.append((local_path, str(e)))
            print(f"\n  ❌  处理失败：{local_path}")
            print(f"      原因：{e}")

    print("\n" + "=" * 60)
    print(f"  📊  批量处理完成：成功 {success_count} 个，失败 {len(failures)} 个")
    if failures:
        print("  ❌  失败文件列表：")
        for failed_path, error_msg in failures:
            print(f"      - {failed_path}")
            print(f"        {error_msg}")
    print("=" * 60)

    return failures


# ============================================================
# 主流程
# ============================================================
def main():
    # ---------- 命令行参数解析 ----------
    parser = argparse.ArgumentParser(
        prog="make_note",
        description="🎓 通义听悟课程笔记自动生成工具（支持公网URL、单文件和文件夹批量处理）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例1（公网URL）：\n"
            "  python make_note.py -u https://example.com/lesson.mp4 -f /Users/me/Notes/课程.md\n\n"
            "示例2（本地文件）：\n"
            "  python make_note.py -l /Users/me/Downloads/lesson.mp4 -f /Users/me/Notes/课程.md\n\n"
            "示例3（本地文件夹）：\n"
            "  python make_note.py -d /Users/me/Downloads/课程录像 -o /Users/me/Notes --recursive\n\n"
            "环境变量：\n"
            "  ALIYUN_ACCESS_KEY_ID\n"
            "  ALIYUN_ACCESS_KEY_SECRET\n"
            "  ALIYUN_TINGWU_APP_KEY\n"
            "  ALIYUN_OSS_ENDPOINT\n"
            "  ALIYUN_OSS_BUCKET_NAME"
        ),
    )

    # -u、-l 和 -d 互斥必选
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "-u", "--url",
        metavar="VIDEO_URL",
        help="视频或音频的公网可访问 URL（与 -l、-d 三选一）",
    )
    source_group.add_argument(
        "-l", "--local_file",
        metavar="LOCAL_FILE_PATH",
        help="本地视频或音频文件路径，程序将自动上传到 OSS（与 -u、-d 三选一）",
    )
    source_group.add_argument(
        "-d", "--local_dir",
        metavar="LOCAL_DIR_PATH",
        help="本地文件夹路径，程序将批量处理其中的音视频文件（与 -u、-l 三选一）",
    )
    parser.add_argument(
        "-f", "--file",
        metavar="MD_FILE_PATH",
        help="单文件模式下的目标 Markdown 文件路径（不存在则自动创建，内容追加到末尾）",
    )
    parser.add_argument(
        "-o", "--output_dir",
        metavar="OUTPUT_DIR_PATH",
        help="文件夹模式下的输出目录，默认使用“输入文件夹/notes”",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="文件夹模式下递归处理所有子文件夹中的音视频文件",
    )
    args = parser.parse_args()

    if args.local_dir:
        if args.file:
            parser.error("文件夹模式下不支持 -f/--file，请改用 -o/--output_dir。")
        output_dir = args.output_dir or os.path.join(args.local_dir, "notes")
        md_file = None
    else:
        if not args.file:
            parser.error("公网 URL 或本地单文件模式必须提供 -f/--file。")
        if args.output_dir:
            parser.error("单文件模式下不支持 -o/--output_dir。")
        output_dir = None
        md_file = args.file

    print("\n" + "=" * 60)
    print("  🎓  通义听悟课程笔记自动生成工具")
    print("=" * 60)

    if args.url:
        print(f"  📹  模式：公网 URL")
        print(f"  🔗  URL：{args.url}")
        print(f"  📄  目标笔记：{md_file}")
    elif args.local_file:
        print(f"  📁  模式：本地文件上传")
        print(f"  🗂️  本地文件：{args.local_file}")
        print(f"  📄  目标笔记：{md_file}")
    else:
        print(f"  📂  模式：本地文件夹批量处理")
        print(f"  🗂️  输入文件夹：{args.local_dir}")
        print(f"  📄  输出目录：{output_dir}")
        print(f"  🔁  递归处理：{'是' if args.recursive else '否'}")
    print("=" * 60 + "\n")

    try:
        ensure_runtime_config(require_oss=bool(args.local_file or args.local_dir))
    except RuntimeError as exc:
        parser.exit(2, f"\n❌  {exc}\n\n")

    # ---------- 构建 SDK 客户端 ----------
    client = _build_client()

    if args.url:
        process_remote_source(client, args.url, md_file)
    elif args.local_file:
        process_local_file(client, args.local_file, md_file)
    else:
        failures = process_local_directory(
            client,
            args.local_dir,
            output_dir,
            recursive=args.recursive,
        )
        if failures:
            sys.exit(1)

    print("\n  🎉  全部完成！祝学习愉快 O(*￣▽￣*)ブ\n")


if __name__ == "__main__":
    main()
