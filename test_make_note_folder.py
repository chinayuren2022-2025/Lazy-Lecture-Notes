from pathlib import Path

import make_note


def test_collect_local_media_files_filters_supported_files(tmp_path):
    input_dir = tmp_path / "lectures"
    input_dir.mkdir()
    (input_dir / "lesson1.mp4").write_text("video")
    (input_dir / "lesson2.MP3").write_text("audio")
    (input_dir / "notes.txt").write_text("ignore me")
    (input_dir / "slides.pdf").write_text("ignore me too")

    media_files = make_note.collect_local_media_files(str(input_dir))

    assert media_files == [
        str(input_dir / "lesson1.mp4"),
        str(input_dir / "lesson2.MP3"),
    ]


def test_collect_local_media_files_recursive_finds_nested_files(tmp_path):
    input_dir = tmp_path / "lectures"
    nested_dir = input_dir / "week1"
    nested_dir.mkdir(parents=True)
    (nested_dir / "lesson1.wav").write_text("audio")

    assert make_note.collect_local_media_files(str(input_dir), recursive=False) == []
    assert make_note.collect_local_media_files(str(input_dir), recursive=True) == [
        str(nested_dir / "lesson1.wav")
    ]


def test_collect_local_media_files_uses_natural_filename_order(tmp_path):
    input_dir = tmp_path / "lectures"
    input_dir.mkdir()
    (input_dir / "创业管理_第10节.mp4").write_text("video")
    (input_dir / "创业管理_第2节.mp4").write_text("video")
    (input_dir / "创业管理_第1节.mp4").write_text("video")

    media_files = make_note.collect_local_media_files(str(input_dir))

    assert [Path(path).name for path in media_files] == [
        "创业管理_第1节.mp4",
        "创业管理_第2节.mp4",
        "创业管理_第10节.mp4",
    ]


def test_build_batch_output_path_preserves_relative_structure(tmp_path):
    input_dir = tmp_path / "lectures"
    output_dir = tmp_path / "notes"
    local_path = input_dir / "week2" / "lesson3.mp4"

    output_path = make_note.build_batch_output_path(
        str(input_dir),
        str(local_path),
        str(output_dir),
    )

    assert output_path == str(output_dir / "week2" / "lesson3.md")


def test_process_local_directory_continues_after_failure(tmp_path, monkeypatch):
    input_dir = tmp_path / "lectures"
    input_dir.mkdir()
    first_file = input_dir / "lesson1.mp4"
    second_file = input_dir / "lesson2.mp3"
    first_file.write_text("video")
    second_file.write_text("audio")

    processed = []

    def fake_process_local_file(client, local_path, md_file):
        processed.append((local_path, md_file))
        if Path(local_path).name == "lesson2.mp3":
            raise RuntimeError("simulated failure")

    monkeypatch.setattr(make_note, "process_local_file", fake_process_local_file)

    failures = make_note.process_local_directory(
        object(),
        str(input_dir),
        str(tmp_path / "notes"),
    )

    assert [Path(path).name for path, _ in failures] == ["lesson2.mp3"]
    assert processed == [
        (
            str(first_file),
            str(tmp_path / "notes" / "lesson1.md"),
        ),
        (
            str(second_file),
            str(tmp_path / "notes" / "lesson2.md"),
        ),
    ]
