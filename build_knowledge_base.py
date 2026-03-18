#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_knowledge_base.py — Build AI-ready knowledge bundles from course videos.
=============================================================================

Outputs per video:
  - full transcript markdown
  - paragraph-level transcript JSONL
  - retrieval-friendly chunk JSONL
  - structured knowledge-base markdown
  - raw Tingwu result JSONs
  - PPT/key frame assets when available

Examples:
  python build_knowledge_base.py -l /path/to/lesson.mp4 -o /path/to/output
  python build_knowledge_base.py -d /path/to/course_videos -o /path/to/output --recursive
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union
from urllib.parse import urlparse

import requests
try:
    import imageio.v2 as imageio_v2
except Exception:
    imageio_v2 = None

from make_note import (
    APP_KEY,
    SUPPORTED_MEDIA_EXTENSIONS,
    _build_client,
    _fetch_result_url,
    collect_local_media_files,
    delete_from_oss,
    upload_to_oss,
    wait_for_completion,
)

DEFAULT_CHUNK_MAX_CHARS = 1200
DEFAULT_FALLBACK_FRAME_COUNT = 12
INTERMEDIATE_ROOT_FILES = {"library_index.json", "library_index.jsonl", ".DS_Store"}
INTERMEDIATE_BUNDLE_FILES = {
    "transcript_paragraphs.jsonl",
    "retrieval_chunks.jsonl",
    ".DS_Store",
}
INTERMEDIATE_BUNDLE_DIRS = {"raw"}
VIDEO_EXTENSIONS = {
    ".avi", ".dat", ".flv", ".m4v", ".mkv", ".mov", ".mp4",
    ".mpeg", ".mpg", ".ogg", ".rmvb", ".webm", ".wmv", ".3gp",
}


