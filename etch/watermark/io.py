"""
Audio I/O for etchmark.

Native WAV/FLAC/OGG via soundfile (libsndfile). For lossy formats (MP3, AAC,
Opus, M4A) and anything else soundfile can't open, we shell out to ffmpeg
to transcode → 16-bit signed PCM WAV in memory, then decode that with
soundfile. This keeps the dependency surface small while still accepting
whatever an artist uploads.

All functions return mono float64 audio in [-1, 1] at the file's native
sample rate.
"""
from __future__ import annotations

import io
import shutil
import subprocess
from typing import Optional

import numpy as np
import soundfile as sf

# Formats soundfile/libsndfile handles directly — extensions only used as a
# hint; the actual probe is the open() call.
_NATIVE_EXTS = {".wav", ".flac", ".ogg", ".oga", ".aiff", ".aif", ".au"}


class AudioIOError(RuntimeError):
    """Raised when an audio file can't be decoded by any available backend."""


def _to_mono_float64(data: np.ndarray) -> np.ndarray:
    """Downmix to mono float64 in [-1, 1]. soundfile already returns floats."""
    if data.ndim == 2:
        data = data.mean(axis=1)
    return data.astype(np.float64, copy=False)


def _read_native(blob: bytes) -> tuple[np.ndarray, int]:
    """Try libsndfile directly. Raises if it can't open the blob."""
    with sf.SoundFile(io.BytesIO(blob)) as f:
        sr = f.samplerate
        data = f.read(dtype="float64", always_2d=False)
    return _to_mono_float64(data), sr


def _read_via_ffmpeg(blob: bytes) -> tuple[np.ndarray, int]:
    """Transcode to 16-bit mono PCM WAV via ffmpeg, then read with soundfile."""
    if shutil.which("ffmpeg") is None:
        raise AudioIOError(
            "this audio format requires ffmpeg, but ffmpeg is not on PATH"
        )
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-vn",            # no video
            "-ac", "1",       # mono
            "-f", "wav",
            "-acodec", "pcm_s16le",
            "pipe:1",
        ],
        input=blob, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()[-1:]
        raise AudioIOError(f"ffmpeg decode failed: {msg[0] if msg else '(no stderr)'}")
    data, sr = sf.read(io.BytesIO(proc.stdout), dtype="float64", always_2d=False)
    return _to_mono_float64(data), sr


def read_audio(blob: bytes, *, filename_hint: Optional[str] = None) -> tuple[np.ndarray, int]:
    """
    Decode `blob` into mono float64 audio + sample rate.

    Tries libsndfile first; falls back to ffmpeg for lossy formats. The
    `filename_hint` is only used to skip the native attempt for obviously
    non-native formats (saves a try/except round-trip on big MP3 uploads).

    Returns:
        (audio: float64 in [-1, 1], sample_rate: int)
    """
    if not blob:
        raise AudioIOError("empty audio blob")

    ext = ""
    if filename_hint:
        dot = filename_hint.rfind(".")
        if dot >= 0:
            ext = filename_hint[dot:].lower()

    if ext and ext not in _NATIVE_EXTS:
        return _read_via_ffmpeg(blob)

    try:
        return _read_native(blob)
    except (sf.LibsndfileError, RuntimeError):
        return _read_via_ffmpeg(blob)


def write_wav(audio: np.ndarray, sr: int, *, subtype: str = "PCM_16") -> bytes:
    """
    Encode mono `audio` (float, [-1, 1]) as a WAV blob. Default 16-bit PCM —
    plenty of headroom for the watermark whose peak delta is < −40 dB.
    """
    if audio.ndim != 1:
        raise ValueError("write_wav expects 1-D mono audio")
    buf = io.BytesIO()
    # libsndfile clips automatically; we clamp first to be explicit.
    clipped = np.clip(audio, -1.0, 1.0)
    sf.write(buf, clipped, sr, format="WAV", subtype=subtype)
    return buf.getvalue()
