# -*- coding: utf-8 -*-
"""
intraday_watch.py
-------------------
夜間のscreener.pyが書き出した candidates.json (その日の狙い目候補) だけを対象に、
分足データで「今INしたら買い時か」を判定してDiscordに通知するスクリプト。
Excel/マーケットスピードⅡ RSSは使わず、GitHub Actions上で完結させる無料版。

想定ワークフロー(intraday_watch.yml):
  - cron: 前場・後場の時間帯に5分おきくらいで実行(例: */5 0-2,3-6 * * 1-5 UTC)
  - candidates.json / state/notified_intraday.json は screener.py と同じリポジトリ内で共有
"""

import json
import os
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf
import requests

from entry_score import compute_verdict, format_discord_message

CANDIDATES_PATH = Path(__file__).parent / "state" / "candidates.json"
STATE_PATH = Path(__file__).parent / "state" / "notified_intraday.json"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
BUY_SCORE_THRESHOLD = 65
JST = ZoneInfo("Asia/Tokyo")
LUNCH_START, LUNCH_END = "11:30", "12:30"


def load_candidates() -> list[dict]:
    return json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))


def load_state() -> dict:
    if STATE_PATH.exists():
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if data.get("date") == str(date.today()):
            return data
    return {"date": str(date.today()), "notified": []}


def save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def in_lunch_break(now_jst: datetime) -> bool:
    hm = now_jst.strftime("%H:%M")
    return LUNCH_START <= hm < LUNCH_END


def send_discord(message: str):
    if not DISCORD_WEBHOOK_URL:
        print("[INFO] Webhook未設定のためコンソール出力のみ:\n" + message)
        return
    requests.post(DISCORD_WEBHOOK_URL, json={"content": message})


def bars_from_df(df) -> list[dict]:
    """yfinanceの分足DataFrame(Close, Volume) を entry_score が読める形式に変換"""
    bars = []
    cum_pv, cum_vol = 0.0, 0.0
    for ts, row in df.iterrows():
        price = float(row["Close"])
        vol = float(row["Volume"])
        cum_pv += price * vol
        cum_vol += vol
        vwap = (cum_pv / cum_vol) if cum_vol > 0 else price
        bars.append({
            "time": ts.tz_convert(JST).strftime("%H:%M") if ts.tzinfo else ts.strftime("%H:%M"),
            "price": price,
            "volume": vol,
            "vwap": vwap,
        })
    return bars


def main():
    now_jst = datetime.now(JST)
    if in_lunch_break(now_jst):
        print("[INFO] 昼休みのためスキップ")
        return

    candidates = load_candidates()
    if not candidates:
        print("[INFO] 本日の候補銘柄なし")
        return

    state = load_state()
    notified = set(state["notified"])
    name_map = {c["ticker"]: c.get("name", "") for c in candidates}
    tickers = [c["ticker"] for c in candidates]

    data = yf.download(
        tickers=tickers, period="1d", interval="1m",
        group_by="ticker", progress=False, threads=True,
    )

    for ticker in tickers:
        if ticker in notified:
            continue
        try:
            df = data[ticker].dropna(subset=["Close", "Volume"])
        except (KeyError, TypeError):
            continue
        if len(df) < 3:
            continue

        bars = bars_from_df(df)
        closes = [b["price"] for b in bars[:3]]
        orb_high, orb_low = max(closes), min(closes)
        i = len(bars) - 1

        verdict = compute_verdict(bars, i, orb_high, orb_low)
        print(f"[CHECK] {ticker} pct={verdict.pct} verdict={verdict.verdict}")

        if verdict.pct >= BUY_SCORE_THRESHOLD:
            msg = format_discord_message(ticker, name_map.get(ticker, ""), bars, i, verdict)
            send_discord(msg)
            notified.add(ticker)

    state["notified"] = list(notified)
    save_state(state)


if __name__ == "__main__":
    main()