def ensure_directory(path: Union[str, Path]) -> Path:
    """Create a directory if it does not exist."""
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_json(path: Union[str, Path], data: Any):
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_jsonl(path: Union[str, Path], rows: list[dict[str, Any]]):
    with Path(path).open("w", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_text(path: Union[str, Path], content: str):
    Path(path).write_text(content, encoding="utf-8")


def format_timestamp(ms: Optional[int]) -> str:
    """Convert millisecond offsets to HH:MM:SS."""
    if ms is None:
        return "00:00:00"
    total_seconds = max(0, int(ms) // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def guess_extension_from_url(url: str, default: str = ".bin") -> str:
    path = urlparse(url).path
    ext = Path(path).suffix.lower()
    return ext or default


def is_video_source(name: str) -> bool:
    return Path(name).suffix.lower() in VIDEO_EXTENSIONS


def build_bundle_dir(output_root: str, source_name: str, source_root: Optional[str] = None) -> str:
    """Create a bundle directory path for one source while preserving relative structure."""
    if source_root:
        relative_path = os.path.relpath(source_name, source_root)
        relative_stem = os.path.splitext(relative_path)[0]
        return os.path.join(output_root, relative_stem)

    parsed = urlparse(source_name)
    stem = Path(parsed.path or source_name).stem or "source"
    return os.path.join(output_root, stem)


def extract_keywords(payloads: dict[str, Any]) -> list[str]:
    return (
        payloads.get("MeetingAssistance", {})
        .get("MeetingAssistance", {})
        .get("Keywords", [])
        or []
    )


def extract_auto_chapters(payloads: dict[str, Any]) -> list[dict[str, Any]]:
    return payloads.get("AutoChapters", {}).get("AutoChapters", []) or []


def submit_kb_task(client, media_url: str, enable_ppt: bool) -> str:
    """Submit a Tingwu offline task tuned for knowledge-base extraction."""
    from alibabacloud_tingwu20230930 import models as tingwu_models

    input_config = tingwu_models.CreateTaskRequestInput(
        source_language="cn",
        file_url=media_url,
    )

    parameters = tingwu_models.CreateTaskRequestParameters(
        transcription=tingwu_models.CreateTaskRequestParametersTranscription(
            diarization_enabled=True
        ),
        auto_chapters_enabled=True,
        summarization_enabled=True,
        summarization=tingwu_models.CreateTaskRequestParametersSummarization(
            types=["Paragraph", "QuestionsAnswering"]
        ),
        meeting_assistance_enabled=True,
        meeting_assistance=tingwu_models.CreateTaskRequestParametersMeetingAssistance(
            types=["KeyInformation", "Actions"]
        ),
        custom_prompt_enabled=True,
        custom_prompt=tingwu_models.CreateTaskRequestParametersCustomPrompt(
            contents=[
                tingwu_models.CreateTaskRequestParametersCustomPromptContents(
                    name="知识库整理",
                    prompt=(
                        "你是一个擅长给 AI 知识库整理原始课程资料的助手。"
                        "请基于如下完整转写内容，输出一份结构化 Markdown。"
                        "要求：\n"
                        "1. 严格基于原文，不要编造；\n"
                        "2. 按课程顺序整理成分级标题和分点；\n"
                        "3. 提取课程主题、核心概念、关键方法、案例、易混点；\n"
                        "4. 补充 8-12 条适合检索问答的 FAQ；\n"
                        "5. 输出适合后续 AI 检索和问答使用的内容。\n\n"
                        "转写内容：\n{Transcription}"
                    ),
                    trans_type="default",
                )
            ]
        ),
        ppt_extraction_enabled=enable_ppt,
    )

    request = tingwu_models.CreateTaskRequest(
        app_key=APP_KEY,
        type="offline",
        input=input_config,
        parameters=parameters,
    )

    response = client.create_task(request)
    body = response.body
    if not body or not body.data or not body.data.task_id:
        raise RuntimeError(f"Submit task failed: {body}")

    task_id = body.data.task_id
    print(f"\n  🚀  Task submitted for knowledge bundle. TaskId: {task_id}")
    return task_id


def download_result_payloads(raw_result: dict) -> dict[str, Any]:
    """Download every available Tingwu result JSON referenced by the task result."""
    result_map = raw_result.get("Data", {}).get("Result", {}) or {}
    payloads = {}

    for result_name in [
        "Transcription",
        "AutoChapters",
        "Summarization",
        "MeetingAssistance",
        "CustomPrompt",
        "PptExtraction",
    ]:
        url = result_map.get(result_name) or result_map.get(result_name.lower())
        if url and isinstance(url, str) and url.startswith("http"):
            print(f"  🌐  Downloading {result_name} payload...")
            payloads[result_name] = _fetch_result_url(url, result_name)

    return payloads


def extract_custom_prompt_text(custom_prompt_payload: dict) -> str:
    prompt_list = custom_prompt_payload.get("CustomPrompt", [])
    if not isinstance(prompt_list, list):
        return ""

    for item in prompt_list:
        if item.get("Name") == "知识库整理" or item.get("name") == "知识库整理":
            return (
                item.get("Result")
                or item.get("result")
                or item.get("Content")
                or item.get("content")
                or ""
            ).strip()

    if prompt_list:
        first = prompt_list[0]
        return (
            first.get("Result")
            or first.get("result")
            or first.get("Content")
            or first.get("content")
            or ""
        ).strip()

    return ""


def parse_transcription_payload(transcription_payload: dict) -> dict[str, Any]:
    """Normalize Tingwu transcription data into paragraph and sentence records."""
    transcription = transcription_payload.get("Transcription", {}) or {}
    paragraphs = []
    sentences = []

    for paragraph_index, paragraph in enumerate(transcription.get("Paragraphs", []), start=1):
        words = [word for word in paragraph.get("Words", []) if word.get("Text")]
        if not words:
            continue

        paragraph_text = "".join(word.get("Text", "") for word in words).strip()
        if not paragraph_text:
            continue

        start_ms = words[0].get("Start")
        end_ms = words[-1].get("End")
        sentence_map = {}
        sentence_order = []

        for word in words:
            sentence_id = word.get("SentenceId") or word.get("Id")
            if sentence_id not in sentence_map:
                sentence_map[sentence_id] = []
                sentence_order.append(sentence_id)
            sentence_map[sentence_id].append(word)

        paragraph_sentences = []
        for sentence_id in sentence_order:
            sentence_words = sentence_map[sentence_id]
            sentence_text = "".join(word.get("Text", "") for word in sentence_words).strip()
            if not sentence_text:
                continue

            sentence_record = {
                "sentence_id": sentence_id,
                "speaker_id": paragraph.get("SpeakerId"),
                "start_ms": sentence_words[0].get("Start"),
                "end_ms": sentence_words[-1].get("End"),
                "text": sentence_text,
            }
            sentences.append(sentence_record)
            paragraph_sentences.append(sentence_record)

        paragraphs.append(
            {
                "paragraph_index": paragraph_index,
                "paragraph_id": paragraph.get("ParagraphId"),
                "speaker_id": paragraph.get("SpeakerId"),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": paragraph_text,
                "sentences": paragraph_sentences,
            }
        )

    return {
        "audio_info": transcription.get("AudioInfo", {}),
        "audio_segments": transcription.get("AudioSegments", []),
        "paragraphs": paragraphs,
        "sentences": sentences,
    }


def build_retrieval_chunks(paragraphs: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    """Merge nearby transcript paragraphs into retrieval-friendly chunks."""
    chunks = []
    current_paragraphs = []
    current_length = 0

    def flush_chunk():
        nonlocal current_paragraphs, current_length
        if not current_paragraphs:
            return

        text = "\n".join(paragraph["text"] for paragraph in current_paragraphs)
        chunks.append(
            {
                "chunk_index": len(chunks) + 1,
                "start_ms": current_paragraphs[0]["start_ms"],
                "end_ms": current_paragraphs[-1]["end_ms"],
                "speaker_ids": sorted(
                    {
                        paragraph["speaker_id"]
                        for paragraph in current_paragraphs
                        if paragraph.get("speaker_id") is not None
                    }
                ),
                "paragraph_indices": [
                    paragraph["paragraph_index"] for paragraph in current_paragraphs
                ],
                "text": text,
            }
        )
        current_paragraphs = []
        current_length = 0

    for paragraph in paragraphs:
        paragraph_length = len(paragraph["text"]) + 1
        if current_paragraphs and current_length + paragraph_length > max_chars:
            flush_chunk()

        current_paragraphs.append(paragraph)
        current_length += paragraph_length

    flush_chunk()
    return chunks


def build_transcript_markdown(source_name: str, transcript_info: dict[str, Any]) -> str:
    """Create a readable markdown transcript with timestamps and speaker labels."""
    audio_info = transcript_info.get("audio_info", {})
    paragraphs = transcript_info.get("paragraphs", [])

    lines = [
        f"# Full Transcript - {source_name}",
        "",
        "## Metadata",
        f"- Duration: {format_timestamp(audio_info.get('Duration'))}",
        f"- Sample Rate: {audio_info.get('SampleRate', 'unknown')}",
        f"- Language: {audio_info.get('Language', 'unknown')}",
        f"- Paragraph Count: {len(paragraphs)}",
        "",
        "## Transcript",
        "",
    ]

    if not paragraphs:
        lines.append("_No transcript content was returned._")
        return "\n".join(lines) + "\n"

    for paragraph in paragraphs:
        speaker = paragraph.get("speaker_id") or "unknown"
        lines.append(
            f"### [{format_timestamp(paragraph.get('start_ms'))} - "
            f"{format_timestamp(paragraph.get('end_ms'))}] Speaker {speaker}"
        )
        lines.append(paragraph["text"])
        lines.append("")

    return "\n".join(lines)


def to_markdown_bullets(value: Any) -> list[str]:
    """Convert mixed JSON values to a compact markdown bullet list."""
    if value in (None, "", [], {}):
        return []

    if isinstance(value, str):
        return [f"- {value.strip()}"] if value.strip() else []

    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict):
                compact = ", ".join(
                    f"{key}: {val}" for key, val in item.items() if val not in (None, "", [], {})
                )
                if compact:
                    lines.append(f"- {compact}")
            elif str(item).strip():
                lines.append(f"- {str(item).strip()}")
        return lines

    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if item in (None, "", [], {}):
                continue
            if isinstance(item, (dict, list)):
                compact = json.dumps(item, ensure_ascii=False)
                lines.append(f"- {key}: {compact}")
            else:
                lines.append(f"- {key}: {item}")
        return lines

    return [f"- {value}"]


def download_file(url: str, destination: Union[str, Path]):
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    Path(destination).write_bytes(response.content)


def build_frame_targets(
    auto_chapters_payload: Union[dict, list],
    max_frames: int = DEFAULT_FALLBACK_FRAME_COUNT,
    offset_ms: int = 2000,
) -> list[dict[str, Any]]:
    """Pick representative timestamps from chapter starts for fallback frame extraction."""
    if isinstance(auto_chapters_payload, list):
        chapters = auto_chapters_payload
    else:
        chapters = auto_chapters_payload.get("AutoChapters", []) or []
    if not chapters:
        return []

    if len(chapters) <= max_frames:
        selected = chapters
    else:
        selected = []
        for index in range(max_frames):
            chapter_index = round(index * (len(chapters) - 1) / (max_frames - 1))
            selected.append(chapters[chapter_index])

    targets = []
    seen_starts = set()
    for chapter in selected:
        start_ms = int(chapter.get("Start", 0))
        end_ms = int(chapter.get("End", start_ms))
        capture_ms = min(start_ms + offset_ms, end_ms)
        if capture_ms in seen_starts:
            continue
        seen_starts.add(capture_ms)
        targets.append(
            {
                "chapter_id": chapter.get("Id"),
                "headline": chapter.get("Headline") or "Untitled chapter",
                "summary": chapter.get("Summary") or "",
                "start_ms": start_ms,
                "capture_ms": capture_ms,
            }
        )

    return targets


def extract_local_keyframes(
    local_video_path: str,
    auto_chapters_payload: dict,
    keyframe_dir: Union[str, Path],
    max_frames: int = DEFAULT_FALLBACK_FRAME_COUNT,
) -> dict[str, Any]:
    """Fallback keyframe extraction using local video frames and chapter timestamps."""
    if imageio_v2 is None:
        return {
            "status": "fallback_unavailable",
            "reason": "imageio ffmpeg plugin is not installed",
            "content": "",
            "pdf_path": None,
            "keyframes": [],
        }

    targets = build_frame_targets(auto_chapters_payload, max_frames=max_frames)
    if not targets:
        return {
            "status": "fallback_unavailable",
            "reason": "no auto chapters available for frame targeting",
            "content": "",
            "pdf_path": None,
            "keyframes": [],
        }

    output_dir = ensure_directory(keyframe_dir)
    reader = imageio_v2.get_reader(local_video_path, format="ffmpeg")

    try:
        meta = reader.get_meta_data()
        fps = float(meta.get("fps") or 0)
        if fps <= 0:
            raise RuntimeError("invalid video fps metadata")

        keyframes = []
        for index, target in enumerate(targets, start=1):
            frame_index = max(0, int((target["capture_ms"] / 1000.0) * fps))
            frame = reader.get_data(frame_index)
            filename = f"{index:03d}.jpg"
            local_path = output_dir / filename
            imageio_v2.imwrite(local_path, frame)
            keyframes.append(
                {
                    "index": index,
                    "chapter_id": target["chapter_id"],
                    "capture_ms": target["capture_ms"],
                    "headline": target["headline"],
                    "summary": target["summary"],
                    "local_path": str(local_path),
                    "source": "local_fallback",
                }
            )

        return {
            "status": "local_fallback",
            "reason": "tingwu ppt extraction returned no assets",
            "content": "",
            "pdf_path": None,
            "keyframes": keyframes,
        }
    finally:
        reader.close()


def download_ppt_assets(ppt_payload: dict, keyframe_dir: Union[str, Path]) -> dict[str, Any]:
    """Download PPT/key frame assets when Tingwu extracted them."""
    ppt = ppt_payload.get("PptExtraction", {}) or {}
    keyframe_entries = ppt.get("KeyFrameList", []) or []
    output_dir = ensure_directory(keyframe_dir)

    manifest = {
        "status": "tingwu_success",
        "reason": "",
        "content": ppt.get("Content", ""),
        "pdf_path": None,
        "keyframes": [],
    }

    pdf_url = ppt.get("PdfPath") or ppt.get("pdf_path")
    if pdf_url:
        pdf_path = output_dir / "slides.pdf"
        try:
            download_file(pdf_url, pdf_path)
            manifest["pdf_path"] = str(pdf_path)
        except Exception as exc:
            print(f"  ⚠️   PPT PDF download failed: {exc}")

    for index, frame in enumerate(keyframe_entries, start=1):
        file_url = frame.get("FileUrl") or frame.get("file_url")
        summary = frame.get("Summary") or frame.get("summary") or ""
        if not file_url:
            continue

        extension = guess_extension_from_url(file_url, default=".png")
        filename = f"{index:03d}{extension}"
        local_path = output_dir / filename

        try:
            download_file(file_url, local_path)
            manifest["keyframes"].append(
                {
                    "index": index,
                    "summary": summary,
                    "local_path": str(local_path),
                    "source_url": file_url,
                }
            )
        except Exception as exc:
            print(f"  ⚠️   Key frame download failed for #{index}: {exc}")

    if not manifest["content"] and not manifest["pdf_path"] and not manifest["keyframes"]:
        manifest["status"] = "tingwu_empty"
        manifest["reason"] = "Tingwu returned PptExtraction payload without extractable assets."

    return manifest


def build_kb_markdown(
    source_name: str,
    task_id: str,
    transcript_info: dict[str, Any],
    payloads: dict[str, Any],
    keyframe_manifest: dict[str, Any],
) -> str:
    """Assemble the final markdown knowledge package."""
    keywords = (
        payloads.get("MeetingAssistance", {})
        .get("MeetingAssistance", {})
        .get("Keywords", [])
    )
    key_information = (
        payloads.get("MeetingAssistance", {})
        .get("MeetingAssistance", {})
        .get("KeyInformation", [])
    )
    auto_chapters = (
        payloads.get("AutoChapters", {})
        .get("AutoChapters", [])
    )
    summarization = (
        payloads.get("Summarization", {})
        .get("Summarization", {})
    )
    custom_prompt_text = extract_custom_prompt_text(payloads.get("CustomPrompt", {}))

    lines = [
        f"# Knowledge Bundle - {source_name}",
        "",
        "## Source",
        f"- TaskId: {task_id}",
        f"- Generated At: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Duration: {format_timestamp(transcript_info.get('audio_info', {}).get('Duration'))}",
        f"- Paragraph Count: {len(transcript_info.get('paragraphs', []))}",
        "",
        "## Keywords",
    ]

    keyword_lines = [f"- {keyword}" for keyword in keywords]
    lines.extend(keyword_lines or ["- No keywords returned."])
    lines.append("")

    lines.append("## Course Outline")
    if auto_chapters:
        for chapter in auto_chapters:
            headline = chapter.get("Headline") or chapter.get("headline") or "Untitled chapter"
            summary = chapter.get("Summary") or chapter.get("summary") or ""
            lines.append(f"### {headline}")
            if summary:
                lines.append(summary)
            lines.append("")
    else:
        lines.append("_No chapter structure returned._")
        lines.append("")

    lines.append("## Structured Knowledge Notes")
    if custom_prompt_text:
        lines.append(custom_prompt_text)
        lines.append("")
    else:
        lines.append("_Custom knowledge prompt returned no content; falling back to other sections._")
        lines.append("")

    lines.append("## Key Information")
    key_info_lines = to_markdown_bullets(key_information)
    lines.extend(key_info_lines or ["- No key information returned."])
    lines.append("")

    lines.append("## Full Summary")
    paragraph_summary = summarization.get("ParagraphSummary", "")
    if paragraph_summary:
        lines.append(paragraph_summary)
    else:
        lines.append("_No paragraph summary returned._")
    lines.append("")

    question_answering = summarization.get("QuestionsAnswering")
    if question_answering not in (None, "", [], {}):
        lines.append("## QA Review")
        lines.extend(to_markdown_bullets(question_answering) or ["- No QA items returned."])
        lines.append("")

    ppt_content = keyframe_manifest.get("content", "")
    keyframes = keyframe_manifest.get("keyframes", [])
    if ppt_content or keyframes:
        lines.append("## PPT / Key Frames")
        if ppt_content:
            lines.append(ppt_content)
            lines.append("")
        for frame in keyframes:
            label = frame.get("summary") or frame.get("headline") or "No summary"
            lines.append(f"- Frame {frame['index']:03d}: {label}")
            lines.append(f"  Local file: {Path(frame['local_path']).name}")
        lines.append("")
    else:
        lines.append("## PPT / Key Frames")
        lines.append(
            f"_No key frames available. Status: {keyframe_manifest.get('status', 'unknown')}; "
            f"Reason: {keyframe_manifest.get('reason', 'not provided')}_"
        )
        lines.append("")

    lines.append("## Files")
    lines.append("- `transcript_full.md`: full transcript with timestamps")
    lines.append("- `transcript_paragraphs.jsonl`: paragraph-level transcript records")
    lines.append("- `retrieval_chunks.jsonl`: retrieval-friendly chunks for indexing")
    lines.append("- `raw/`: raw Tingwu result payloads")
    if keyframes:
        lines.append("- `keyframes/`: PPT/key frame image assets")
    lines.append("")

    return "\n".join(lines)


def save_payloads(raw_dir: Union[str, Path], payloads: dict[str, Any]):
    raw_output_dir = ensure_directory(raw_dir)
    for name, payload in payloads.items():
        write_json(raw_output_dir / f"{name}.json", payload)


def load_bundle_payloads(bundle_dir: Union[str, Path]) -> dict[str, Any]:
    """Load raw Tingwu payloads that were already written to disk."""
    bundle_path = Path(bundle_dir)
    payloads = {}
    for name in [
        "AutoChapters",
        "CustomPrompt",
        "MeetingAssistance",
        "PptExtraction",
        "Summarization",
        "Transcription",
    ]:
        payload_path = bundle_path / "raw" / f"{name}.json"
        if payload_path.exists():
            payloads[name] = json.loads(payload_path.read_text(encoding="utf-8"))
    return payloads


def find_chunk_chapters(
    chunk_start_ms: int,
    chunk_end_ms: int,
    auto_chapters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return chapters that overlap a retrieval chunk."""
    matches = []
    for chapter in auto_chapters:
        chapter_start = int(chapter.get("Start", 0))
        chapter_end = int(chapter.get("End", chapter_start))
        if chunk_start_ms <= chapter_end and chunk_end_ms >= chapter_start:
            matches.append(chapter)
    return matches


def build_rag_chunks(
    metadata: dict[str, Any],
    payloads: dict[str, Any],
    retrieval_chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Enrich retrieval chunks with stable metadata for RAG ingestion."""
    auto_chapters = payloads.get("AutoChapters", {}).get("AutoChapters", []) or []
    keywords = (
        payloads.get("MeetingAssistance", {})
        .get("MeetingAssistance", {})
        .get("Keywords", [])
    )

    rag_chunks = []
    for chunk in retrieval_chunks:
        chunk_start_ms = int(chunk.get("start_ms") or 0)
        chunk_end_ms = int(chunk.get("end_ms") or chunk_start_ms)
        chapter_matches = find_chunk_chapters(chunk_start_ms, chunk_end_ms, auto_chapters)
        rag_chunks.append(
            {
                "chunk_id": f"{metadata.get('source_name', 'source')}::chunk::{int(chunk['chunk_index']):03d}",
                "source_name": metadata.get("source_name"),
                "task_id": metadata.get("task_id"),
                "bundle_dir": metadata.get("bundle_dir"),
                "start_ms": chunk_start_ms,
                "end_ms": chunk_end_ms,
                "time_range": f"{format_timestamp(chunk_start_ms)}-{format_timestamp(chunk_end_ms)}",
                "speaker_ids": chunk.get("speaker_ids", []),
                "paragraph_indices": chunk.get("paragraph_indices", []),
                "keywords": keywords,
                "chapter_ids": [chapter.get("Id") for chapter in chapter_matches],
                "chapter_titles": [
                    chapter.get("Headline") or "Untitled chapter" for chapter in chapter_matches
                ],
                "text": chunk.get("text", ""),
            }
        )

    return rag_chunks


def build_bundle_rag_markdown(
    metadata: dict[str, Any],
    payloads: Optional[dict[str, Any]],
    rag_chunks: list[dict[str, Any]],
    knowledge_overview_text: str = "",
) -> str:
    """Create a chunk-first markdown document designed for RAG ingestion."""
    payloads = payloads or {}
    auto_chapters = extract_auto_chapters(payloads) or metadata.get("chapters", [])
    keywords = extract_keywords(payloads) or metadata.get("keywords", [])
    custom_prompt_text = extract_custom_prompt_text(payloads.get("CustomPrompt", {})) or knowledge_overview_text

    lines = [
        f"# RAG Source Pack - {metadata.get('source_name', 'source')}",
        "",
        "## Source Metadata",
        f"- source_name: {metadata.get('source_name', 'unknown')}",
        f"- task_id: {metadata.get('task_id', 'unknown')}",
        f"- generated_at: {metadata.get('generated_at', 'unknown')}",
        f"- transcript_paragraph_count: {metadata.get('transcript_paragraph_count', 0)}",
        f"- retrieval_chunk_count: {metadata.get('retrieval_chunk_count', 0)}",
        f"- keyframe_count: {metadata.get('keyframe_count', 0)}",
        "",
        "## Retrieval Guidance",
        "- Split on `### CHUNK` when importing into a retriever.",
        "- Prefer chunk metadata fields for filtering by source, chapter, speaker, or time.",
        "- Use chapter titles and keywords as retrieval hints, not as standalone truth.",
        "",
        "## Keywords",
    ]

    if keywords:
        lines.extend([f"- {keyword}" for keyword in keywords])
    else:
        lines.append("- No keywords returned.")
    lines.append("")

    lines.append("## Chapter Map")
    if auto_chapters:
        for chapter in auto_chapters:
            chapter_id = chapter.get("Id")
            chapter_start = format_timestamp(chapter.get("Start"))
            chapter_end = format_timestamp(chapter.get("End"))
            headline = chapter.get("Headline") or "Untitled chapter"
            summary = chapter.get("Summary") or ""
            lines.append(f"- CH{int(chapter_id):02d} | {chapter_start}-{chapter_end} | {headline}")
            if summary:
                lines.append(f"  summary: {summary}")
    else:
        lines.append("- No chapter map returned.")
    lines.append("")

    lines.append("## Knowledge Overview")
    if custom_prompt_text:
        lines.append(custom_prompt_text)
    else:
        lines.append("_No custom knowledge overview returned._")
    lines.append("")

    lines.append("## Retrieval Chunks")
    if not rag_chunks:
        lines.append("_No retrieval chunks available._")
    else:
        for chunk in rag_chunks:
            lines.append(f"### CHUNK {chunk['chunk_id']}")
            lines.append(f"- source_name: {chunk['source_name']}")
            lines.append(f"- task_id: {chunk['task_id']}")
            lines.append(f"- time_range: {chunk['time_range']}")
            lines.append(f"- chapter_titles: {', '.join(chunk['chapter_titles']) or 'none'}")
            lines.append(f"- speaker_ids: {', '.join(str(item) for item in chunk['speaker_ids']) or 'none'}")
            lines.append(
                f"- paragraph_indices: {', '.join(str(item) for item in chunk['paragraph_indices']) or 'none'}"
            )
            lines.append(f"- keywords: {', '.join(chunk['keywords']) or 'none'}")
            lines.append("- text:")
            lines.append(chunk["text"])
            lines.append("")

    return "\n".join(lines)


def load_jsonl_rows(path: Union[str, Path]) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    rows = []
    with target.open("r", encoding="utf-8") as file_obj:
        for line in file_obj:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


def remove_path(path: Union[str, Path]):
    target = Path(path)
    if not target.exists():
        return
    if target.is_dir():
        for child in target.iterdir():
            remove_path(child)
        target.rmdir()
    else:
        target.unlink()


def cleanup_output_root(output_root: Union[str, Path]):
    """Remove intermediate/debug files and macOS metadata from the output directory."""
    root = Path(output_root)

    for filename in INTERMEDIATE_ROOT_FILES:
        remove_path(root / filename)

    for bundle_dir in root.iterdir():
        if not bundle_dir.is_dir():
            continue
        for filename in INTERMEDIATE_BUNDLE_FILES:
            remove_path(bundle_dir / filename)
        for dirname in INTERMEDIATE_BUNDLE_DIRS:
            remove_path(bundle_dir / dirname)


def collect_library_rag_chunks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        rows.extend(load_jsonl_rows(Path(record["bundle_dir"]) / "rag_chunks.jsonl"))
    return rows


def build_library_rag_markdown(records: list[dict[str, Any]]) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# RAG Library Pack",
        "",
        "## Overview",
        f"- generated_at: {generated_at}",
        f"- bundle_count: {len(records)}",
        "",
        "## Documents",
        "",
    ]

    if not records:
        lines.append("_No bundles were generated._")
        lines.append("")
        return "\n".join(lines)

    for index, record in enumerate(records, start=1):
        bundle_dir = Path(record["bundle_dir"])
        rag_path = bundle_dir / "bundle_rag.md"
        lines.append(f"---\n")
        lines.append(f"## DOCUMENT {index}: {record.get('source_name', bundle_dir.name)}")
        lines.append("")
        if rag_path.exists():
            lines.append(rag_path.read_text(encoding="utf-8").strip())
        else:
            lines.append("_bundle_rag.md is missing._")
        lines.append("")

    return "\n".join(lines)


def build_bundle_master_markdown(bundle_path: Union[str, Path], metadata: dict[str, Any]) -> str:
    """Combine the main textual assets of one bundle into a single markdown file."""
    bundle_dir = Path(bundle_path)
    knowledge_base_path = bundle_dir / "knowledge_base.md"
    transcript_path = bundle_dir / "transcript_full.md"

    knowledge_text = knowledge_base_path.read_text(encoding="utf-8") if knowledge_base_path.exists() else ""
    transcript_text = transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else ""

    lines = [
        f"# Bundle Master - {metadata.get('source_name', bundle_dir.name)}",
        "",
        "## Bundle Metadata",
        f"- TaskId: {metadata.get('task_id', 'unknown')}",
        f"- Generated At: {metadata.get('generated_at', 'unknown')}",
        f"- Transcript Paragraph Count: {metadata.get('transcript_paragraph_count', 0)}",
        f"- Retrieval Chunk Count: {metadata.get('retrieval_chunk_count', 0)}",
        f"- Key Frame Count: {metadata.get('keyframe_count', 0)}",
        "",
        "## Structured Knowledge",
        "",
    ]

    if knowledge_text:
        lines.append(knowledge_text.strip())
    else:
        lines.append("_knowledge_base.md is missing._")

    lines.extend(["", "## Full Transcript", ""])
    if transcript_text:
        lines.append(transcript_text.strip())
    else:
        lines.append("_transcript_full.md is missing._")

    lines.append("")
    return "\n".join(lines)


def build_library_master_markdown(records: list[dict[str, Any]]) -> str:
    """Combine all bundle text into a single course-level markdown file."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Knowledge Library Master",
        "",
        "## Overview",
        f"- Generated At: {generated_at}",
        f"- Bundle Count: {len(records)}",
        "",
        "## Bundles",
        "",
    ]

    if not records:
        lines.append("_No bundles were generated._")
        lines.append("")
        return "\n".join(lines)

    for index, record in enumerate(records, start=1):
        bundle_dir = Path(record["bundle_dir"])
        bundle_master_path = bundle_dir / "bundle_master.md"
        if bundle_master_path.exists():
            bundle_text = bundle_master_path.read_text(encoding="utf-8").strip()
        else:
            bundle_text = build_bundle_master_markdown(bundle_dir, record).strip()

        lines.append(f"---\n")
        lines.append(f"## {index}. {record.get('source_name', bundle_dir.name)}")
        lines.append("")
        lines.append(bundle_text)
        lines.append("")

    return "\n".join(lines)


def process_source(
    client,
    source_label: str,
    bundle_dir: str,
    local_path: Optional[str] = None,
    media_url: Optional[str] = None,
    chunk_max_chars: int = DEFAULT_CHUNK_MAX_CHARS,
    keep_intermediate_files: bool = False,
) -> dict[str, Any]:
    """Process one URL or local file into a knowledge bundle."""
    bundle_path = ensure_directory(bundle_dir)
    keyframe_dir = ensure_directory(bundle_path / "keyframes")
    local_bundle_source = local_path or media_url or source_label
    enable_ppt = is_video_source(local_bundle_source)

    temporary_object_key = None
    effective_media_url = media_url

    try:
        if local_path:
            print(f"  📤  Uploading local media: {local_path}")
            effective_media_url, temporary_object_key = upload_to_oss(local_path)

        task_id = submit_kb_task(client, effective_media_url, enable_ppt=enable_ppt)
        raw_result = wait_for_completion(client, task_id)

        payloads = download_result_payloads(raw_result)
        if keep_intermediate_files:
            raw_dir = ensure_directory(bundle_path / "raw")
            write_json(raw_dir / "task_result.json", raw_result)
            save_payloads(raw_dir, payloads)

        transcription_info = parse_transcription_payload(payloads.get("Transcription", {}))
        paragraph_rows = transcription_info.get("paragraphs", [])
        retrieval_chunks = build_retrieval_chunks(paragraph_rows, max_chars=chunk_max_chars)

        transcript_markdown = build_transcript_markdown(source_label, transcription_info)
        write_text(bundle_path / "transcript_full.md", transcript_markdown)
        if keep_intermediate_files:
            write_jsonl(bundle_path / "transcript_paragraphs.jsonl", paragraph_rows)
            write_jsonl(bundle_path / "retrieval_chunks.jsonl", retrieval_chunks)

        keyframe_manifest = {
            "status": "not_requested",
            "reason": "ppt extraction not requested",
            "content": "",
            "pdf_path": None,
            "keyframes": [],
        }
        if payloads.get("PptExtraction"):
            keyframe_manifest = download_ppt_assets(payloads["PptExtraction"], keyframe_dir)
            if (
                keyframe_manifest.get("status") == "tingwu_empty"
                and local_path
                and is_video_source(local_path)
            ):
                print("  🖼️   Tingwu did not return key frames, trying local fallback extraction...")
                fallback_manifest = extract_local_keyframes(
                    local_video_path=local_path,
                    auto_chapters_payload=payloads.get("AutoChapters", {}),
                    keyframe_dir=keyframe_dir,
                )
                if fallback_manifest.get("keyframes"):
                    keyframe_manifest = fallback_manifest
            write_json(bundle_path / "keyframes_manifest.json", keyframe_manifest)

        knowledge_markdown = build_kb_markdown(
            source_name=source_label,
            task_id=task_id,
            transcript_info=transcription_info,
            payloads=payloads,
            keyframe_manifest=keyframe_manifest,
        )
        write_text(bundle_path / "knowledge_base.md", knowledge_markdown)

        metadata = {
            "source_name": source_label,
            "local_path": local_path,
            "media_url": effective_media_url,
            "task_id": task_id,
            "bundle_dir": str(bundle_path),
            "transcript_paragraph_count": len(paragraph_rows),
            "retrieval_chunk_count": len(retrieval_chunks),
            "keyframe_count": len(keyframe_manifest.get("keyframes", [])),
            "keywords": extract_keywords(payloads),
            "chapters": extract_auto_chapters(payloads),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        rag_chunks = build_rag_chunks(metadata, payloads, retrieval_chunks)
        write_json(bundle_path / "metadata.json", metadata)
        write_jsonl(bundle_path / "rag_chunks.jsonl", rag_chunks)
        write_text(
            bundle_path / "bundle_rag.md",
            build_bundle_rag_markdown(
                metadata,
                payloads,
                rag_chunks,
                knowledge_overview_text=knowledge_markdown,
            ),
        )
        write_text(bundle_path / "bundle_master.md", build_bundle_master_markdown(bundle_path, metadata))
        return metadata

    finally:
        if temporary_object_key:
            delete_from_oss(temporary_object_key)


def load_existing_records(output_root: Union[str, Path]) -> list[dict[str, Any]]:
    """Load library records from an existing output directory."""
    root = Path(output_root)
    library_index_path = root / "library_index.json"
    if library_index_path.exists():
        return json.loads(library_index_path.read_text(encoding="utf-8"))

    records = []
    for metadata_path in sorted(root.glob("*/metadata.json")):
        records.append(json.loads(metadata_path.read_text(encoding="utf-8")))
    return records


def rebuild_existing_output(output_root: Union[str, Path]) -> list[dict[str, Any]]:
    """Rebuild master markdown files and local fallback key frames from existing output."""
    root = Path(output_root)
    records = load_existing_records(root)

    for record in records:
        bundle_dir = Path(record["bundle_dir"])
        keyframes_manifest_path = bundle_dir / "keyframes_manifest.json"
        raw_auto_chapters_path = bundle_dir / "raw" / "AutoChapters.json"

        keyframe_manifest = {
            "status": "unknown",
            "reason": "manifest missing",
            "content": "",
            "pdf_path": None,
            "keyframes": [],
        }
        if keyframes_manifest_path.exists():
            keyframe_manifest = json.loads(keyframes_manifest_path.read_text(encoding="utf-8"))

        if not record.get("chapters") and raw_auto_chapters_path.exists():
            raw_auto_chapters = json.loads(raw_auto_chapters_path.read_text(encoding="utf-8"))
            record["chapters"] = raw_auto_chapters.get("AutoChapters", [])

        if (
            not keyframe_manifest.get("keyframes")
            and record.get("local_path")
            and os.path.isfile(record["local_path"])
            and is_video_source(record["local_path"])
        ):
            try:
                print(f"  🖼️   Rebuilding local fallback key frames for {record['source_name']}...")
                auto_chapters_payload = record.get("chapters", [])
                if not auto_chapters_payload and raw_auto_chapters_path.exists():
                    auto_chapters_payload = json.loads(raw_auto_chapters_path.read_text(encoding="utf-8"))
                fallback_manifest = extract_local_keyframes(
                    local_video_path=record["local_path"],
                    auto_chapters_payload=auto_chapters_payload,
                    keyframe_dir=bundle_dir / "keyframes",
                )
                if fallback_manifest.get("keyframes"):
                    keyframe_manifest = fallback_manifest
                    record["keyframe_count"] = len(fallback_manifest["keyframes"])
                    write_json(bundle_dir / "metadata.json", record)
            except Exception as exc:
                keyframe_manifest["status"] = "fallback_failed"
                keyframe_manifest["reason"] = str(exc)

        if keyframes_manifest_path.parent.exists():
            write_json(keyframes_manifest_path, keyframe_manifest)

        knowledge_base_text = ""
        knowledge_base_path = bundle_dir / "knowledge_base.md"
        if knowledge_base_path.exists():
            knowledge_base_text = knowledge_base_path.read_text(encoding="utf-8")

        rag_chunks = load_jsonl_rows(bundle_dir / "rag_chunks.jsonl")
        if not rag_chunks:
            payloads = load_bundle_payloads(bundle_dir)
            retrieval_chunks = load_jsonl_rows(bundle_dir / "retrieval_chunks.jsonl")
            if retrieval_chunks:
                rag_chunks = build_rag_chunks(record, payloads, retrieval_chunks)
                write_jsonl(bundle_dir / "rag_chunks.jsonl", rag_chunks)

        write_text(
            bundle_dir / "bundle_rag.md",
            build_bundle_rag_markdown(
                record,
                None,
                rag_chunks,
                knowledge_overview_text=knowledge_base_text,
            ),
        )
        write_json(bundle_dir / "metadata.json", record)
        write_text(bundle_dir / "bundle_master.md", build_bundle_master_markdown(bundle_dir, record))

    write_text(root / "library_master.md", build_library_master_markdown(records))
    write_jsonl(root / "library_rag.jsonl", collect_library_rag_chunks(records))
    write_text(root / "library_rag.md", build_library_rag_markdown(records))
    cleanup_output_root(root)
    return records


def main():
    parser = argparse.ArgumentParser(
        prog="build_knowledge_base",
        description="Build AI-ready knowledge bundles from Tingwu transcript results.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python build_knowledge_base.py -l /Users/me/Downloads/lesson.mp4 -o /Users/me/KB\n\n"
            "  python build_knowledge_base.py -d /Users/me/Downloads/course_videos "
            "-o /Users/me/KB --recursive\n\n"
            "Environment variables:\n"
            "  ALIYUN_ACCESS_KEY_ID\n"
            "  ALIYUN_ACCESS_KEY_SECRET\n"
            "  ALIYUN_TINGWU_APP_KEY\n"
            "  ALIYUN_OSS_ENDPOINT\n"
            "  ALIYUN_OSS_BUCKET_NAME"
        ),
    )

    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("-u", "--url", metavar="MEDIA_URL", help="Public media URL")
    source_group.add_argument("-l", "--local_file", metavar="LOCAL_FILE", help="Local media file")
    source_group.add_argument("-d", "--local_dir", metavar="LOCAL_DIR", help="Local media directory")
    source_group.add_argument(
        "--rebuild-existing",
        metavar="OUTPUT_ROOT",
        help="Rebuild bundle/library master markdown and local fallback key frames from an existing output directory.",
    )

    parser.add_argument(
        "-o",
        "--output_dir",
        metavar="OUTPUT_DIR",
        help="Output root directory. Defaults to ./knowledge_base_output for a single source, or INPUT_DIR/knowledge_base for directory mode.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively scan sub-directories in directory mode.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process the first N sorted files in directory mode. 0 means no limit.",
    )
    parser.add_argument(
        "--chunk-max-chars",
        type=int,
        default=DEFAULT_CHUNK_MAX_CHARS,
        help="Maximum approximate characters per retrieval chunk.",
    )
    parser.add_argument(
        "--keep-intermediate-files",
        action="store_true",
        help="Keep debug/intermediate files such as raw JSON and non-RAG chunk dumps.",
    )

    args = parser.parse_args()

    if args.rebuild_existing:
        output_root = args.rebuild_existing
    elif args.local_dir:
        output_root = args.output_dir or os.path.join(args.local_dir, "knowledge_base")
    else:
        output_root = args.output_dir or os.path.join(os.getcwd(), "knowledge_base_output")

    ensure_directory(output_root)

    print("\n" + "=" * 72)
    print("  AI Knowledge Bundle Builder")
    print("=" * 72)
    print(f"  Output Root: {output_root}")
    print("=" * 72 + "\n")

    if args.rebuild_existing:
        records = rebuild_existing_output(output_root)
        print("\n" + "=" * 72)
        print(f"  Rebuilt existing output. Bundle count: {len(records)}")
        print(f"  Library master markdown: {Path(output_root) / 'library_master.md'}")
        print(f"  Library RAG markdown: {Path(output_root) / 'library_rag.md'}")
        print(f"  Library RAG JSONL: {Path(output_root) / 'library_rag.jsonl'}")
        print("=" * 72 + "\n")
        return

    client = _build_client()
    records = []

    if args.url:
        source_name = Path(urlparse(args.url).path).name or "remote_source"
        bundle_dir = build_bundle_dir(output_root, source_name)
        records.append(
            process_source(
                client,
                source_label=source_name,
                bundle_dir=bundle_dir,
                media_url=args.url,
                chunk_max_chars=args.chunk_max_chars,
            )
        )
    elif args.local_file:
        if not os.path.isfile(args.local_file):
            parser.error(f"Local file does not exist: {args.local_file}")
        if Path(args.local_file).suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
            parser.error(f"Unsupported media type: {args.local_file}")

        source_name = os.path.basename(args.local_file)
        bundle_dir = build_bundle_dir(output_root, source_name)
        records.append(
            process_source(
                client,
                source_label=source_name,
                bundle_dir=bundle_dir,
                local_path=args.local_file,
                chunk_max_chars=args.chunk_max_chars,
                keep_intermediate_files=args.keep_intermediate_files,
            )
        )
    else:
        media_files = collect_local_media_files(args.local_dir, recursive=args.recursive)
        if not media_files:
            parser.error(
                "No supported media files found in the directory. "
                f"Supported extensions: {', '.join(sorted(SUPPORTED_MEDIA_EXTENSIONS))}"
            )

        if args.limit > 0:
            media_files = media_files[: args.limit]

        print(f"  Files to process: {len(media_files)}")
        for index, local_path in enumerate(media_files, start=1):
            bundle_dir = build_bundle_dir(output_root, local_path, source_root=args.local_dir)
            print("\n" + "-" * 72)
            print(f"  [{index}/{len(media_files)}] Building bundle for {os.path.basename(local_path)}")
            print(f"  Bundle Dir: {bundle_dir}")
            print("-" * 72)

            try:
                records.append(
                    process_source(
                        client,
                        source_label=os.path.basename(local_path),
                        bundle_dir=bundle_dir,
                        local_path=local_path,
                        chunk_max_chars=args.chunk_max_chars,
                        keep_intermediate_files=args.keep_intermediate_files,
                    )
                )
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"  ❌  Failed to process {local_path}: {exc}")

    write_text(Path(output_root) / "library_master.md", build_library_master_markdown(records))
    write_jsonl(Path(output_root) / "library_rag.jsonl", collect_library_rag_chunks(records))
    write_text(Path(output_root) / "library_rag.md", build_library_rag_markdown(records))
    if not args.keep_intermediate_files:
        cleanup_output_root(output_root)

    print("\n" + "=" * 72)
    print(f"  Completed. Generated {len(records)} bundle(s).")
    print(f"  Library master markdown: {Path(output_root) / 'library_master.md'}")
    print(f"  Library RAG markdown: {Path(output_root) / 'library_rag.md'}")
    print(f"  Library RAG JSONL: {Path(output_root) / 'library_rag.jsonl'}")
    print("=" * 72 + "\n")


if __name__ == "__main__":
    main()
