# -*- coding: utf-8 -*-
"""
株価スクリーニング & 通知ツール（自動銘柄ピックアップ版）
--------------------------------------------------
・JPX（東証）が無料公開している上場銘柄一覧を毎回自動取得（ウォッチリストの手入力は不要）
・予算内(budget_yen)で買える価格の銘柄に絞り込み
・テクニカルシグナル（ゴールデンクロス/デッドクロス/RSI/出来高急増）を検知
・該当銘柄をDiscordに通知（Discordアプリを入れておけばiPhoneにプッシュ通知が届く）
・発注は一切行わない「通知専用」ツール

【重要な免責事項】
本ツールが出す通知は機械的なテクニカル指標の検知結果に過ぎず、投資助言ではありません。
表示された銘柄が「儲かる」ことを保証するものではなく、選定ロジックにも限界があります。
売買の最終判断・実行は必ずご自身の責任で行ってください。
"""

import io
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_PATH = Path(__file__).parent / "state" / "notified.json"
JPX_LIST_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"


def load_state() -> set:
    """当日すでに通知済みの(銘柄,シグナル種別)を読み込む。無ければ空集合。"""
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return set(tuple(x) for x in json.load(f))
        except Exception:
            return set()
    return set()


def save_state(state: set):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump([list(x) for x in state], f, ensure_ascii=False)


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_webhook_url(config: dict) -> str:
    # GitHub Actionsのsecretsを優先。なければconfig.jsonの値を使う
    return os.environ.get("DISCORD_WEBHOOK_URL") or config.get("discord_webhook_url", "")


def fetch_universe(markets: list[str]) -> pd.DataFrame:
    """JPXの東証上場銘柄一覧を取得し、対象市場の銘柄だけに絞る"""
    print("[INFO] JPX銘柄一覧を取得中...")
    resp = requests.get(JPX_LIST_URL, timeout=30)
    resp.raise_for_status()
    df = pd.read_excel(io.BytesIO(resp.content))
    df = df[["コード", "銘柄名", "市場・商品区分", "33業種区分"]].dropna(subset=["コード"])

    def normalize_code(x):
        # 2024年以降、証券コードは「130A」のような英数字混在も存在するため
        # 単純なint変換はできない。数値ならゼロ埋め、文字列ならそのまま使う。
        if isinstance(x, float) and x.is_integer():
            return str(int(x)).zfill(4)
        if isinstance(x, int):
            return str(x).zfill(4)
        return str(x).strip().zfill(4)

    df["コード"] = df["コード"].apply(normalize_code)
    df["ticker"] = df["コード"] + ".T"

    # 指定した市場区分（プライム/スタンダード/グロースの普通株のみ、ETFやREIT等は除外）
    mask = df["市場・商品区分"].apply(
        lambda x: any(m in str(x) for m in markets) and "内国株式" in str(x)
    )
    df = df[mask].reset_index(drop=True)
    print(f"[INFO] 対象銘柄数: {len(df)}")
    return df


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def screen_batch(tickers: list[str], budget_yen: int, lot_size: int) -> list[dict]:
    """複数銘柄をまとめてダウンロードし、シグナルと予算条件を満たすものを返す"""
    hits = []
    try:
        data = yf.download(
            tickers, period="6mo", interval="1d", group_by="ticker",
            threads=True, progress=False,
        )
    except Exception as e:
        print(f"[WARN] バッチ取得失敗: {e}", file=sys.stderr)
        return hits

    for ticker in tickers:
        try:
            df = data[ticker].dropna(how="all")
        except Exception:
            continue
        if df.empty or len(df) < 30:
            continue

        df["SMA5"] = df["Close"].rolling(5).mean()
        df["SMA25"] = df["Close"].rolling(25).mean()
        df["RSI14"] = calc_rsi(df["Close"], 14)
        df["VOL_AVG20"] = df["Volume"].rolling(20).mean()

        today, yesterday = df.iloc[-1], df.iloc[-2]
        price = today["Close"]

        # 予算オーバーの銘柄は除外(単元株ベース)
        if pd.isna(price) or price * lot_size > budget_yen:
            continue

        signals = []  # (種別キー, 表示テキスト) のタプルで保持
        if yesterday["SMA5"] <= yesterday["SMA25"] and today["SMA5"] > today["SMA25"]:
            signals.append(("golden_cross", "🟢 ゴールデンクロス"))
        if yesterday["SMA5"] >= yesterday["SMA25"] and today["SMA5"] < today["SMA25"]:
            signals.append(("dead_cross", "🔴 デッドクロス"))
        if today["RSI14"] < 30:
            signals.append(("rsi_oversold", f"🟢 RSI売られすぎ({today['RSI14']:.0f})"))
        elif today["RSI14"] > 70:
            signals.append(("rsi_overbought", f"🔴 RSI買われすぎ({today['RSI14']:.0f})"))
        if today["VOL_AVG20"] > 0 and today["Volume"] > today["VOL_AVG20"] * 2:
            signals.append(("volume_spike", f"🟡 出来高急増({today['Volume'] / today['VOL_AVG20']:.1f}倍)"))

        if signals:
            hits.append({
                "ticker": ticker,
                "price": price,
                "signals": signals,
            })
    return hits


