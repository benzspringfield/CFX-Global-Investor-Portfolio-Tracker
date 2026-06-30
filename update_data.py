"""
ดึงข้อมูลล่าสุดของนักลงทุนทุกคนเข้า DB
รันก่อนเปิด dashboard:  python update_data.py
"""
import sys

# Windows console (cp874) พิมพ์ไทย/สัญลักษณ์ไม่ได้ -> บังคับ UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from store import init_db
from config import INVESTORS
from trackers.edgar_13f import fetch_investor_13f
from trackers.ark_daily import fetch_ark
from trackers.congress import fetch_congress


def update_all(n_periods=2, resolve_tickers=False):
    init_db()
    for key, cfg in INVESTORS.items():
        src = cfg["source"]
        try:
            if src == "edgar_13f":
                fetch_investor_13f(key, cfg["cik"], n_periods=n_periods,
                                   resolve_tickers=resolve_tickers)
            elif src == "ark_daily":
                fetch_ark(key, cfg.get("ark_symbols"))
            elif src == "congress":
                from datetime import date
                yr = date.today().year
                fetch_congress(key, cfg["congress_name"], years=[yr, yr - 1])
            elif src == "methodology":
                print(f"[{key}] methodology-only — ใช้ skill {cfg.get('skill')}, ไม่มี holdings")
        except Exception as e:
            print(f"[{key}] ERROR: {e}")
    print("\n✓ อัปเดตข้อมูลเสร็จ")


if __name__ == "__main__":
    # --tickers เพื่อแปลง CUSIP->ticker ผ่าน OpenFIGI (ต้องตั้ง key ใน config)
    resolve = "--tickers" in sys.argv
    update_all(resolve_tickers=resolve)
