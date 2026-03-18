import json
from pathlib import Path

import build_knowledge_base as kb


def test_parse_transcription_payload_extracts_paragraphs_and_sentences():
    payload = {
        "Transcription": {
            "AudioInfo": {"Duration": 9000, "Language": "cn", "SampleRate": 16000},
            "Paragraphs": [
                {
                    "ParagraphId": 1,
                    "SpeakerId": 1,
                    "Words": [
                        {"Text": "大家", "Start": 0, "End": 200, "SentenceId": 1},
                        {"Text": "好。", "Start": 200, "End": 400, "SentenceId": 1},
                        {"Text": "今天", "Start": 500, "End": 700, "SentenceId": 2},
                        {"Text": "上课。", "Start": 700, "End": 900, "SentenceId": 2},
                    ],
                },
                {
                    "ParagraphId": 2,
                    "SpeakerId": 2,
                    "Words": [
                        {"Text": "这是", "Start": 1000, "End": 1200, "SentenceId": 3},
                        {"Text": "第二段。", "Start": 1200, "End": 1500, "SentenceId": 3},
                    ],
                },
            ],
        }
    }

    result = kb.parse_transcription_payload(payload)

    assert result["audio_info"]["Duration"] == 9000
    assert [paragraph["text"] for paragraph in result["paragraphs"]] == [
        "大家好。今天上课。",
        "这是第二段。",
    ]
    assert [sentence["text"] for sentence in result["sentences"]] == [
        "大家好。",
        "今天上课。",
        "这是第二段。",
    ]


def test_build_retrieval_chunks_merges_nearby_paragraphs():
    paragraphs = [
        {"paragraph_index": 1, "start_ms": 0, "end_ms": 1000, "speaker_id": 1, "text": "A" * 5},
        {"paragraph_index": 2, "start_ms": 1000, "end_ms": 2000, "speaker_id": 1, "text": "B" * 5},
        {"paragraph_index": 3, "start_ms": 2000, "end_ms": 3000, "speaker_id": 2, "text": "C" * 5},
    ]

    chunks = kb.build_retrieval_chunks(paragraphs, max_chars=12)

    assert [chunk["paragraph_indices"] for chunk in chunks] == [[1, 2], [3]]
    assert chunks[0]["speaker_ids"] == [1]
    assert chunks[1]["speaker_ids"] == [2]


def test_build_frame_targets_uses_chapter_starts():
    payload = {
        "AutoChapters": [
            {"Id": 1, "Start": 0, "End": 10000, "Headline": "第一章", "Summary": "简介"},
            {"Id": 2, "Start": 10000, "End": 20000, "Headline": "第二章", "Summary": "方法"},
        ]
    }

    targets = kb.build_frame_targets(payload, max_frames=12, offset_ms=2000)

    assert [target["headline"] for target in targets] == ["第一章", "第二章"]
    assert [target["capture_ms"] for target in targets] == [2000, 12000]


def test_build_bundle_dir_preserves_relative_structure(tmp_path):
    output_root = tmp_path / "kb"
    source_root = tmp_path / "videos"
    source_path = source_root / "week1" / "lesson01.mp4"

    bundle_dir = kb.build_bundle_dir(
        str(output_root),
        str(source_path),
        source_root=str(source_root),
    )

    assert bundle_dir == str(output_root / "week1" / "lesson01")


def test_extract_custom_prompt_text_prefers_named_entry():
    payload = {
        "CustomPrompt": [
            {"Name": "别的结果", "Result": "ignore"},
            {"Name": "知识库整理", "Result": "keep me"},
        ]
    }

    assert kb.extract_custom_prompt_text(payload) == "keep me"


def test_build_kb_markdown_includes_keyframe_section():
    markdown = kb.build_kb_markdown(
        source_name="lesson01.mp4",
        task_id="task-123",
        transcript_info={"audio_info": {"Duration": 6000}, "paragraphs": [{"text": "foo"}]},
        payloads={
            "MeetingAssistance": {"MeetingAssistance": {"Keywords": ["创业", "管理"]}},
            "AutoChapters": {"AutoChapters": [{"Headline": "导论", "Summary": "课程介绍"}]},
            "CustomPrompt": {"CustomPrompt": [{"Name": "知识库整理", "Result": "## 核心内容"}]},
            "Summarization": {"Summarization": {"ParagraphSummary": "总结"}},
        },
        keyframe_manifest={
            "content": "PPT摘要",
            "pdf_path": None,
            "keyframes": [{"index": 1, "summary": "封面页", "local_path": str(Path("keyframes/001.png"))}],
        },
    )

    assert "## Structured Knowledge Notes" in markdown
    assert "## PPT / Key Frames" in markdown
    assert "封面页" in markdown


def test_build_rag_chunks_attaches_chapter_context():
    metadata = {
        "source_name": "lesson01.mp4",
        "task_id": "task-1",
        "bundle_dir": "/tmp/lesson01",
    }
    payloads = {
        "AutoChapters": {
            "AutoChapters": [
                {"Id": 1, "Start": 0, "End": 10000, "Headline": "导论"},
                {"Id": 2, "Start": 10001, "End": 20000, "Headline": "方法"},
            ]
        },
        "MeetingAssistance": {"MeetingAssistance": {"Keywords": ["创业", "融资"]}},
    }
    retrieval_chunks = [
        {
            "chunk_index": 1,
            "start_ms": 500,
            "end_ms": 9000,
            "speaker_ids": [1],
            "paragraph_indices": [1, 2],
            "text": "chunk one",
        }
    ]

    rag_chunks = kb.build_rag_chunks(metadata, payloads, retrieval_chunks)

    assert rag_chunks[0]["chapter_titles"] == ["导论"]
    assert rag_chunks[0]["keywords"] == ["创业", "融资"]
    assert rag_chunks[0]["chunk_id"].endswith("001")


