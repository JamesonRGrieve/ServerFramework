# SPDX-License-Identifier: AGPL-3.0-or-later
"""Captions from a timed transcription (``EXT_AI.transcribe_timed``'s
segments): WebVTT for browsers and players, SubRip (SRT) for editors.

A cue's text is the segment's, with the characters each format reserves
escaped, so a transcript can never inject markup or a cue into the file.
"""

from typing import Any, Literal

CaptionFormat = Literal["vtt", "srt"]
CAPTION_FORMATS: tuple[CaptionFormat, ...] = ("vtt", "srt")
CONTENT_TYPES: dict[CaptionFormat, str] = {
    "vtt": "text/vtt; charset=utf-8",
    "srt": "application/x-subrip; charset=utf-8",
}
MILLISECONDS_PER_SECOND = 1000
SECONDS_PER_HOUR = 3600
SECONDS_PER_MINUTE = 60


def _timestamp(seconds: float, decimal_mark: str) -> str:
    total_ms = max(0, round(seconds * MILLISECONDS_PER_SECOND))
    whole, ms = divmod(total_ms, MILLISECONDS_PER_SECOND)
    hours, rest = divmod(whole, SECONDS_PER_HOUR)
    minutes, secs = divmod(rest, SECONDS_PER_MINUTE)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{decimal_mark}{ms:03d}"


def _cue_text(text: str, caption_format: CaptionFormat) -> str:
    """One cue's text on a single paragraph: a blank line would end the cue
    (and ``-->`` start another), so line breaks collapse to spaces."""
    flat = " ".join(text.split())
    if caption_format == "vtt":
        flat = flat.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return flat.replace("-->", "->")


def render_captions(
    segments: list[dict[str, Any]], caption_format: CaptionFormat
) -> str:
    """``segments`` (``{start, end, text}``, seconds) as a caption file. Empty
    segments are left out; cues are numbered from 1 in time order."""
    if caption_format not in CAPTION_FORMATS:
        raise ValueError(f"caption format is one of {', '.join(CAPTION_FORMATS)}")
    decimal_mark = "." if caption_format == "vtt" else ","
    cues = [
        segment
        for segment in sorted(segments, key=lambda s: float(s["start"]))
        if str(segment.get("text", "")).strip()
    ]
    blocks = [
        f"{number}\n"
        f"{_timestamp(float(cue['start']), decimal_mark)} --> "
        f"{_timestamp(max(float(cue['end']), float(cue['start'])), decimal_mark)}\n"
        f"{_cue_text(str(cue['text']), caption_format)}"
        for number, cue in enumerate(cues, start=1)
    ]
    header = ["WEBVTT"] if caption_format == "vtt" else []
    return "\n\n".join(header + blocks) + "\n"
