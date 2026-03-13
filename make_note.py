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

  -u 和 -l 二者必选其一，不可同时使用。

【依赖安装】
    pip install alibabacloud_tingwu20230930 alibabacloud_tea_openapi oss2 requests
"""

import argparse
import os
import sys
import time
import threading
import uuid
import requests                  # 用于下载 API 返回的结果 URL
from datetime import datetime

# ============================================================
# ⚠️  阿里云 AccessKey —— 请替换为你自己的密钥
#     获取地址：https://ram.console.aliyun.com/manage/ak
# ============================================================
ACCESS_KEY_ID = ""
ACCESS_KEY_SECRET = ""

# ============================================================
# ⚠️  通义听悟 AppKey —— 每个项目独有的标识，必须填写
#     获取地址：https://tingwu.console.aliyun.com/ → 我的项目
# ============================================================
APP_KEY = ""

# ============================================================
# ⚠️  OSS 配置 —— 仅"本地文件模式（-l）"时需要填写
#
#   OSS_ENDPOINT:   Bucket 所在地域外网 Endpoint，例如：
#                   oss-cn-beijing.aliyuncs.com
#                   oss-cn-hangzhou.aliyuncs.com
#   OSS_BUCKET_NAME: 你的 Bucket 名称，Bucket 需已存在且有写权限。
# ============================================================
OSS_ENDPOINT = ""
OSS_BUCKET_NAME = ""

# 通义听悟服务接入点（无需修改）
TINGWU_ENDPOINT = "tingwu.cn-beijing.aliyuncs.com"

# 任务轮询间隔（秒）
POLL_INTERVAL = 20


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


# ============================================================
# 主流程
# ============================================================
def main():
    # ---------- 命令行参数解析 ----------
    parser = argparse.ArgumentParser(
        prog="make_note",
        description="🎓 通义听悟课程笔记自动生成工具（支持公网URL和本地文件）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例1（公网URL）：\n"
            "  python make_note.py -u https://example.com/lesson.mp4 -f /Users/me/Notes/课程.md\n\n"
            "示例2（本地文件）：\n"
            "  python make_note.py -l /Users/me/Downloads/lesson.mp4 -f /Users/me/Notes/课程.md"
        ),
    )

    # -u 和 -l 互斥必选
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "-u", "--url",
        metavar="VIDEO_URL",
        help="视频或音频的公网可访问 URL（与 -l 二选一）",
    )
    source_group.add_argument(
        "-l", "--local_file",
        metavar="LOCAL_FILE_PATH",
        help="本地视频或音频文件路径，程序将自动上传到 OSS（与 -u 二选一）",
    )
    parser.add_argument(
        "-f", "--file",
        required=True,
        metavar="MD_FILE_PATH",
        help="目标 Markdown 文件的绝对路径（不存在则自动创建，内容追加到末尾）",
    )
    args = parser.parse_args()
    md_file = args.file

    print("\n" + "=" * 60)
    print("  🎓  通义听悟课程笔记自动生成工具")
    print("=" * 60)

    # ---------- 决定视频来源 ----------
    oss_object_key = None    # OSS 对象路径（用于事后删除）
    source_label = ""        # 本地文件名（用于笔记来源标注）

    if args.url:
        video_url = args.url
        print(f"  📹  模式：公网 URL")
        print(f"  🔗  URL：{video_url}")
    else:
        local_path = args.local_file
        source_label = os.path.basename(local_path)
        print(f"  📁  模式：本地文件上传")
        print(f"  🗂️  本地文件：{local_path}")
        # 上传到 OSS，获取公网可访问 URL 和 object_key（用于后续删除）
        video_url, oss_object_key = upload_to_oss(local_path)

    print(f"  📄  目标笔记：{md_file}")
    print("=" * 60 + "\n")

    # ---------- 构建 SDK 客户端 ----------
    client = _build_client()

    # ---------- 核心执行流：try...finally 保证 OSS 文件必被清理 ----------
    # 无论是 API 报错、网络中断，还是用户按下 Ctrl+C，finally 块都一定会执行。
    try:
        # 1. 提交离线任务
        task_id = submit_task(client, video_url)

        # 2. 轮询直到任务完成
        raw_result = wait_for_completion(client, task_id)

        # 3. 下载真实结果 URL 并组装 Markdown
        note_content = build_markdown(raw_result, video_url, source_label=source_label)

        # 4. 追加写入本地目标文件
        append_to_file(md_file, note_content)

        print("\n  🎉  全部完成！祝学习愉快 O(*￣▽￣*)ブ\n")

    finally:
        # "阅后即焚"：任何情况下（成功/失败/Ctrl+C），都清理 OSS 临时文件
        if oss_object_key:
            delete_from_oss(oss_object_key)


if __name__ == "__main__":
    main()
