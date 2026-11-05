# SPDX-License-Identifier: AGPL-3.0-or-later
"""A speech recognition model (transformers ``AutoModelForSpeechSeq2Seq``,
such as Whisper): the text spoken in WAV audio, read in windows of the
length the model hears at once."""

import io
import re
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple

import numpy as np
import numpy.typing as npt
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ai.EXT_AI import TRANSCRIPTION
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.local_ai.LocalAI import bounded_number
from zephyrex.extensions.local_ai_torch.Torch import (
    PRETRAINED,
    TORCH_SETTINGS,
    AbstractTorchProvider,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DEFAULT_MAX_AUDIO_SECONDS = 600.0
MAX_AUDIO_SECONDS_CEILING = 4 * 3600.0
DEFAULT_WINDOW_SECONDS = 30.0
# Room left in the decoder for the tokens it is started with (language,
# task, timestamps).
DECODER_PROMPT_TOKENS = 8
_LANGUAGE = re.compile(r"^[A-Za-z]{2,20}$")
_SAMPLE_TYPES = {1: np.uint8, 2: np.int16, 4: np.int32}

Samples = npt.NDArray[np.float32]


@dataclass(frozen=True)
class SpeechModel:
    model: Any
    processor: Any


def wav_samples(audio: bytes) -> Tuple[Samples, int]:
    """WAV (PCM) audio as mono samples in [-1, 1], and its sample rate."""
    try:
        with wave.open(io.BytesIO(audio)) as reader:
            width, channels = reader.getsampwidth(), reader.getnchannels()
            rate, frames = reader.getframerate(), reader.readframes(reader.getnframes())
    except (wave.Error, EOFError) as exc:
        raise InvalidInputExternalError(
            "local transcription reads WAV (PCM) audio"
        ) from exc
    if width not in _SAMPLE_TYPES or channels < 1 or rate < 1:
        raise InvalidInputExternalError("WAV audio is 8, 16 or 32-bit PCM")
    raw = np.frombuffer(frames, dtype=_SAMPLE_TYPES[width]).astype(np.float64)
    if width == 1:
        raw = raw - 128.0
    scale = float(2 ** (8 * width - 1))
    samples = (raw / scale).reshape(-1, channels).mean(axis=1)
    return samples.astype(np.float32), rate


def resampled(samples: Samples, rate: int, target: int) -> Samples:
    """``samples`` at ``target`` Hz, linearly interpolated."""
    if rate == target or not len(samples):
        return samples
    duration = len(samples) / rate
    times = np.arange(int(duration * target)) / target
    found = np.interp(times, np.arange(len(samples)) / rate, samples)
    return found.astype(np.float32)


def checked_language(language: Optional[str]) -> Optional[str]:
    if language is not None and not _LANGUAGE.match(language):
        raise InvalidInputExternalError("language is a language code or name")
    return language.lower() if language else None


class PRV_Torch_Speech(AbstractTorchProvider):
    name: ClassVar[str] = "torch_speech_recognition"
    friendly_name: ClassVar[str] = "PyTorch speech recognition model"
    description: ClassVar[str] = "Transcription with a transformers speech model"
    _abilities: ClassVar[Set[str]] = {TRANSCRIPTION}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        *TORCH_SETTINGS,
        InstanceSetting(
            "max_audio_seconds", "The longest audio one call may send", default="600"
        ),
    )

    @classmethod
    def load_model(cls, instance: ProviderInstanceModel, directory: Path) -> Any:
        model = cls.pretrained(AutoModelForSpeechSeq2Seq, instance, directory)
        return SpeechModel(
            model, AutoProcessor.from_pretrained(directory, **PRETRAINED)
        )

    @classmethod
    def audio_limit(cls, instance: ProviderInstanceModel) -> float:
        return bounded_number(
            cls.setting(instance, "max_audio_seconds"),
            DEFAULT_MAX_AUDIO_SECONDS,
            MAX_AUDIO_SECONDS_CEILING,
            "max_audio_seconds",
            cls.name,
        )

    @classmethod
    async def transcribe(
        cls,
        instance: ProviderInstanceModel,
        audio: bytes,
        filename: str,
        language: Optional[str],
    ) -> Dict[str, Any]:
        samples, rate = wav_samples(audio)
        limit = cls.audio_limit(instance)
        if len(samples) / rate > limit:
            raise InvalidInputExternalError(f"audio is at most {limit:g} seconds")
        chosen = checked_language(language)
        max_tokens = cls.max_tokens(instance, None)
        seconds = cls.timeout_seconds(instance)
        model_name = cls.model(instance)

        def work(value: Any) -> Dict[str, Any]:
            if not isinstance(value, SpeechModel):
                raise TypeError(f"{cls.name} holds a {type(value).__name__}")
            return {
                "text": transcribed(value, samples, rate, chosen, max_tokens, seconds),
                "model": model_name,
            }

        return await cls.run(instance, work)


def transcribed(
    speech: SpeechModel,
    samples: Samples,
    rate: int,
    language: Optional[str],
    max_tokens: int,
    seconds: float,
) -> str:
    """The text in ``samples``, window by window, within ``seconds``."""
    extractor = speech.processor.feature_extractor
    target = int(extractor.sampling_rate)
    audio = resampled(samples, rate, target)
    window_seconds = float(getattr(extractor, "chunk_length", DEFAULT_WINDOW_SECONDS))
    window = int(window_seconds * target)
    decoder_limit = getattr(speech.model.config, "max_target_positions", None)
    if isinstance(decoder_limit, int):
        max_tokens = min(max_tokens, decoder_limit - DECODER_PROMPT_TOKENS)
    options: Dict[str, Any] = {"task": "transcribe"}
    if language:
        options["language"] = language
    deadline = time.monotonic() + seconds
    texts: List[str] = []
    for start in range(0, max(len(audio), 1), window):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        features = speech.processor(
            audio[start : start + window], sampling_rate=target, return_tensors="pt"
        ).input_features.to(speech.model.device, speech.model.dtype)
        try:
            with torch.inference_mode():
                ids = speech.model.generate(
                    features, max_new_tokens=max_tokens, max_time=remaining, **options
                )
        except ValueError as exc:
            raise InvalidInputExternalError(
                f"the model refused the request: {exc}"
            ) from exc
        texts.append(speech.processor.batch_decode(ids, skip_special_tokens=True)[0])
    return " ".join(text.strip() for text in texts if text.strip())
