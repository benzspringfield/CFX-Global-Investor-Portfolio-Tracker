"""
ดึงและ parse แบบฟอร์ม 13F-HR จาก SEC EDGAR

13F = รายงานการถือครองหุ้น US ของผู้จัดการสถาบัน (>$100M) รายไตรมาส
- เห็นเฉพาะฝั่ง long (หุ้น/ออปชั่นบางส่วน), ไม่เห็น short, ดีเลย์ ~45 วัน
- value: filing ตั้งแต่ 2023-01-03 รายงานเป็น "ดอลลาร์เต็ม", ก่อนหน้านั้นเป็น "พันดอลลาร์"
"""
import re
from lxml import etree

from trackers.common import sec_get, map_cusips_to_tickers
from store import upsert_holdings, log_fetch

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik10}.json"
ARCHIVE_DIR = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"

# เกณฑ์เปลี่ยนหน่วย value ของ 13F
DOLLAR_RULE_DATE = "2023-01-03"


def _cik10(cik):
    return str(int(cik)).zfill(10)


# XML information table เริ่มบังคับใช้ ~กลางปี 2013 -> ก่อนหน้านี้เป็น text parse ไม่ได้
MIN_XML_REPORT_DATE = "2014-01-01"


def _rows_from_block(block):
    out = []
    for form, acc, fdate, rdate in zip(
        block["form"], block["accessionNumber"],
        block["filingDate"], block["reportDate"],
    ):
        if form in ("13F-HR", "13F-HR/A"):
            out.append({
                "accession": acc,
                "acc_nodash": acc.replace("-", ""),
                "filing_date": fdate,
                "report_date": rdate,
                "form": form,
            })
    return out


def list_13f_filings(cik, deep=False, since=MIN_XML_REPORT_DATE):
    """
    คืน list ของ filing 13F-HR (ใหม่->เก่า)
    deep=True -> ตามอ่าน shard เก่าทั้งหมดด้วย (ประวัติลึก) กรอง report_date >= since
    """
    data = sec_get(SUBMISSIONS.format(cik10=_cik10(cik)), as_json=True)
    out = _rows_from_block(data["filings"]["recent"])
    if deep:
        for shard in data["filings"].get("files", []):
            try:
                sd = sec_get("https://data.sec.gov/submissions/" + shard["name"],
                             as_json=True)
                out.extend(_rows_from_block(sd))
            except Exception as e:
                print(f"  shard {shard['name']} โหลดไม่ได้: {e}")
    # dedup ตาม report_date: เลือก 13F-HR ต้นฉบับ (ตัวเต็ม) ก่อน /A (amendment อาจเป็นบางส่วน)
    # ถ้ามีแต่ /A ค่อยใช้ /A · จากนั้นเรียงงวดใหม่ -> เก่า
    out = [f for f in out if f["report_date"] >= since]
    best = {}
    for f in out:
        rd = f["report_date"]
        is_amend = f["form"].endswith("/A")
        if rd not in best or (best[rd]["form"].endswith("/A") and not is_amend):
            best[rd] = f
    return sorted(best.values(), key=lambda x: x["report_date"], reverse=True)


def _find_infotable_url(cik, acc_nodash):
    """หาไฟล์ XML information table ในโฟลเดอร์ filing"""
    base = ARCHIVE_DIR.format(cik=int(cik), acc=acc_nodash)
    idx = sec_get(base + "/index.json", as_json=True)
    items = idx["directory"]["item"]
    candidates = []
    for it in items:
        name = it["name"].lower()
        if name.endswith(".xml") and "primary_doc" not in name:
            candidates.append(it["name"])
    # เดาตัวที่น่าจะเป็น info table ก่อน
    for c in candidates:
        if "infotable" in c.lower() or "form13f" in c.lower() or "table" in c.lower():
            return base + "/" + c
    return base + "/" + candidates[0] if candidates else None


def _local(tag):
    return etree.QName(tag).localname if tag is not None else None


