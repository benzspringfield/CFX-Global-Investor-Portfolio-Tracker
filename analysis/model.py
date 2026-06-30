"""
Smart-Money Model — สังเคราะห์สัญญาณจากนักลงทุนทุกคนเป็นคะแนนเดียว
แล้วเสนอ 'พอร์ตแนะนำ'

แนวคิด (ถอดจากพฤติกรรมยอดฝีมือ):
  1. Consensus breadth   : ยิ่งหลายเจ้าถือ ยิ่งน่าเชื่อ            (เงินใหญ่เห็นพ้อง)
  2. Conviction          : น้ำหนักเฉลี่ยในพอร์ตเขาสูง            (กล้าลงหนัก)
  3. Net buying          : งวดล่าสุดมีการ 'เพิ่ม/เปิดใหม่' มากกว่า 'ลด/ขายออก'
  4. Price momentum (opt): ราคา 6 เดือนยังเป็นขาขึ้น            (ride winners — Minervini/Drucken)

คะแนนรวม 0–100 = ถ่วงน้ำหนัก 4 องค์ประกอบข้างต้น
*** เป็นเครื่องมือช่วยตัดสินใจ ไม่ใช่คำแนะนำการลงทุน ***
"""
import pandas as pd

from analysis.consensus import build_consensus
from analysis.changes import compute_changes
from store import get_all_latest_holdings
from config import HOLDINGS_SOURCES

WEIGHTS = {"consensus": 0.35, "conviction": 0.20, "buying": 0.25, "momentum": 0.20}


def _net_buying_signal():
    """รวมสัญญาณซื้อ-ขายล่าสุดของทุกนักลงทุน -> dict {join_key: score -1..+1}"""
    score = {}
    weight_map = {"NEW": 1.0, "ADD": 0.5, "HOLD": 0.0, "TRIM": -0.5, "EXIT": -1.0}
    for inv in HOLDINGS_SOURCES:
        ch = compute_changes(inv)
        if ch.empty or "change" not in ch:
            continue
        for _, r in ch.iterrows():
            key = (r.get("cusip") or r.get("ticker") or r.get("issuer"))
            if not key:
                continue
            key = str(key).upper()
            score.setdefault(key, [])
            score[key].append(weight_map.get(r["change"], 0.0))
    return {k: sum(v) / len(v) for k, v in score.items() if v}


def _price_momentum(tickers):
    """คืน dict {ticker: 6M return %}. ใช้ yfinance (ถ้าดึงไม่ได้ คืนว่าง)"""
    out = {}
    tickers = [t for t in set(tickers) if t and isinstance(t, str)]
    if not tickers:
        return out
    try:
        import yfinance as yf
        data = yf.download(tickers, period="6mo", interval="1d",
                           progress=False, auto_adjust=True)["Close"]
        if isinstance(data, pd.Series):
            data = data.to_frame()
        for t in data.columns:
            s = data[t].dropna()
            if len(s) > 5:
                out[t] = 100.0 * (s.iloc[-1] / s.iloc[0] - 1)
    except Exception as e:
        print("momentum overlay ข้าม:", e)
    return out


def _norm(series):
    s = series.astype(float)
    lo, hi = s.min(), s.max()
    if hi - lo < 1e-9:
        return pd.Series(50.0, index=s.index)
    return 100.0 * (s - lo) / (hi - lo)


def build_model(min_investors=1, use_momentum=True, top_n=20):
    from analysis.consensus import LIVE_MAX_AGE_DAYS
    base = build_consensus(min_investors=min_investors)
    if base.empty:
        return pd.DataFrame()

    df = get_all_latest_holdings(max_age_days=LIVE_MAX_AGE_DAYS)

    def key_for(r):
        if pd.notna(r["ticker"]) and r["ticker"]:
            return str(r["ticker"]).upper()
        return None
    # map join (consensus ใช้ cusip/ticker/issuer) -> หา ticker เพื่อ momentum
    buying = _net_buying_signal()

    # คีย์ match ต้องตรงกับ _net_buying_signal: cusip ก่อน แล้ว ticker แล้ว issuer
    def ckey(r):
        if pd.notna(r.get("cusip")) and r.get("cusip"):
            return str(r["cusip"]).upper()
        if pd.notna(r["ticker"]) and r["ticker"]:
            return str(r["ticker"]).upper()
        return str(r["issuer"]).upper().strip()
    base["key"] = base.apply(ckey, axis=1)
    base["buying_signal"] = base["key"].map(buying).fillna(0.0)

    mom = {}
    if use_momentum:
        mom = _price_momentum(base["ticker"].dropna().tolist())
    base["momentum_6m"] = base["ticker"].map(
        lambda t: mom.get(str(t).upper()) if pd.notna(t) else None)

    # ทำคะแนนย่อย 0–100
    base["s_consensus"] = _norm(base["n_investors"])
    base["s_conviction"] = _norm(base["avg_weight"].fillna(0))
    base["s_buying"] = _norm(base["buying_signal"])
    if base["momentum_6m"].notna().any():
        base["s_momentum"] = _norm(base["momentum_6m"].fillna(base["momentum_6m"].median()))
        w = WEIGHTS
    else:
        base["s_momentum"] = 50.0
        # ไม่มี momentum -> เกลี่ยน้ำหนักไปองค์ประกอบอื่น
        tot = WEIGHTS["consensus"] + WEIGHTS["conviction"] + WEIGHTS["buying"]
        w = {"consensus": WEIGHTS["consensus"] / tot,
             "conviction": WEIGHTS["conviction"] / tot,
             "buying": WEIGHTS["buying"] / tot, "momentum": 0.0}

    base["score"] = (
        w["consensus"] * base["s_consensus"] +
        w["conviction"] * base["s_conviction"] +
        w["buying"] * base["s_buying"] +
        w["momentum"] * base["s_momentum"]
    ).round(1)

    cols = ["issuer", "ticker", "n_investors", "investors", "total_value",
            "avg_weight", "buying_signal", "momentum_6m",
            "s_consensus", "s_conviction", "s_buying", "s_momentum", "score"]
    return base[cols].sort_values("score", ascending=False).head(top_n).reset_index(drop=True)


def suggest_portfolio(model_df, n=10):
    """พอร์ตแนะนำ: top-n ตามคะแนน ถ่วงน้ำหนักตามคะแนน (รวม 100%)"""
    if model_df.empty:
        return pd.DataFrame()
    top = model_df.head(n).copy()
    top["alloc_pct"] = (100.0 * top["score"] / top["score"].sum()).round(1)
    return top[["issuer", "ticker", "score", "n_investors", "alloc_pct"]]


if __name__ == "__main__":
    m = build_model(use_momentum=False, top_n=15)
    print(m[["issuer", "ticker", "n_investors", "buying_signal", "score"]].to_string())
