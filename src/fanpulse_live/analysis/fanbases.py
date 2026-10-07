"""Phase 5: how the fanbases differ, as one offline HTML report.

Built on per-comment sentiment and the player each comment names, from
whichever model tagged them. With the baseline tags that means RoBERTa tone
and name matching, so the report says nothing about emotion types or blame;
those need Jev's readings.
"""

from __future__ import annotations

import html
from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from fanpulse_live.gamestate import GameTimeline
from fanpulse_live.viz import charts

FANBASES = {"LAD": "Dodgers fans", "TOR": "Blue Jays fans", None: "Neutral fans"}
NEGATIVE, POSITIVE = -0.25, 0.25
TONE_COLORS = {"negative": "#e34948", "neutral": "#c3c2b7", "positive": "#2a78d6"}
MIN_MENTIONS = 15
RECOVERY_BEFORE = timedelta(minutes=3)
RECOVERY_AFTER = timedelta(minutes=12)


def with_fanbase(tagged: pd.DataFrame, streams: list[dict]) -> pd.DataFrame:
    team = {s["id"]: s["team"] for s in streams}
    out = tagged.copy()
    out["fanbase"] = out["stream"].map(lambda s: FANBASES[team[s]])
    return out


def win_probability_added(timeline: GameTimeline) -> pd.DataFrame:
    """Each player's win probability added for his own team, batting and pitching combined."""
    totals: dict[int, float] = {}
    for play in timeline.plays:
        batter_is_home = timeline.players[play.batter_id].team == timeline.home
        for_batter = play.wp_delta if batter_is_home else -play.wp_delta
        totals[play.batter_id] = totals.get(play.batter_id, 0.0) + for_batter
        totals[play.pitcher_id] = totals.get(play.pitcher_id, 0.0) - for_batter
    return pd.DataFrame(
        [{"player": timeline.name(i), "team": timeline.players[i].team, "wpa": round(v, 4)} for i, v in totals.items()]
    )


def echo_chamber(in_game: pd.DataFrame, streams: list[dict]) -> pd.DataFrame:
    """A team's own subreddit against the same team's flaired fans in the neutral subreddit."""
    rows = []
    for team, fanbase in FANBASES.items():
        if team is None:
            continue
        home = next(s["id"] for s in streams if s["team"] == team and s["flair"] is None)
        away = next(s["id"] for s in streams if s["team"] == team and s["flair"] is not None)
        per_minute = (
            in_game[in_game["stream"].isin([home, away])]
            .assign(minute=lambda f: f["created_utc"].dt.floor("min"))
            .groupby(["minute", "stream"])["sentiment"].mean().unstack("stream")
        )
        both = per_minute.dropna()
        for stream in (home, away):
            group = in_game[in_game["stream"] == stream]
            rows.append(
                {
                    "fanbase": fanbase,
                    "stream": stream,
                    "where": "own subreddit" if stream == home else "r/baseball, flaired",
                    "comments": len(group),
                    "mean_sentiment": round(group["sentiment"].mean(), 3),
                    "share_negative": round((group["sentiment"] < NEGATIVE).mean(), 3),
                    "share_positive": round((group["sentiment"] > POSITIVE).mean(), 3),
                    "swing": round(per_minute[stream].std(), 3),
                    "minute_correlation": round(both[home].corr(both[away]), 2),
                }
            )
    return pd.DataFrame(rows)


def perception_vs_performance(in_game: pd.DataFrame, timeline: GameTimeline) -> pd.DataFrame:
    """For each fanbase and player: how often he is named, how fans feel about him, and his WPA."""
    wpa = win_probability_added(timeline).set_index("player")
    named = in_game.dropna(subset=["subject"])
    table = (
        named.groupby(["fanbase", "subject"])["sentiment"].agg(mentions="count", sentiment="mean").reset_index()
        .rename(columns={"subject": "player"})
    )
    table = table[table["mentions"] >= MIN_MENTIONS].copy()
    table["team"] = table["player"].map(lambda p: next(x.team for x in timeline.players.values() if x.name == p))
    table["wpa"] = table["player"].map(wpa["wpa"]).fillna(0.0)
    table["sentiment"] = table["sentiment"].round(3)
    return table.sort_values(["fanbase", "mentions"], ascending=[True, False]).reset_index(drop=True)


