"""
ชั้นเก็บข้อมูล (SQLite) — schema เดียวกลางสำหรับทุกแหล่ง

ตาราง holdings = snapshot ของพอร์ต ณ วันที่หนึ่ง (period_date)
ทำให้ diff ข้ามงวด และ join ข้ามนักลงทุนด้วย cusip/ticker ได้
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS holdings (
    investor      TEXT NOT NULL,            -- key จาก config.INVESTORS
    period_date   TEXT NOT NULL,            -- วันที่ของ snapshot (YYYY-MM-DD)
    source        TEXT NOT NULL,            -- edgar_13f | ark_daily | congress
    cusip         TEXT NOT NULL DEFAULT '', -- คีย์ join ที่สะอาดสุดสำหรับ 13F
    ticker        TEXT,                     -- attribute (ไม่อยู่ใน PK — เติมภายหลังได้)
    issuer        TEXT NOT NULL DEFAULT '',
    value_usd     REAL,                     -- มูลค่าตลาด (USD)
    shares        REAL,
    weight_pct    REAL,                     -- น้ำหนักในพอร์ต (%)
    fund          TEXT NOT NULL DEFAULT '', -- เช่น ARKK (สำหรับ ark_daily)
    fetched_at    TEXT NOT NULL,
    -- ticker ไม่อยู่ใน PK + คอลัมน์ PK ห้าม NULL (SQLite ถือ NULL!=NULL จะ insert ซ้ำ)
    PRIMARY KEY (investor, period_date, fund, cusip, issuer)
);
CREATE INDEX IF NOT EXISTS idx_holdings_inv  ON holdings(investor, period_date);
CREATE INDEX IF NOT EXISTS idx_holdings_cusip ON holdings(cusip);
CREATE INDEX IF NOT EXISTS idx_holdings_ticker ON holdings(ticker);

CREATE TABLE IF NOT EXISTS trades (
    investor      TEXT NOT NULL,        -- congressional / disclosed trades
    txn_date      TEXT,
    disclosed_at  TEXT,
    ticker        TEXT,
    issuer        TEXT,
    txn_type      TEXT,                 -- buy | sell | exchange
    amount_low    REAL,
    amount_high   REAL,
    raw           TEXT,
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (investor, txn_date, ticker, txn_type, amount_low, raw)
);

CREATE TABLE IF NOT EXISTS fetch_log (
    investor   TEXT,
    source     TEXT,
    period_date TEXT,
    rows       INTEGER,
    fetched_at TEXT
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_holdings(rows):
    """rows = list ของ dict ที่มีคีย์ตรงกับคอลัมน์ holdings"""
    if not rows:
        return 0
    cols = ["investor", "period_date", "source", "cusip", "ticker", "issuer",
            "value_usd", "shares", "weight_pct", "fund", "fetched_at"]
    fetched = now_iso()
    # คอลัมน์ที่อยู่ใน PK ห้าม NULL -> coalesce เป็น '' (กัน insert ซ้ำจาก NULL!=NULL)
    pk_notnull = ("cusip", "issuer", "fund")
    payload = []
    for r in rows:
        r = {**r}
        r.setdefault("fetched_at", fetched)
        for c in cols:
            r.setdefault(c, None)
        for c in pk_notnull:
            if r[c] is None:
                r[c] = ""
        payload.append([r[c] for c in cols])
    placeholders = ",".join("?" * len(cols))
    with connect() as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO holdings ({','.join(cols)}) VALUES ({placeholders})",
            payload,
        )
    return len(payload)


def upsert_trades(rows):
    if not rows:
        return 0
    cols = ["investor", "txn_date", "disclosed_at", "ticker", "issuer",
            "txn_type", "amount_low", "amount_high", "raw", "fetched_at"]
    fetched = now_iso()
    payload = []
    for r in rows:
        r = {**r}
        r.setdefault("fetched_at", fetched)
        for c in cols:
            r.setdefault(c, None)
        payload.append([r[c] for c in cols])
    placeholders = ",".join("?" * len(cols))
    with connect() as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO trades ({','.join(cols)}) VALUES ({placeholders})",
            payload,
        )
    return len(payload)


def log_fetch(investor, source, period_date, rows):
    with connect() as conn:
        conn.execute(
            "INSERT INTO fetch_log VALUES (?,?,?,?,?)",
            (investor, source, period_date, rows, now_iso()),
        )


def get_periods(investor):
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT period_date FROM holdings WHERE investor=? ORDER BY period_date DESC",
            (investor,),
        ).fetchall()
    return [r["period_date"] for r in rows]


def get_holdings(investor, period_date=None):
    import pandas as pd
    q = "SELECT * FROM holdings WHERE investor=?"
    params = [investor]
    if period_date:
        q += " AND period_date=?"
        params.append(period_date)
    with connect() as conn:
        return pd.read_sql_query(q, conn, params=params)


def get_all_latest_holdings(max_age_days=None):
    """
    holdings งวดล่าสุดของนักลงทุนแต่ละคน รวมเป็น DataFrame เดียว
    max_age_days: ถ้าระบุ จะตัดนักลงทุนที่ข้อมูลล่าสุดเก่ากว่านี้ออก
                  (กันพอร์ตเก่าของกองที่เลิกยื่น 13F มาปนในคำแนะนำสด)
    """
    import pandas as pd
    from datetime import datetime
    today = datetime.now().date()
    frames = []
    with connect() as conn:
        investors = [r["investor"] for r in conn.execute(
            "SELECT DISTINCT investor FROM holdings").fetchall()]
    for inv in investors:
        periods = get_periods(inv)
        if not periods:
            continue
        if max_age_days is not None:
            age = (today - datetime.strptime(periods[0], "%Y-%m-%d").date()).days
            if age > max_age_days:
                continue
        frames.append(get_holdings(inv, periods[0]))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def get_trades(investor=None):
    import pandas as pd
    q = "SELECT * FROM trades"
    params = []
    if investor:
        q += " WHERE investor=?"
        params.append(investor)
    q += " ORDER BY txn_date DESC"
    with connect() as conn:
        return pd.read_sql_query(q, conn, params=params)


if __name__ == "__main__":
    init_db()
    print("DB ready:", DB_PATH)