def test_build_bundle_rag_markdown_contains_chunk_block():
    metadata = {
        "source_name": "lesson01.mp4",
        "task_id": "task-1",
        "generated_at": "2026-03-15T10:00:00",
        "transcript_paragraph_count": 3,
        "retrieval_chunk_count": 1,
        "keyframe_count": 0,
    }
    payloads = {
        "AutoChapters": {"AutoChapters": [{"Id": 1, "Start": 0, "End": 10000, "Headline": "导论"}]},
        "MeetingAssistance": {"MeetingAssistance": {"Keywords": ["创业"]}},
        "CustomPrompt": {"CustomPrompt": [{"Name": "知识库整理", "Result": "## 摘要"}]},
    }
    rag_chunks = [
        {
            "chunk_id": "lesson01.mp4::chunk::001",
            "source_name": "lesson01.mp4",
            "task_id": "task-1",
            "time_range": "00:00:00-00:00:10",
            "chapter_titles": ["导论"],
            "speaker_ids": [1],
            "paragraph_indices": [1, 2],
            "keywords": ["创业"],
            "text": "chunk text",
        }
    ]

    markdown = kb.build_bundle_rag_markdown(metadata, payloads, rag_chunks)

    assert "# RAG Source Pack - lesson01.mp4" in markdown
    assert "### CHUNK lesson01.mp4::chunk::001" in markdown
    assert "chunk text" in markdown


def test_build_bundle_and_library_master_markdown(tmp_path):
    bundle_dir = tmp_path / "lesson01"
    bundle_dir.mkdir()
    (bundle_dir / "knowledge_base.md").write_text("# KB\n\n知识点", encoding="utf-8")
    (bundle_dir / "transcript_full.md").write_text("# Transcript\n\n全文", encoding="utf-8")
    metadata = {
        "source_name": "lesson01.mp4",
        "task_id": "task-1",
        "generated_at": "2026-03-15T10:00:00",
        "transcript_paragraph_count": 3,
        "retrieval_chunk_count": 2,
        "keyframe_count": 1,
        "bundle_dir": str(bundle_dir),
    }

    bundle_master = kb.build_bundle_master_markdown(bundle_dir, metadata)
    (bundle_dir / "bundle_master.md").write_text(bundle_master, encoding="utf-8")
    library_master = kb.build_library_master_markdown([metadata])

    assert "## Structured Knowledge" in bundle_master
    assert "知识点" in bundle_master
    assert "全文" in bundle_master
    assert "lesson01.mp4" in library_master
    assert "Bundle Count: 1" in library_master


def test_rebuild_existing_output_generates_library_master(tmp_path):
    output_root = tmp_path / "kb"
    bundle_dir = output_root / "lesson01"
    raw_dir = bundle_dir / "raw"
    raw_dir.mkdir(parents=True)

    metadata = {
        "source_name": "lesson01.mp4",
        "task_id": "task-1",
        "generated_at": "2026-03-15T10:00:00",
        "transcript_paragraph_count": 3,
        "retrieval_chunk_count": 2,
        "keyframe_count": 0,
        "bundle_dir": str(bundle_dir),
        "local_path": None,
    }
    (bundle_dir / "knowledge_base.md").write_text("# KB\n\n知识点", encoding="utf-8")
    (bundle_dir / "transcript_full.md").write_text("# Transcript\n\n全文", encoding="utf-8")
    (bundle_dir / "keyframes_manifest.json").write_text(
        '{"status":"tingwu_empty","reason":"empty","content":"","pdf_path":null,"keyframes":[]}',
        encoding="utf-8",
    )
    (raw_dir / "AutoChapters.json").write_text('{"AutoChapters":[]}', encoding="utf-8")
    (output_root / "library_index.json").write_text(
        json.dumps([metadata], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    records = kb.rebuild_existing_output(output_root)

    assert len(records) == 1
    assert (bundle_dir / "bundle_master.md").exists()
    assert (output_root / "library_master.md").exists()
    assert (bundle_dir / "bundle_rag.md").exists()
    assert (output_root / "library_rag.md").exists()
    assert (output_root / "library_rag.jsonl").exists()


def test_cleanup_output_root_removes_intermediate_files(tmp_path):
    output_root = tmp_path / "kb"
    bundle_dir = output_root / "lesson01"
    raw_dir = bundle_dir / "raw"
    raw_dir.mkdir(parents=True)

    (output_root / "library_index.json").write_text("{}", encoding="utf-8")
    (output_root / "library_master.md").write_text("keep", encoding="utf-8")
    (bundle_dir / "transcript_paragraphs.jsonl").write_text("{}", encoding="utf-8")
    (bundle_dir / "retrieval_chunks.jsonl").write_text("{}", encoding="utf-8")
    (bundle_dir / "bundle_rag.md").write_text("keep", encoding="utf-8")
    (raw_dir / "AutoChapters.json").write_text("{}", encoding="utf-8")

    kb.cleanup_output_root(output_root)

    assert not (output_root / "library_index.json").exists()
    assert (output_root / "library_master.md").exists()
    assert not (bundle_dir / "transcript_paragraphs.jsonl").exists()
    assert not (bundle_dir / "retrieval_chunks.jsonl").exists()
    assert not raw_dir.exists()
    assert (bundle_dir / "bundle_rag.md").exists()
