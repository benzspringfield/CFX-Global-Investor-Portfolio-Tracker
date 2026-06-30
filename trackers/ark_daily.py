"""
ดึง holdings รายวันของกองทุน ARK จาก arkfunds.io (ฟรี)
ข้อมูลละเอียดสุดในกลุ่มนี้ — เห็น ticker, จำนวนหุ้น, น้ำหนัก รายวัน
"""
from trackers.common import web_get
from store import upsert_holdings, log_fetch

API = "https://arkfunds.io/api/v2/etf/holdings?symbol={sym}"


def fetch_ark(investor_key="ark", symbols=None):
    symbols = symbols or ["ARKK", "ARKW", "ARKG", "ARKQ", "ARKF", "ARKX"]
    total = 0
    latest_date = None
    for sym in symbols:
        try:
            data = web_get(API.format(sym=sym), as_json=True)
        except Exception as e:
            print(f"[ark] {sym} ดึงไม่ได้: {e}")
            continue
        holdings = data.get("holdings", [])
        rows = []
        for h in holdings:
            if not h.get("ticker") and not h.get("company"):
                continue
            d = h.get("date")
            latest_date = max(latest_date, d) if latest_date else d
            rows.append({
                "investor": investor_key,
                "period_date": d,
                "source": "ark_daily",
                "cusip": (h.get("cusip") or "").upper() or None,
                "ticker": h.get("ticker"),
                "issuer": h.get("company") or h.get("ticker"),
                "value_usd": h.get("market_value"),
                "shares": h.get("shares"),
                "weight_pct": h.get("weight"),
                "fund": sym,
            })
        n = upsert_holdings(rows)
        total += n
        print(f"[ark] {sym} {data.get('date_to') or ''}: {n} holdings")
    if latest_date:
        log_fetch(investor_key, "ark_daily", latest_date, total)
    return total


if __name__ == "__main__":
    from store import init_db
    init_db()
    fetch_ark(symbols=["ARKK", "ARKW"])