def tone_by_inning(in_game: pd.DataFrame, timeline: GameTimeline) -> pd.DataFrame:
    """Share of negative, neutral and positive comments per fanbase and inning."""
    starts = {}
    for play in timeline.plays:
        starts.setdefault(play.inning, play.start)
    edges = [pd.Timestamp(starts[i]) for i in sorted(starts)] + [pd.Timestamp(timeline.final_out())]
    inning = pd.cut(in_game["created_utc"], edges, labels=sorted(starts), right=False)
    tone = pd.cut(in_game["sentiment"], [-1.01, NEGATIVE, POSITIVE, 1.01], labels=list(TONE_COLORS))
    counts = in_game.assign(inning=inning, tone=tone).groupby(["fanbase", "inning", "tone"], observed=True).size()
    share = counts / counts.groupby(level=[0, 1]).transform("sum")
    return share.rename("share").reset_index()


def recovery(in_game: pd.DataFrame, timeline: GameTimeline) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    """After each team's worst swing: how long its fans' mood takes to get back to where it was.

    Mood is a one-minute rolling mean of comment sentiment. Returns one row
    per fanbase and the mood curve around the play, by seconds from its end.
    """
    rows, curves = [], {}
    for team, fanbase in FANBASES.items():
        if team is None:
            continue
        sign = 1 if team == timeline.home else -1
        play = min(timeline.plays, key=lambda p: sign * p.wp_delta)
        end = pd.Timestamp(play.end)
        fans = in_game[in_game["fanbase"] == fanbase].set_index("created_utc")["sentiment"].sort_index()
        rolling = fans.rolling("60s").mean()
        around = rolling[(rolling.index >= end - RECOVERY_BEFORE) & (rolling.index <= end + RECOVERY_AFTER)]
        before = around[around.index <= end].mean()
        after = around[around.index > end]
        low_at = after.idxmin()
        back = after[(after.index > low_at) & (after >= before)]
        curve = around.copy()
        curve.index = (curve.index - end).total_seconds()
        curves[fanbase] = curve
        rows.append(
            {
                "fanbase": fanbase,
                "play": play.description,
                "inning": f"{'Top' if play.half == 'top' else 'Bottom'} {play.inning}",
                "team_wp_change": round(sign * play.wp_delta, 3),
                "mood_before": round(before, 3),
                "mood_low": round(after.min(), 3),
                "seconds_to_low": round((low_at - end).total_seconds()),
                "seconds_to_recover": round((back.index[0] - end).total_seconds()) if len(back) else None,
            }
        )
    return pd.DataFrame(rows), curves


def postgame_subjects(postgame: pd.DataFrame, top: int = 6) -> pd.DataFrame:
    named = postgame.dropna(subset=["subject"])
    table = named.groupby(["fanbase", "subject"])["sentiment"].agg(mentions="count", sentiment="mean").reset_index()
    return table.sort_values(["fanbase", "mentions"], ascending=[True, False]).groupby("fanbase").head(top)


# ------------------------------------------------------------------ charts


def _style(figure: go.Figure, height: int) -> go.Figure:
    figure.update_layout(
        font={"family": charts.FONT, "color": charts.INK_SECONDARY, "size": 12},
        paper_bgcolor=charts.SURFACE, plot_bgcolor=charts.SURFACE, height=height,
        margin={"l": 56, "r": 24, "t": 48, "b": 48},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.06, "x": 0, "title": None},
    )  # fmt: skip
    figure.update_xaxes(showgrid=False, linecolor=charts.AXIS, tickfont={"color": charts.INK_MUTED}, title_font={"color": charts.INK_MUTED})
    figure.update_yaxes(gridcolor=charts.GRID, zeroline=False, linecolor=charts.AXIS, tickfont={"color": charts.INK_MUTED}, title_font={"color": charts.INK_MUTED})
    figure.update_annotations(font={"color": charts.INK, "size": 13})
    return figure


