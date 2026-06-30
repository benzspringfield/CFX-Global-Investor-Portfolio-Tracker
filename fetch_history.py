"""
ดึงประวัติ 13F ย้อนหลังลึก (ยุค XML, ตั้งแต่ ~2014) สำหรับ backtest หลายรอบตลาด
ครอบคลุมตลาดหมี 2018Q4 / 2020 COVID / 2022

รัน:  python fetch_history.py [since=YYYY-MM-DD]
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from store import init_db
from config import INVESTORS
from trackers.edgar_13f import fetch_investor_13f


def fetch_all_history(since="2015-01-01"):
    init_db()
    for key, cfg in INVESTORS.items():
        if cfg["source"] != "edgar_13f":
            continue
        print(f"\n=== {key} (ตั้งแต่ {since}) ===")
        try:
            fetch_investor_13f(key, cfg["cik"], n_periods=None,
                               resolve_tickers=False, deep=True, since=since)
        except Exception as e:
            print(f"[{key}] ERROR: {e}")
    print("\n✓ ดึงประวัติเสร็จ")


if __name__ == "__main__":
    since = sys.argv[1] if len(sys.argv) > 1 else "2015-01-01"
    fetch_all_history(since)