def _detect_value_scale(rows):
    """
    ตรวจอัตโนมัติว่า value รายงานเป็น 'พันดอลลาร์' หรือ 'ดอลลาร์เต็ม'
    โดยดูราคาต่อหุ้นโดยนัย (value/shares) เทียบช่วงราคาหุ้นที่สมเหตุผล
    คืน multiplier (1000.0 ถ้าเป็นพัน, 1.0 ถ้าเป็นดอลลาร์เต็ม)
    """
    import statistics
    implied = [r["value"] / r["shares"]
               for r in rows if r.get("shares") and r.get("value")]
    if not implied:
        return 1000.0  # default เดิมของ 13F
    median_price = statistics.median(implied)
    # ราคาหุ้นจริงแทบไม่เคยต่ำกว่า $1 ทั้งพอร์ต -> ถ้าต่ำมากแปลว่า value เป็นพัน
    return 1000.0 if median_price < 1.0 else 1.0


def parse_infotable(xml_text):
    """parse XML information table -> list ของ dict holding (value_usd หน่วยดอลลาร์เต็ม)"""
    root = etree.fromstring(xml_text.encode("utf-8"))
    rows = []
    # หา element infoTable ทุกตัวโดยไม่สน namespace
    for info in root.iter():
        if _local(info.tag) != "infoTable":
            continue
        rec = {"value": 0.0, "shares": 0.0}
        for child in info.iter():
            tag = _local(child.tag)
            if tag == "nameOfIssuer":
                rec["issuer"] = (child.text or "").strip()
            elif tag == "cusip":
                rec["cusip"] = (child.text or "").strip().upper()
            elif tag == "value":
                try:
                    rec["value"] = float((child.text or "0").replace(",", ""))
                except ValueError:
                    rec["value"] = 0.0
            elif tag == "sshPrnamt":
                try:
                    rec["shares"] = float((child.text or "0").replace(",", ""))
                except ValueError:
                    rec["shares"] = 0.0
        if rec.get("issuer"):
            rows.append(rec)

    mult = _detect_value_scale(rows)
    for r in rows:
        r["value_usd"] = r["value"] * mult
    return rows


def fetch_investor_13f(investor_key, cik, n_periods=2, resolve_tickers=True,
                       deep=False, since=MIN_XML_REPORT_DATE):
    """
    ดึง 13F ของนักลงทุนแล้วบันทึกลง DB
    n_periods=None + deep=True -> ดึงทุกงวดตั้งแต่ since (ประวัติลึกสำหรับ backtest)
    """
    filings = list_13f_filings(cik, deep=deep, since=since)
    if not filings:
        print(f"[{investor_key}] ไม่พบ filing 13F")
        return 0
    if n_periods is not None:
        filings = filings[:n_periods]

    total = 0
    for f in filings:
        url = _find_infotable_url(cik, f["acc_nodash"])
        if not url:
            continue
        xml = sec_get(url)
        holdings = parse_infotable(xml)
        if not holdings:
            continue

        # รวมรายการที่ CUSIP เดียวกัน (13F อาจแยกตามชนิด/ผู้จัดการ)
        merged = {}
        for h in holdings:
            key = h["cusip"]
            if key in merged:
                merged[key]["value_usd"] += h["value_usd"]
                merged[key]["shares"] += h.get("shares", 0)
            else:
                merged[key] = {**h, "shares": h.get("shares", 0)}
        holdings = list(merged.values())

        total_value = sum(h["value_usd"] for h in holdings) or 1.0

        # แปลง CUSIP -> ticker (ถ้ามี OpenFIGI key)
        ticker_map = {}
        if resolve_tickers:
            ticker_map = map_cusips_to_tickers([h["cusip"] for h in holdings])

        rows = []
        for h in holdings:
            rows.append({
                "investor": investor_key,
                "period_date": f["report_date"],
                "source": "edgar_13f",
                "cusip": h["cusip"],
                "ticker": ticker_map.get(h["cusip"]),
                "issuer": h["issuer"],
                "value_usd": h["value_usd"],
                "shares": h.get("shares"),
                "weight_pct": 100.0 * h["value_usd"] / total_value,
                "fund": None,
            })
        n = upsert_holdings(rows)
        log_fetch(investor_key, "edgar_13f", f["report_date"], n)
        total += n
        print(f"[{investor_key}] {f['report_date']} ({f['form']}): {n} holdings "
              f"= ${total_value/1e9:.2f}B")
    return total


if __name__ == "__main__":
    from store import init_db
    init_db()
    fetch_investor_13f("druckenmiller", "0001536411", n_periods=2,
                       resolve_tickers=False)