def echo_figure(in_game: pd.DataFrame, streams: list[dict], colors: dict[str, str]) -> go.Figure:
    teams = [(t, f) for t, f in FANBASES.items() if t is not None]
    figure = make_subplots(rows=1, cols=2, shared_yaxes=True, subplot_titles=[f for _, f in teams], horizontal_spacing=0.06)
    for col, (team, _) in enumerate(teams, start=1):
        for stream in (s["id"] for s in streams if s["team"] == team):
            series = (
                in_game[in_game["stream"] == stream].set_index("created_utc")["sentiment"].sort_index()
                .resample("1min").mean().rolling(5, min_periods=2, center=True).mean()
            )
            figure.add_trace(
                go.Scatter(x=series.index, y=series, mode="lines", name=stream, line={"color": colors[stream], "width": 2},
                           hovertemplate="%{y:+.2f}<extra>" + stream + "</extra>"),
                row=1, col=col,
            )  # fmt: skip
    figure.add_hline(y=0, line={"color": charts.AXIS, "width": 1})
    figure.update_yaxes(title_text="mean sentiment, 5-minute average", row=1, col=1)
    figure.update_xaxes(title_text="time (UTC)")
    figure.update_layout(hovermode="x unified")
    return _style(figure, 380)


def perception_figure(table: pd.DataFrame, timeline: GameTimeline) -> go.Figure:
    teams = [(t, f) for t, f in FANBASES.items() if t is not None]
    figure = make_subplots(rows=1, cols=2, shared_yaxes=True, subplot_titles=[f"{f}, about their own players" for _, f in teams], horizontal_spacing=0.06)
    for col, (team, fanbase) in enumerate(teams, start=1):
        own = table[(table["fanbase"] == fanbase) & (table["team"] == team)]
        figure.add_trace(
            go.Scatter(
                x=own["wpa"], y=own["sentiment"], mode="markers+text", text=own["player"].str.split().str[-1],
                textposition="top center", textfont={"color": charts.INK_SECONDARY, "size": 11},
                marker={"size": (own["mentions"] ** 0.5 * 2.2).clip(8, 40), "color": charts.SERIES_COLORS[col - 1],
                        "line": {"color": charts.SURFACE, "width": 2}},
                customdata=own[["player", "mentions"]],
                hovertemplate="%{customdata[0]}<br>WPA %{x:+.1%}<br>sentiment %{y:+.2f}<br>%{customdata[1]} mentions<extra></extra>",
                showlegend=False,
            ),
            row=1, col=col,
        )  # fmt: skip
    figure.add_hline(y=0, line={"color": charts.AXIS, "width": 1})
    figure.add_vline(x=0, line={"color": charts.AXIS, "width": 1})
    figure.update_xaxes(title_text="win probability added for his team", tickformat="+.0%")
    figure.update_yaxes(title_text="mean sentiment of comments naming him", row=1, col=1)
    return _style(figure, 420)


def tone_figure(share: pd.DataFrame) -> go.Figure:
    fanbases = list(FANBASES.values())
    figure = make_subplots(rows=1, cols=3, shared_yaxes=True, subplot_titles=fanbases, horizontal_spacing=0.04)
    for col, fanbase in enumerate(fanbases, start=1):
        rows = share[share["fanbase"] == fanbase]
        for tone, color in TONE_COLORS.items():
            part = rows[rows["tone"] == tone]
            figure.add_trace(
                go.Bar(x=part["inning"].astype(str), y=part["share"], name=tone, marker={"color": color, "line": {"color": charts.SURFACE, "width": 2}},
                       legendgroup=tone, showlegend=col == 1, hovertemplate="%{y:.0%} " + tone + "<extra>inning %{x}</extra>"),
                row=1, col=col,
            )  # fmt: skip
    figure.update_layout(barmode="stack")
    figure.update_yaxes(tickformat=".0%", range=[0, 1])
    figure.update_yaxes(title_text="share of comments", row=1, col=1)
    figure.update_xaxes(title_text="inning")
    return _style(figure, 380)


