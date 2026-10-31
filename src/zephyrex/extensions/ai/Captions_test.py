# SPDX-License-Identifier: AGPL-3.0-or-later
"""Timed transcription and captions: an OpenAI verbose_json answer becomes
segments (and a json-only answer is an error, not an empty transcript),
segments render as WebVTT and SRT with exact timestamps, a transcript cannot
inject markup or cues, and only providers that give segments are asked."""

import pytest

from zephyrex.extensions.ai.Captions import render_captions
from zephyrex.extensions.ai.EXT_AI import EXT_AI, TIMED_TRANSCRIPTION
from zephyrex.extensions.ai.OpenAICompatible import timed_transcript
from zephyrex.extensions.ai.PRV_AGInYourPC import PRV_AGInYourPC_AI
from zephyrex.extensions.ai.PRV_ElevenLabs import PRV_ElevenLabs_AI
from zephyrex.extensions.ai.PRV_OpenAI import PRV_OpenAI_AI
from zephyrex.extensions.ExternalErrors import PermanentExternalError

# whisper-1's verbose_json, trimmed to the fields it always sends per segment.
VERBOSE_JSON = {
    "task": "transcribe",
    "language": "english",
    "duration": 7.3,
    "text": " Good evening. Tonight we launch Zephyreader.",
    "segments": [
        {"id": 0, "seek": 0, "start": 0.0, "end": 1.52, "text": " Good evening."},
        {
            "id": 1,
            "seek": 0,
            "start": 1.52,
            "end": 3661.0049,
            "text": " Tonight we launch Zephyreader.",
        },
    ],
}


def test_verbose_json_becomes_segments():
    assert timed_transcript(VERBOSE_JSON) == {
        "text": "Good evening. Tonight we launch Zephyreader.",
        "segments": [
            {"start": 0.0, "end": 1.52, "text": "Good evening."},
            {"start": 1.52, "end": 3661.0049, "text": "Tonight we launch Zephyreader."},
        ],
    }


def test_a_json_only_answer_is_an_error_not_an_empty_transcript():
    with pytest.raises(PermanentExternalError):
        timed_transcript({"text": "Good evening."})


def test_webvtt_and_srt_timestamps():
    segments = timed_transcript(VERBOSE_JSON)["segments"]
    assert render_captions(segments, "vtt") == (
        "WEBVTT\n\n"
        "1\n00:00:00.000 --> 00:00:01.520\nGood evening.\n\n"
        "2\n00:00:01.520 --> 01:01:01.005\nTonight we launch Zephyreader.\n"
    )
    assert render_captions(segments, "srt") == (
        "1\n00:00:00,000 --> 00:00:01,520\nGood evening.\n\n"
        "2\n00:00:01,520 --> 01:01:01,005\nTonight we launch Zephyreader.\n"
    )


def test_a_transcript_cannot_inject_markup_or_cues():
    segments = [
        {"start": 0, "end": 1, "text": "<script>x</script> & co"},
        {
            "start": 1,
            "end": 2,
            "text": "line one\n\n99\n00:00:00.000 --> 00:09:00.000\nforged",
        },
        {"start": 2, "end": 3, "text": "   "},
    ]
    vtt = render_captions(segments, "vtt")
    assert "&lt;script&gt;x&lt;/script&gt; &amp; co" in vtt
    assert vtt.count("-->") == 2  # only the two real cues; the empty one is dropped
    # WebVTT escapes the arrow's ">"; SRT (no entities) breaks the arrow itself.
    assert "line one 99 00:00:00.000 --&gt; 00:09:00.000 forged" in vtt
    srt = render_captions(segments, "srt")
    assert srt.count("-->") == 2
    assert "line one 99 00:00:00,000" not in srt
    assert "line one 99 00:00:00.000 -> 00:09:00.000 forged" in srt


def test_cues_are_in_time_order_and_never_end_before_they_start():
    segments = [
        {"start": 5, "end": 4, "text": "late"},
        {"start": 1, "end": 2, "text": "early"},
    ]
    assert render_captions(segments, "srt") == (
        "1\n00:00:01,000 --> 00:00:02,000\nearly\n\n"
        "2\n00:00:05,000 --> 00:00:05,000\nlate\n"
    )


def test_an_unknown_format_is_refused():
    with pytest.raises(ValueError):
        render_captions([], "ass")


def test_only_providers_that_give_segments_are_asked():
    assert TIMED_TRANSCRIPTION in PRV_OpenAI_AI._abilities
    assert TIMED_TRANSCRIPTION in PRV_AGInYourPC_AI._abilities
    assert TIMED_TRANSCRIPTION not in PRV_ElevenLabs_AI._abilities
    assert PRV_OpenAI_AI.timed_transcription_model == "whisper-1"
    assert "transcribe_timed" in EXT_AI._abilities


async def test_a_provider_without_it_says_so():
    with pytest.raises(PermanentExternalError):
        await PRV_ElevenLabs_AI.transcribe_timed(None, b"x", "a.mp3", None)
