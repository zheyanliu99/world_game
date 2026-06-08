from hwsim.core.models import SubtitleEntry
from hwsim.render.subtitle_writer import format_srt_timestamp, write_srt


def test_srt_timestamp_formatting() -> None:
    assert format_srt_timestamp(0) == "00:00:00,000"
    assert format_srt_timestamp(61.719) == "00:01:01,719"


def test_write_srt(tmp_path) -> None:
    output = tmp_path / "demo.srt"
    write_srt([SubtitleEntry(start_seconds=0, end_seconds=1.25, text="测试字幕")], output)

    assert "00:00:00,000 --> 00:00:01,250" in output.read_text(encoding="utf-8")

