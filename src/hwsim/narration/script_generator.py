from __future__ import annotations

from hwsim.core.models import ScenarioConfig, SubtitleEntry, TriggeredEvent


def year_to_seconds(scenario: ScenarioConfig, year: int) -> float:
    span = max(1, scenario.end_year - scenario.start_year)
    progress = (year - scenario.start_year) / span
    return progress * scenario.video_length_seconds


def schedule_events(
    scenario: ScenarioConfig,
    events: list[TriggeredEvent],
) -> list[TriggeredEvent]:
    scheduled = [event.model_copy(deep=True) for event in sorted(events, key=lambda item: (item.year, -item.importance))]
    previous_end = 5.5
    for event in scheduled:
        natural_start = year_to_seconds(scenario, event.year)
        start = max(natural_start, previous_end + 0.3)
        if start + event.duration_seconds > scenario.video_length_seconds - 1:
            start = max(0.0, scenario.video_length_seconds - event.duration_seconds - 1)
        event.start_seconds = round(start, 3)
        event.end_seconds = round(start + event.duration_seconds, 3)
        previous_end = event.end_seconds
    return scheduled


def build_subtitles(
    scenario: ScenarioConfig,
    events: list[TriggeredEvent],
) -> list[SubtitleEntry]:
    subtitles = [
        SubtitleEntry(
            start_seconds=0.0,
            end_seconds=min(5.0, scenario.video_length_seconds),
            text=scenario.intro_narration,
        )
    ]
    for event in events:
        subtitles.append(
            SubtitleEntry(
                start_seconds=event.start_seconds,
                end_seconds=event.end_seconds,
                text=event.narration,
            )
        )
    return subtitles