def send_discord(webhook_url: str, lines: list[str]):
    if not lines:
        lines = ["本日は条件に合う銘柄がありませんでした。"]
    header = "📈 本日の株価スクリーニング結果\n" + "-" * 20 + "\n"
    content = header + "\n".join(lines)

    if not webhook_url or "ここに" in webhook_url:
        print("[INFO] Webhook未設定のためコンソール出力のみ:")
        print(content)
        return

    # Discordの1メッセージ上限(2000文字)を考慮して分割送信
    chunk = ""
    for line in content.split("\n"):
        if len(chunk) + len(line) + 1 > 1900:
            requests.post(webhook_url, json={"content": chunk})
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        requests.post(webhook_url, json={"content": chunk})


def main():
    config = load_config()
    webhook_url = get_webhook_url(config)
    budget_yen = config.get("budget_yen", 200000)
    lot_size = config.get("lot_size", 100)
    markets = config.get("markets", ["プライム", "スタンダード"])
    max_notify = config.get("max_notify", 30)
    batch_size = config.get("batch_size", 80)

    universe = fetch_universe(markets)
    name_map = dict(zip(universe["ticker"], universe["銘柄名"]))
    tickers = universe["ticker"].tolist()

    all_hits = []
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        print(f"[INFO] {i}/{len(tickers)} 件処理中...")
        all_hits.extend(screen_batch(batch, budget_yen, lot_size))
        time.sleep(1.5)  # レート制限対策

    # 当日すでに通知した(銘柄,シグナル種別)は除外し、新規分だけ通知する
    state = load_state()
    new_hits = []
    for h in all_hits:
        new_signals = [(k, t) for k, t in h["signals"] if (h["ticker"], k) not in state]
        if new_signals:
            new_hits.append({**h, "signals": new_signals})
            for k, _ in new_signals:
                state.add((h["ticker"], k))

    new_hits.sort(key=lambda h: len(h["signals"]), reverse=True)
    new_hits = new_hits[:max_notify]

    lines = []
    for h in new_hits:
        name = name_map.get(h["ticker"], "")
        lines.append(f"**{h['ticker']} {name}** (現在値 {h['price']:.0f}円)")
        for _, text in h["signals"]:
            lines.append(f"　{text}")

    print(f"[INFO] 新規該当銘柄数: {len(new_hits)} (総該当 {len(all_hits)})")
    if new_hits:
        send_discord(webhook_url, lines)
    else:
        print("[INFO] 新規シグナルなし。通知をスキップします。")

    save_state(state)


if __name__ == "__main__":
    main()
