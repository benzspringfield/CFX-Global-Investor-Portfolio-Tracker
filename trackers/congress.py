"""
ดึงรายการ Periodic Transaction Report (PTR) ของสมาชิกสภา US จาก House Clerk (ทางการ ฟรี)

ข้อจำกัดสำคัญ: ZIP ทางการให้แค่ "ดัชนีการยื่น" (ชื่อ, วันที่, ประเภท, DocID)
รายละเอียดธุรกรรม (ticker/จำนวน/ซื้อ-ขาย) อยู่ใน PDF -> ต้อง OCR (เฟส 2)
ตอนนี้เก็บ metadata + ลิงก์ PDF ให้กดดูเองได้
"""
import io
import zipfile
from lxml import etree

from trackers.common import web_get
from store import upsert_trades, log_fetch

FD_ZIP = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
PTR_PDF = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{docid}.pdf"
import requests
from config import HTTP_UA


def _download_zip_xml(year):
    r = requests.get(FD_ZIP.format(year=year),
                     headers={"User-Agent": HTTP_UA}, timeout=60)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    xml_name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
    return zf.read(xml_name)


def fetch_congress(investor_key="pelosi", last_name="Pelosi", years=None):
    from datetime import date
    years = years or [date.today().year, date.today().year - 1]
    total = 0
    for year in years:
        try:
            xml = _download_zip_xml(year)
        except Exception as e:
            print(f"[{investor_key}] {year} โหลด ZIP ไม่ได้: {e}")
            continue
        root = etree.fromstring(xml)
        rows = []
        for m in root.iter("Member"):
            def g(tag):
                el = m.find(tag)
                return el.text.strip() if el is not None and el.text else ""
            if g("Last").lower() != last_name.lower():
                continue
            ftype = g("FilingType")        # P = Periodic Transaction Report
            if ftype != "P":
                continue
            docid = g("DocID")
            rows.append({
                "investor": investor_key,
                "txn_date": g("FilingDate"),
                "disclosed_at": g("FilingDate"),
                "ticker": None,            # อยู่ใน PDF
                "issuer": f"{g('First')} {g('Last')} — PTR",
                "txn_type": "ptr_filing",
                "amount_low": None,
                "amount_high": None,
                "raw": PTR_PDF.format(year=year, docid=docid),
            })
        n = upsert_trades(rows)
        total += n
        print(f"[{investor_key}] {year}: {n} PTR filings")
        log_fetch(investor_key, "congress", str(year), n)
    return total


if __name__ == "__main__":
    from store import init_db
    init_db()
    fetch_congress(years=[2025, 2024])