def recovery_figure(curves: dict[str, pd.Series], table: pd.DataFrame) -> go.Figure:
    figure = make_subplots(rows=1, cols=len(curves), shared_yaxes=True, horizontal_spacing=0.06,
                           subplot_titles=[f"{row.fanbase}: {row.inning}" for row in table.itertuples()])  # fmt: skip
    for col, (fanbase, curve) in enumerate(curves.items(), start=1):
        figure.add_trace(
            go.Scatter(x=curve.index / 60, y=curve, mode="lines", line={"color": charts.SERIES_COLORS[col - 1], "width": 2},
                       name=fanbase, showlegend=False, hovertemplate="%{y:+.2f}<extra>%{x:.1f} min</extra>"),
            row=1, col=col,
        )  # fmt: skip
        before = float(table.loc[table["fanbase"] == fanbase, "mood_before"].iloc[0])
        figure.add_hline(y=before, line={"color": charts.INK_MUTED, "width": 1, "dash": "dot"}, row=1, col=col)
    figure.add_vline(x=0, line={"color": charts.AXIS, "width": 1})
    figure.update_xaxes(title_text="minutes from the play (dotted line: mood before it)")
    figure.update_yaxes(title_text="mood, one-minute average", row=1, col=1)
    return _style(figure, 360)


def write_report(path, title: str, intro: str, sections: list[tuple[str, str, go.Figure | None, str]]) -> None:
    """A standalone HTML page: for each section a heading, a paragraph, a chart and a table."""
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{html.escape(title)}</title>",
        "<script src='https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.35.3/plotly.min.js'></script>",
        "<style>body{margin:0;background:#f9f9f7;color:#0b0b0b;font:15px/1.55 system-ui,-apple-system,'Segoe UI',sans-serif}"
        "main{max-width:1040px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 8px}"
        "h2{font-size:18px;margin:36px 0 6px}p{color:#52514e;margin:6px 0 12px}"
        ".card{background:#fcfcfb;border:1px solid rgba(11,11,11,.1);border-radius:10px;padding:8px;overflow-x:auto}"
        "table{border-collapse:collapse;margin:12px 0;font-size:13.5px;width:100%}th,td{padding:5px 10px;border-bottom:1px solid #e1e0d9;text-align:left}"
        "th{color:#898781;font-weight:600}td.n{text-align:right;font-variant-numeric:tabular-nums}li{margin:6px 0;color:#52514e}li b{color:#0b0b0b}</style>",
        f"</head><body><main><h1>{html.escape(title)}</h1>{intro}",
    ]
    for number, (heading, text, figure, table) in enumerate(sections):
        parts.append(f"<h2>{html.escape(heading)}</h2>{text}")
        if figure is not None:
            parts.append("<div class='card'>" + figure.to_html(full_html=False, include_plotlyjs=False, div_id=f"chart{number}", config={"displayModeBar": False, "responsive": True}) + "</div>")
        parts.append(table)
    parts.append("</main></body></html>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")


def html_table(frame: pd.DataFrame, formats: dict[str, str] | None = None) -> str:
    formats = formats or {}
    head = "".join(f"<th>{html.escape(str(c).replace('_', ' '))}</th>" for c in frame.columns)
    body = []
    for row in frame.itertuples(index=False):
        cells = []
        for column, value in zip(frame.columns, row):
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            if value is None or (isinstance(value, float) and value != value):
                text = "–"
            elif column in formats:
                text = format(value, formats[column])
            else:
                text = str(value)
            cells.append(f"<td class='{'n' if numeric else ''}'>{html.escape(text)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
