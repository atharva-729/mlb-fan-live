"""How long after a play fans react, measured per stream.

Cross-correlates 5-second comment volume with play end times, weighting each
play by how much it moved win probability.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BUCKET_S = 5
MIN_OFFSET_S = -60
MAX_OFFSET_S = 180
# Share of the excess reaction that must have arrived by the reported tail lag.
TAIL_SHARE = 0.9


def _epoch_seconds(times: pd.Series) -> np.ndarray:
    return (times - pd.Timestamp(0, tz="UTC")).dt.total_seconds().to_numpy()


def reaction_curve(comment_times: pd.Series, play_ends: pd.Series, weights: pd.Series) -> pd.Series:
    """Weighted mean comments per 5-second bucket, by seconds since a play ended.

    Index is the bucket's start offset: 20 means comments posted 20-25s after
    the play's end time. Negative offsets are the run-up before the play ended.
    """
    times = np.sort(_epoch_seconds(comment_times))
    ends = _epoch_seconds(play_ends)
    w = weights.to_numpy(dtype=float)
    offsets = np.arange(MIN_OFFSET_S, MAX_OFFSET_S, BUCKET_S)

    curve = []
    for offset in offsets:
        counts = np.searchsorted(times, ends + offset + BUCKET_S) - np.searchsorted(times, ends + offset)
        curve.append(float((counts * w).sum() / w.sum()))
    return pd.Series(curve, index=pd.Index(offsets, name="offset_s"), name="comments_per_bucket")


def lag_stats(curve: pd.Series) -> dict[str, float]:
    """Peak lag and the lag by which ``TAIL_SHARE`` of the excess reaction has arrived.

    Excess is volume above the baseline, the mean over the run-up before the
    play ended. Both lags are bucket ends, in seconds after the play.
    """
    baseline = curve[curve.index < 0].mean()
    after = curve[curve.index >= 0]
    excess = (after - baseline).clip(lower=0)
    cumulative = excess.cumsum() / excess.sum()
    return {
        "baseline": float(baseline),
        "peak_lag_s": float(excess.idxmax() + BUCKET_S),
        "peak_x_baseline": float(after.max() / baseline),
        "tail_lag_s": float(cumulative[cumulative >= TAIL_SHARE].index[0] + BUCKET_S),
    }
