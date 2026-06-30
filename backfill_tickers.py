"""
เติม ticker ให้ holdings ของ 13F (ที่มีแต่ CUSIP) ผ่าน OpenFIGI
ผลถูก cache ใน data/cusip_ticker.json -> รันซ้ำได้เร็ว

รัน:  python backfill_tickers.py
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from store import connect
from trackers.common import map_cusips_to_tickers


def backfill():
    with connect() as c:
        cusips = [r[0] for r in c.execute(
            "SELECT DISTINCT cusip FROM holdings "
            "WHERE cusip IS NOT NULL AND (ticker IS NULL OR ticker='')"
        ).fetchall()]
    print(f"ต้องแปลง {len(cusips)} CUSIP")
    if not cusips:
        print("ครบแล้ว ไม่มีอะไรต้องทำ")
        return

    mapping = map_cusips_to_tickers(cusips, verbose=True)
    resolved = {c: t for c, t in mapping.items() if t}
    print(f"แปลงสำเร็จ {len(resolved)}/{len(cusips)}")

    with connect() as c:
        for cusip, ticker in resolved.items():
            c.execute("UPDATE holdings SET ticker=? WHERE cusip=? "
                      "AND (ticker IS NULL OR ticker='')", (ticker, cusip))
    print("อัปเดต ticker ลง DB เรียบร้อย")


if __name__ == "__main__":
    backfill()
