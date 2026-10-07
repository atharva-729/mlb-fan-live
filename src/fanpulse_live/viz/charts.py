"""Plotly charts for the phase checkpoints, written as standalone HTML under ``reports/``."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from fanpulse_live import config

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

# One fixed color per stream, in config order, so a stream keeps its color in every chart.
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]


def stream_colors(stream_ids: list[str]) -> dict[str, str]:
    return dict(zip(stream_ids, SERIES_COLORS))


def reports_dir() -> Path:
    return config.PROJECT_ROOT / "reports"


def write_html(figure: go.Figure, filename: str) -> Path:
    path = reports_dir() / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(path, include_plotlyjs="cdn")
    return path


def _style(figure: go.Figure, title: str) -> go.Figure:
    figure.update_layout(
        title={"text": title, "font": {"size": 16, "color": INK}, "x": 0, "xanchor": "left"},
        font={"family": FONT, "color": INK_SECONDARY, "size": 12},
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0, "title": None},
        margin={"l": 64, "r": 24, "t": 96, "b": 48},
    )
    figure.update_xaxes(showgrid=False, linecolor=AXIS, tickfont={"color": INK_MUTED}, title_font={"color": INK_MUTED})
    figure.update_yaxes(
        gridcolor=GRID, zeroline=False, linecolor=AXIS, tickfont={"color": INK_MUTED}, title_font={"color": INK_MUTED}
    )
    return figure


def comments_per_minute(comments: pd.DataFrame, stream_ids: list[str]) -> pd.DataFrame:
    """Comments per minute, one column per stream, with empty minutes as zero."""
    minute = comments["created_utc"].dt.floor("min")
    counts = comments.groupby([minute, "stream"]).size().unstack("stream", fill_value=0)
    counts = counts.reindex(columns=stream_ids, fill_value=0)
    full = pd.date_range(counts.index.min(), counts.index.max(), freq="min")
    return counts.reindex(full, fill_value=0)


def win_prob_steps(plays: pd.DataFrame, win_prob: pd.DataFrame) -> pd.DataFrame:
    """Home win probability over time: the value each play left behind, at the play's end."""
    merged = plays.merge(win_prob, on=["game_pk", "at_bat_index"]).sort_values("end_time_utc")
    start = pd.DataFrame(
        {"time": [plays["start_time_utc"].min()], "home_wp": [merged["home_wp_before"].iloc[0]], "description": [""]}
    )
    steps = merged.rename(columns={"end_time_utc": "time", "home_wp_after": "home_wp"})
    return pd.concat([start, steps[["time", "home_wp", "description"]]], ignore_index=True)


def inning_starts(plays: pd.DataFrame) -> pd.DataFrame:
    """When each half-inning began."""
    first = plays.sort_values("at_bat_index").groupby(["inning", "half"], sort=False).first().reset_index()
    return first[["inning", "half", "start_time_utc"]]


def volume_figure(
    game: pd.Series, plays: pd.DataFrame, win_prob: pd.DataFrame, per_minute: pd.DataFrame
) -> go.Figure:
    """Win probability (top) over comments per minute by stream (bottom), on one time axis."""
    colors = stream_colors(list(per_minute.columns))
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.32, 0.68],
        vertical_spacing=0.06,
    )

    steps = win_prob_steps(plays, win_prob)
    figure.add_trace(
        go.Scatter(
            x=steps["time"],
            y=steps["home_wp"],
            mode="lines",
            line={"color": INK_SECONDARY, "width": 2, "shape": "hv"},
            name=f"{game.home_abbr} win probability",
            text=steps["description"],
            hovertemplate="%{y:.0%}  %{text}<extra></extra>",
            showlegend=False,
        ),
        row=1,
        col=1,
    )
    for stream, color in colors.items():
        figure.add_trace(
            go.Scatter(
                x=per_minute.index,
                y=per_minute[stream],
                mode="lines",
                line={"color": color, "width": 2},
                name=stream,
                hovertemplate="%{y} / min<extra>" + stream + "</extra>",
            ),
            row=2,
            col=1,
        )

    for row in inning_starts(plays).itertuples():
        figure.add_vline(x=row.start_time_utc, line={"color": GRID, "width": 1}, layer="below")
        if row.half == "top":
            figure.add_annotation(
                x=row.start_time_utc,
                y=1.0,
                xref="x",
                yref="y domain",
                text=f"{row.inning}",
                showarrow=False,
                xanchor="left",
                yanchor="top",
                font={"color": INK_MUTED, "size": 11},
                row=1,
                col=1,
            )

    figure.update_yaxes(title_text=f"{game.home_abbr} win probability", tickformat=".0%", range=[0, 1], row=1, col=1)
    figure.update_yaxes(title_text="comments per minute", rangemode="tozero", row=2, col=1)
    figure.update_xaxes(title_text="time (UTC); numbers mark the start of each inning", row=2, col=1)
    figure.update_layout(height=640)
    return _style(figure, f"Comment volume by fanbase vs. win probability: {game.final_score}")


def lag_figure(curves: dict[str, pd.Series], baselines: dict[str, float]) -> go.Figure:
    """Reaction curve per stream as a multiple of that stream's run-up volume."""
    colors = stream_colors(list(curves))
    figure = go.Figure()
    for stream, curve in curves.items():
        figure.add_trace(
            go.Scatter(
                x=curve.index,
                y=curve / baselines[stream],
                mode="lines",
                line={"color": colors[stream], "width": 2},
                name=stream,
                hovertemplate="%{y:.2f}x<extra>" + stream + "</extra>",
            )
        )
    figure.add_vline(x=0, line={"color": AXIS, "width": 1})
    figure.update_xaxes(title_text="seconds after the play ended (5-second buckets)")
    figure.update_yaxes(title_text="comment volume, x the run-up before the play", rangemode="tozero")
    figure.update_layout(height=460)
    return _style(figure, "Reaction lag: comment volume around a play, weighted by win-probability swing")
