"""
Spread-spectrum FFT watermark layer.

For each fixed-duration audio chunk, embed one bit by modulating the magnitudes
of a pseudorandomly chosen set of frequency bins inside a "watermark band"
(default 2–6 kHz). A `1` bit multiplies those bins' magnitudes by (1 + α);
a `0` bit by (1 − α). α is small (~0.005) so the change is well below
psychoacoustic salience — it's distributed energy, not a tone.

Detection takes the FFT of the chunk, looks at the same PRNG-selected bins,
and computes a correlation: sum of (log_magnitude_change · ±1_pattern). If
the correlation is positive the bit is 1; negative is 0. The magnitude of
the correlation is the soft-bit confidence used by sync.find_alignment().

Why this is robust:
  - Energy is spread across hundreds of bins, so lossy compression that
    quantizes individual bins still leaves the aggregate signal recoverable.
  - The PRNG seed is public (so anyone can verify) but the watermark is
    nonetheless very hard to remove without audible damage, because the
    selected bins are scattered.
  - Correlation detection is robust to LUFS normalization (multiplicative
    amplitude changes affect numerator and reference equally — see _ratio
    below).
"""
from __future__ import annotations

import hashlib

import numpy as np

# Public watermark "namespace" — seeds the PRNG. Changing this would invalidate
# all existing watermarks, so it's part of the on-wire protocol version.
DEFAULT_SEED = "ETCH_V1"

# Watermark band — 2–6 kHz is psychoacoustically forgiving and survives the
# sub-8kHz lowpass that some streaming codecs apply at low bitrates.
DEFAULT_BAND_HZ = (2000.0, 6000.0)

# Number of bins used per chunk. More bins → lower per-bit error rate via
# √N correlation gain. 1000 gives plenty of headroom against ffmpeg lossy
# pipelines (MP3/AAC/Opus) while keeping the watermark inaudible.
DEFAULT_BINS_PER_CHUNK = 1000

# Modulation strength α. Per-bin amplitude shift is (1 ± α) ≈ ±0.5 dB at
# α=0.06, distributed across the selected bins in a 4 kHz band. Tuned so that
# round-trip extraction survives MP3 128k / AAC 128k / Opus 128k re-encodes
# while the time-domain peak watermark delta stays below −40 dB.
DEFAULT_ALPHA = 0.06


def _bin_selection(seed: str, n_fft: int, sr: int,
                   band: tuple[float, float], n_bins: int) -> np.ndarray:
    """
    Deterministically pick `n_bins` distinct FFT bin indices inside `band`.

    Trim- AND resample-invariant: the seed depends only on the protocol
    parameters (seed, band, n_bins), not on n_fft or sr. As long as the
    decoder uses the same `chunk_seconds`, the bin indices always represent
    the same physical frequencies — which is what we need to survive the
    sample-rate change that codecs like Opus impose (everything → 48 kHz).
    """
    f_lo, f_hi = band
    bin_lo = max(1, int(np.floor(f_lo * n_fft / sr)))
    bin_hi = min(n_fft // 2, int(np.ceil(f_hi * n_fft / sr)))
    candidates = np.arange(bin_lo, bin_hi, dtype=np.int64)
    if candidates.size < n_bins:
        raise ValueError(
            f"watermark band {band} only has {candidates.size} bins at sr={sr} "
            f"n_fft={n_fft}, need {n_bins}"
        )

    h = hashlib.sha256(f"{seed}:bins:{f_lo}:{f_hi}:{n_bins}".encode()).digest()
    rng = np.random.default_rng(int.from_bytes(h[:8], "big"))
    # rng.choice over the candidate range — same seed + same range → same picks
    # whenever sr and chunk_seconds are consistent (e.g. chunk_seconds=1.0).
    return rng.choice(candidates, size=n_bins, replace=False)


def _bin_signs(seed: str, n_bins: int) -> np.ndarray:
    """
    Per-bin ±1 pattern: half the selected bins are "positive" carriers,
    half "negative". A `1` bit boosts positive bins and attenuates negative
    bins; a `0` bit does the reverse. Zero-mean in log-amplitude, so total
    chunk energy is preserved (loudness invariant). Trim-invariant for
    the same reason as _bin_selection.
    """
    h = hashlib.sha256(f"{seed}:signs".encode()).digest()
    rng = np.random.default_rng(int.from_bytes(h[:8], "big"))
    signs = np.where(rng.random(n_bins) < 0.5, 1.0, -1.0)
    return signs


def embed_chunk(chunk: np.ndarray, bit: int, sr: int, *,
                seed: str = DEFAULT_SEED, band: tuple[float, float] = DEFAULT_BAND_HZ,
                n_bins: int = DEFAULT_BINS_PER_CHUNK,
                alpha: float = DEFAULT_ALPHA) -> np.ndarray:
    """Embed a single bit in `chunk`. Returns a new chunk of the same shape."""
    if chunk.ndim != 1:
        raise ValueError("embed_chunk expects 1-D mono audio")
    n = chunk.size
    spec = np.fft.rfft(chunk)
    bins = _bin_selection(seed, n, sr, band, n_bins)
    signs = _bin_signs(seed, n_bins)

    # Bit 1 → multiply selected magnitudes by (1 + alpha*signs);
    # Bit 0 → multiply by (1 - alpha*signs). Phase preserved.
    bit_sign = 1.0 if bit else -1.0
    factor = 1.0 + alpha * bit_sign * signs
    spec[bins] = spec[bins] * factor
    return np.fft.irfft(spec, n=n).astype(chunk.dtype, copy=False)


def detect_chunk(chunk: np.ndarray, sr: int, *,
                 seed: str = DEFAULT_SEED, band: tuple[float, float] = DEFAULT_BAND_HZ,
                 n_bins: int = DEFAULT_BINS_PER_CHUNK) -> float:
    """
    Detect the watermark bit in `chunk`. Returns a soft estimate:
        > 0  → bit was 1 (with magnitude as confidence)
        < 0  → bit was 0
        ≈ 0  → no signal / ambiguous

    The estimate is the correlation between the per-bin sign pattern and
    the deviation of each bin's log-magnitude from the local median.
    Subtracting the median makes detection invariant to LUFS gain changes
    and large-scale spectral shaping, both of which platforms apply.
    """
    if chunk.ndim != 1:
        raise ValueError("detect_chunk expects 1-D mono audio")
    n = chunk.size
    spec = np.fft.rfft(chunk)
    bins = _bin_selection(seed, n, sr, band, n_bins)
    signs = _bin_signs(seed, n_bins)

    mags = np.abs(spec[bins])
    # log-magnitude — converts the multiplicative watermark into additive,
    # and is what the human auditory system roughly cares about anyway.
    log_mags = np.log(mags + 1e-12)
    # Detrend against the median of the *whole watermark band* (not just the
    # selected bins), so that broad spectral changes from EQ don't bias the
    # correlation.
    f_lo, f_hi = band
    bin_lo = max(1, int(np.floor(f_lo * n / sr)))
    bin_hi = min(n // 2, int(np.ceil(f_hi * n / sr)))
    band_log_mags = np.log(np.abs(spec[bin_lo:bin_hi]) + 1e-12)
    baseline = np.median(band_log_mags)
    deviations = log_mags - baseline

    # Correlation: positive iff selected bins move with `signs` (=> bit 1).
    return float(np.dot(deviations, signs) / n_bins)
