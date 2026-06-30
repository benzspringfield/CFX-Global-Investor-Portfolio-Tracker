"""
Consensus ข้ามนักลงทุน — หาหุ้นที่ 'เงินใหญ่' ถือซ้ำกันหลายเจ้า
join ด้วย CUSIP (สะอาดสุด) ถ้าไม่มีก็ใช้ ticker/issuer
"""
import pandas as pd

from store import get_all_latest_holdings
from config import INVESTORS


# คำแนะนำสด: ตัดข้อมูลที่เก่ากว่านี้ (13F สดสุด ~135 วัน + buffer) -> กันกองที่เลิกยื่น
LIVE_MAX_AGE_DAYS = 200


def build_consensus(min_investors=1, max_age_days=LIVE_MAX_AGE_DAYS):
    df = get_all_latest_holdings(max_age_days=max_age_days)
    if df.empty:
        return pd.DataFrame()

    # คีย์รวม: ใช้ cusip ก่อน ไม่งั้น ticker ไม่งั้น issuer (normalize)
    def rowkey(r):
        if pd.notna(r["cusip"]) and r["cusip"]:
            return r["cusip"]
        if pd.notna(r["ticker"]) and r["ticker"]:
            return str(r["ticker"]).upper()
        return str(r["issuer"]).upper().strip()
    df["join_key"] = df.apply(rowkey, axis=1)

    # ป้ายชื่อที่อ่านง่าย
    label = (df.sort_values("value_usd", ascending=False)
               .groupby("join_key")
               .agg(issuer=("issuer", "first"),
                    ticker=("ticker", "first"),
                    cusip=("cusip", "first")))

    g = df.groupby("join_key").agg(
        n_investors=("investor", "nunique"),
        investors=("investor", lambda s: ", ".join(sorted(set(s)))),
        total_value=("value_usd", "sum"),
        avg_weight=("weight_pct", "mean"),
        max_weight=("weight_pct", "max"),
    )
    out = label.join(g).reset_index(drop=True)
    out = out[out["n_investors"] >= min_investors]
    # คะแนน consensus เบื้องต้น: จำนวนเจ้า + conviction เฉลี่ย
    out["consensus_score"] = (
        out["n_investors"] * 10 + out["avg_weight"].fillna(0)
    )
    return out.sort_values(
        ["n_investors", "total_value"], ascending=False
    ).reset_index(drop=True)


def overlap_matrix():
    """ตารางจำนวนหุ้นที่ถือซ้ำกันระหว่างนักลงทุนแต่ละคู่"""
    df = get_all_latest_holdings()
    if df.empty:
        return pd.DataFrame()
    df["k"] = df["cusip"].fillna(df["ticker"]).fillna(df["issuer"])
    holders = df.groupby("investor")["k"].apply(lambda s: set(s.dropna()))
    inv = list(holders.index)
    mat = pd.DataFrame(index=inv, columns=inv, dtype=int)
    for a in inv:
        for b in inv:
            mat.loc[a, b] = len(holders[a] & holders[b])
    return mat
