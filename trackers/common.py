"""HTTP helper + cache + ตัวแปลง CUSIP -> ticker (OpenFIGI, optional)"""
import json
import time
import requests

from config import SEC_USER_AGENT, HTTP_UA, OPENFIGI_API_KEY, DATA_DIR

_SESSION = requests.Session()

# rate-limit เบาๆ ให้ SEC (ขอไม่เกิน ~10 req/s)
_last_call = {"t": 0.0}


def _throttle(min_interval=0.12):
    dt = time.time() - _last_call["t"]
    if dt < min_interval:
        time.sleep(min_interval - dt)
    _last_call["t"] = time.time()


def sec_get(url, as_json=False, timeout=30):
    """GET ไปยัง sec.gov / data.sec.gov พร้อม User-Agent ที่ถูกต้อง"""
    _throttle()
    r = _SESSION.get(url, headers={"User-Agent": SEC_USER_AGENT,
                                   "Accept-Encoding": "gzip, deflate"},
                     timeout=timeout)
    r.raise_for_status()
    return r.json() if as_json else r.text


def web_get(url, as_json=False, timeout=30, headers=None):
    h = {"User-Agent": HTTP_UA}
    if headers:
        h.update(headers)
    r = _SESSION.get(url, headers=h, timeout=timeout)
    r.raise_for_status()
    return r.json() if as_json else r.text


# ---------------------------------------------------------------------------
# CUSIP -> ticker cache (ใช้ OpenFIGI ถ้ามี key, ไม่งั้นข้าม)
# ---------------------------------------------------------------------------
_CUSIP_CACHE_PATH = DATA_DIR / "cusip_ticker.json"


def _load_cusip_cache():
    if _CUSIP_CACHE_PATH.exists():
        return json.loads(_CUSIP_CACHE_PATH.read_text())
    return {}


def _save_cusip_cache(cache):
    _CUSIP_CACHE_PATH.write_text(json.dumps(cache, indent=0))


def map_cusips_to_tickers(cusips, verbose=False):
    """
    คืน dict {cusip: ticker} ผ่าน OpenFIGI
    - มี key  : batch 100, ~25 req/min
    - ไม่มี key: batch 10,  ~25 req/min (ช้ากว่า แต่ใช้ได้)
    ผลลัพธ์ถูก cache ลงไฟล์ จึงเสียเวลาแค่ครั้งแรก
    """
    cache = _load_cusip_cache()
    unknown = [c for c in dict.fromkeys(cusips) if c and c not in cache]
    if not unknown:
        return {c: cache.get(c) for c in cusips}

    url = "https://api.openfigi.com/v3/mapping"
    headers = {"Content-Type": "application/json"}
    if OPENFIGI_API_KEY:
        headers["X-OPENFIGI-APIKEY"] = OPENFIGI_API_KEY
        batch_size, pause = 100, 0.3
    else:
        batch_size, pause = 10, 2.6   # keyless: <=10 jobs, <=25 req/min

    done = 0
    for i in range(0, len(unknown), batch_size):
        batch = unknown[i:i + batch_size]
        body = [{"idType": "ID_CUSIP", "idValue": c} for c in batch]
        try:
            resp = _SESSION.post(url, headers=headers,
                                 data=json.dumps(body), timeout=40)
            if resp.status_code == 429:        # โดน rate limit -> พักแล้วลองใหม่
                time.sleep(10)
                resp = _SESSION.post(url, headers=headers,
                                     data=json.dumps(body), timeout=40)
            resp.raise_for_status()
            for c, item in zip(batch, resp.json()):
                data = item.get("data") or []
                cache[c] = data[0].get("ticker") if data else None
            done += len(batch)
            if verbose and done % 200 < batch_size:
                print(f"  ...แปลงแล้ว {done}/{len(unknown)} CUSIP")
            _save_cusip_cache(cache)        # save ระหว่างทาง กันงานหาย
            time.sleep(pause)
        except Exception as e:
            print("OpenFIGI error:", e, "(หยุดชั่วคราว ผลที่ได้ถูก cache แล้ว)")
            break

    return {c: cache.get(c) for c in cusips}
