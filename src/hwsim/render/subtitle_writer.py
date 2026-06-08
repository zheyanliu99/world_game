from __future__ import annotations

from pathlib import Path

from hwsim.core.models import SubtitleEntry


def format_srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    whole_milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(whole_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(subtitles: list[SubtitleEntry], output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for index, subtitle in enumerate(subtitles, start=1):
        lines.extend(
            [
                str(index),
                f"{format_srt_timestamp(subtitle.start_seconds)} --> {format_srt_timestamp(subtitle.end_seconds)}",
                subtitle.text,
                "",
            ]
        )
    output_path.write_text("\n".join(lines), encoding="utf-8")

