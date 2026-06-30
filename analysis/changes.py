"""
ตรวจจับการเปลี่ยนแปลงพอร์ตระหว่าง 2 งวด (ถอดรื้อ 'การเคลื่อนไหว')
- NEW    : ตำแหน่งที่เพิ่งเปิด
- EXIT   : ขายออกหมด
- ADD    : เพิ่มจำนวนหุ้น
- TRIM   : ลดจำนวนหุ้น
"""
import pandas as pd

from store import get_periods, get_holdings


def _key_col(df):
    """ใช้ ticker ถ้ามีครบ ไม่งั้น fallback เป็น cusip/issuer"""
    if df.empty:
        return "issuer"
    if df["ticker"].notna().mean() > 0.8:
        return "ticker"
    if df["cusip"].notna().mean() > 0.8:
        return "cusip"
    return "issuer"


def compute_changes(investor, period_new=None, period_old=None):
    periods = get_periods(investor)
    if len(periods) < 1:
        return pd.DataFrame()
    period_new = period_new or periods[0]
    if period_old is None:
        period_old = periods[1] if len(periods) > 1 else None

    new = get_holdings(investor, period_new)
    if period_old is None or new.empty:
        # ไม่มีงวดก่อนหน้าให้เทียบ -> ถือว่าทุกตัวเป็น NEW
        out = new.copy()
        out["change"] = "NEW"
        out["shares_old"] = 0
        out["shares_new"] = out["shares"]
        out["value_new"] = out["value_usd"]
        return out.sort_values("value_usd", ascending=False)

    old = get_holdings(investor, period_old)
    key = _key_col(new)

    # รวมตามคีย์ (เผื่อหลายกองทุน/หลาย share class)
    # ตัดคอลัมน์ key ออกจาก agg ไม่งั้นชนกับ index ตอน reset_index
    agg = {"shares": "sum", "value_usd": "sum", "weight_pct": "sum",
           "issuer": "first", "ticker": "first", "cusip": "first"}
    agg.pop(key, None)
    n = new.groupby(key, dropna=False).agg(agg).rename(
        columns={"shares": "shares_new", "value_usd": "value_new"})
    o = old.groupby(key, dropna=False).agg({"shares": "sum", "value_usd": "sum"}).rename(
        columns={"shares": "shares_old", "value_usd": "value_old"})
    m = n.join(o, how="outer")
    m["shares_new"] = m["shares_new"].fillna(0)
    m["shares_old"] = m["shares_old"].fillna(0)
    m["value_new"] = m["value_new"].fillna(0)

    def classify(r):
        if r["shares_old"] == 0 and r["shares_new"] > 0:
            return "NEW"
        if r["shares_new"] == 0 and r["shares_old"] > 0:
            return "EXIT"
        if r["shares_new"] > r["shares_old"] * 1.02:
            return "ADD"
        if r["shares_new"] < r["shares_old"] * 0.98:
            return "TRIM"
        return "HOLD"

    m["change"] = m.apply(classify, axis=1)
    m["pct_change_shares"] = (
        (m["shares_new"] - m["shares_old"]) /
        m["shares_old"].replace(0, pd.NA) * 100
    )
    m = m.reset_index()
    m["period_new"] = period_new
    m["period_old"] = period_old
    return m.sort_values("value_new", ascending=False)


def summary_counts(changes_df):
    if changes_df.empty or "change" not in changes_df:
        return {}
    return changes_df["change"].value_counts().to_dict()
