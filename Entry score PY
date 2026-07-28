# -*- coding: utf-8 -*-
"""
entry_score.py
----------------
「今INしたら買い時かどうか」をスコア化する共通ロジック。
Web版デモ(entry-trainer)と同じ考え方をPythonに移植したもの。

想定している「バー」は5分足である必要はなく、ローカル監視スクリプト側で
RSSのポーリング結果を積み上げた簡易バー(1ポーリング=1バー)でもよい。
各バーは以下のdictを想定:
    {
        "time": "09:05",      # 表示用の時刻文字列
        "price": 1234.0,      # その時点の株価
        "volume": 5000,       # そのバー区間で増えた出来高(累積出来高の差分)
        "vwap": 1230.5,       # そのバー時点までのVWAP(累積 price*volume / 累積volume)
    }

このモジュールは発注も通知も行わない。判定とtips文言の生成のみ。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class IndicatorResult:
    score: float          # -1.0 ~ 1.0
    label: str
    tip: str


@dataclass
class Verdict:
    vwap: IndicatorResult
    orb: IndicatorResult
    atr: IndicatorResult
    pct: int               # 0-100
    verdict: str            # "買い時" / "様子見" / "見送り"


def _avg_volume_up_to(bars: List[Dict], i: int) -> float:
    vols = [b["volume"] for b in bars[: i + 1]]
    return sum(vols) / len(vols) if vols else 0.0


def _atr_proxy(bars: List[Dict], i: int, span: int = 6) -> float:
    start = max(1, i - span + 1)
    diffs = [abs(bars[k]["price"] - bars[k - 1]["price"]) for k in range(start, i + 1)]
    return sum(diffs) / len(diffs) if diffs else 0.0


def _day_atr_up_to(bars: List[Dict], i: int) -> float:
    if i <= 0:
        return 0.0
    diffs = [abs(bars[k]["price"] - bars[k - 1]["price"]) for k in range(1, i + 1)]
    return sum(diffs) / len(diffs)


def vwap_indicator(bar: Dict) -> IndicatorResult:
    if bar["vwap"] == 0:
        return IndicatorResult(0.0, "VWAP計算前", "データが少なくVWAPがまだ安定していない")
    diff = (bar["price"] - bar["vwap"]) / bar["vwap"] * 100
    if diff > 1.6:
        return IndicatorResult(0.3, "過熱ぎみ", f"VWAPより+{diff:.1f}%と離れすぎ。ここからのINは高値掴みのリスクが高い")
    if diff > 0:
        return IndicatorResult(1.0, "VWAP上・良好", f"VWAPより+{diff:.1f}%。買い方の平均コストより高く、地合いは強い")
    if diff > -0.5:
        return IndicatorResult(0.0, "VWAP付近", "VWAPにタッチ中。方向感がまだ弱く、様子見が無難")
    return IndicatorResult(-1.0, "VWAP割れ", f"VWAPより{diff:.1f}%下。買い方が含み損を抱えていて下落継続に注意")


def orb_indicator(bars: List[Dict], i: int, orb_high: float, orb_low: float) -> IndicatorResult:
    if i < 2:
        return IndicatorResult(0.0, "レンジ形成中", "寄り付き15分でその日のオープニングレンジを作っている最中")
    row = bars[i]
    avg_vol = _avg_volume_up_to(bars, i)
    if row["price"] > orb_high and row["volume"] > avg_vol * 1.3:
        return IndicatorResult(1.0, "出来高を伴うブレイク", "寄り付きのレンジ上限を出来高増加とともに突破。一番信頼度の高いサイン")
    if row["price"] > orb_high:
        return IndicatorResult(0.2, "出来高が伴わない突破", "値段はレンジを抜けたが出来高が薄い。ダマシで押し戻される可能性に注意")
    if row["price"] < orb_low:
        return IndicatorResult(-1.0, "レンジ下抜け", "寄り付きのレンジ下限を割り込み。下降トレンド入りのサイン")
    return IndicatorResult(0.0, "レンジ内", "まだ寄り付きのレンジ内。方向感が出るのを待つ場面")


def atr_indicator(bars: List[Dict], i: int) -> IndicatorResult:
    if i < 6:
        return IndicatorResult(0.0, "判定に必要な本数が未達", "値幅の目安を出すにはあと少しデータが必要")
    recent = _atr_proxy(bars, i, 6)
    day = _day_atr_up_to(bars, i)
    if day == 0:
        return IndicatorResult(0.0, "判定不可", "値動きがまだ発生していない")
    if recent >= day * 0.9:
        return IndicatorResult(1.0, "値幅十分", "直近の値動きの幅が普段どおりある。利益を狙いやすいコンディション")
    return IndicatorResult(-0.5, "値幅が細い", "値動きが小さく利益を伸ばしにくいかも。無理にINしないのも選択肢")


def compute_verdict(bars: List[Dict], i: int, orb_high: float, orb_low: float) -> Verdict:
    v = vwap_indicator(bars[i])
    o = orb_indicator(bars, i, orb_high, orb_low)
    a = atr_indicator(bars, i)
    total = (v.score + o.score + a.score) / 3
    pct = round(((total + 1) / 2) * 100)
    if pct >= 65:
        label = "買い時"
    elif pct >= 40:
        label = "様子見"
    else:
        label = "見送り"
    return Verdict(v, o, a, pct, label)


def format_discord_message(ticker: str, name: str, bars: List[Dict], i: int, verdict: Verdict) -> str:
    """通知用のメッセージ本文(tips付き)を組み立てる"""
    row = bars[i]
    lines = [
        f"📈 **{ticker} {name}** — {verdict.verdict}（買いスコア {verdict.pct}）",
        f"現在値 ¥{row['price']:.0f} ／ VWAP ¥{row['vwap']:.0f} ／ {row['time']}",
        "",
        f"・VWAP乖離: {verdict.vwap.label} — {verdict.vwap.tip}",
        f"・オープニングレンジ: {verdict.orb.label} — {verdict.orb.tip}",
        f"・値幅(ATR目安): {verdict.atr.label} — {verdict.atr.tip}",
    ]
    return "\n".join(lines)
