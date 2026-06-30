"""
Migration ครั้งเดียว: ลบ holdings ที่ซ้ำ (จากบั๊ก PK ที่มี NULL) แล้วย้ายเข้า schema ใหม่
- รวมเป็น 1 แถวต่อ (investor, period_date, fund, cusip, issuer)
- กู้ ticker ด้วย MAX (ค่า non-null ชนะ NULL)
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from store import connect, init_db, SCHEMA


def migrate():
    with connect() as c:
        before = c.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
        print(f"ก่อน: {before} rows")

        c.executescript("ALTER TABLE holdings RENAME TO holdings_old;")
    # สร้าง holdings ใหม่ (schema ใหม่) + ตารางอื่นที่ยังไม่มี
    init_db()

    with connect() as c:
        c.execute("""
            INSERT OR REPLACE INTO holdings
              (investor, period_date, source, cusip, ticker, issuer,
               value_usd, shares, weight_pct, fund, fetched_at)
            SELECT investor, period_date,
                   MAX(source),
                   COALESCE(cusip,''),
                   MAX(ticker),                 -- non-null ticker ชนะ
                   COALESCE(issuer,''),
                   MAX(value_usd), MAX(shares), MAX(weight_pct),
                   COALESCE(fund,''),
                   MAX(fetched_at)
            FROM holdings_old
            GROUP BY investor, period_date, COALESCE(fund,''),
                     COALESCE(cusip,''), COALESCE(issuer,'')
        """)
        after = c.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
        tk = c.execute("SELECT 100.0*SUM(CASE WHEN ticker IS NOT NULL AND ticker!='' "
                       "THEN 1 ELSE 0 END)/COUNT(*) FROM holdings "
                       "WHERE source='edgar_13f'").fetchone()[0]
        c.execute("DROP TABLE holdings_old;")
        print(f"หลัง: {after} rows (ลบซ้ำ {before-after}) | 13F มี ticker: {tk:.1f}%")
    with connect() as c:
        c.execute("VACUUM;")
    print("✓ migration เสร็จ + VACUUM")


if __name__ == "__main__":
    migrate()
