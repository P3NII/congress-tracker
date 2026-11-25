"""House of Representatives Periodic Transaction Reports (PTRs).

Free, official source: the House Clerk publishes a yearly zip index of every
financial disclosure filing, plus one PDF per PTR.
  index:  https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip
  PTR:    https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf
Electronically filed PTRs contain text we can parse. Paper filings are scanned
images; those are listed in the report with a link so you can read them yourself.
"""
from __future__ import annotations

import io
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime

import requests

BASE = "https://disclosures-clerk.house.gov/public_disc"
UA = {"User-Agent": "Mozilla/5.0 (personal research script; congress-tracker)"}

TX_TYPES = {"P": "buy", "S": "sell", "S (partial)": "sell (partial)", "E": "exchange"}

# A transaction starts on a line holding: [owner] asset... TX MM/DD/YYYY MM/DD/YYYY $amount
# The asset name (and the "(TICKER) [ST]" part) often wraps onto the next line(s).
TX_LINE = re.compile(
    r"^(?P<pre>.*?)\s*(?<![A-Za-z])(?P<tx>S \(partial\)|P|S|E)\s+"
    r"(?P<tdate>\d{1,2}/\d{1,2}/\d{4})\s+(?P<ndate>\d{1,2}/\d{1,2}/\d{4})\s*(?P<amt>.*)$"
)
META = re.compile(r"^\s*(F S|S O|D|C|L|I P O|Filing ID|\* For the complete|Cap\.|ID Owner|Type Date|\$200\?|Clerk of the House|Name:|Status:|State/District)\b\s*:?")
TICKER = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,7})\)")
ATYPE = re.compile(r"\[([A-Z]{2})\]")
AMT_TAIL = re.compile(r"(\$[\d,]+)\s*$")


def _get(url: str, **kw) -> requests.Response:
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=45, **kw)
            if r.status_code == 200:
                return r
        except requests.RequestException:
            pass
        time.sleep(2 * (attempt + 1))
    r.raise_for_status()
    return r


def fetch_index(year: int) -> list[dict]:
    """All PTR filings ('P' filing type) listed in the year's index."""
    r = _get(f"{BASE}/financial-pdfs/{year}FD.zip")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    xml_name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
    root = ET.fromstring(z.read(xml_name))
    out = []
    for m in root.iter("Member"):
        g = lambda tag: (m.findtext(tag) or "").strip()
        if g("FilingType") != "P":
            continue
        try:
            filed = datetime.strptime(g("FilingDate"), "%m/%d/%Y").date()
        except ValueError:
            continue
        name = " ".join(x for x in [g("Prefix"), g("First"), g("Last"), g("Suffix")] if x)
        out.append({
            "chamber": "House",
            "doc_id": g("DocID"),
            "politician": name.replace("Hon. ", "").strip(),
            "district": g("StateDst"),
            "filed": filed.isoformat(),
            "year": int(g("Year") or year),
            "url": f"{BASE}/ptr-pdfs/{int(g('Year') or year)}/{g('DocID')}.pdf",
        })
    return out


def pdf_text(content: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    return text.replace("\x00", "")


def _parse_date(s: str) -> str | None:
    try:
        return datetime.strptime(s, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def parse_ptr_text(text: str) -> list[dict]:
    """Pull transactions out of an electronic PTR's text, block by block."""
    blocks, cur, in_desc = [], None, False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = TX_LINE.match(line)
        if m and (m.group("amt").startswith("$") or m.group("amt").startswith("Over") or m.group("amt") == ""):
            cur = {"pre": m.group("pre"), "tx": m.group("tx"), "tdate": m.group("tdate"),
                   "amt": m.group("amt"), "cont": [], "desc": []}
            blocks.append(cur)
            in_desc = False
            continue
        if cur is None:
            continue
        if META.match(line):
            in_desc = bool(re.match(r"^\s*D\s*:", line))
            if in_desc:
                cur["desc"].append(re.sub(r"^\s*D\s*:\s*", "", line))
            else:
                cur["closed"] = True
            continue
        if in_desc:
            cur["desc"].append(line)
        elif not cur.get("closed"):
            cur["cont"].append(line)

    rows = []
    for b in blocks:
        asset_parts, amt = [b["pre"]], b["amt"].strip()
        for c in b["cont"]:
            t = AMT_TAIL.search(c)
            if t and amt.endswith("-"):
                amt = f"{amt} {t.group(1)}"
                c = c[: t.start()]
            asset_parts.append(c)
        asset = " ".join(p.strip() for p in asset_parts if p.strip())
        owner = "self"
        om = re.match(r"^(SP|JT|DC)\s+", asset)
        if om:
            owner = {"SP": "spouse", "JT": "joint", "DC": "dependent child"}[om.group(1)]
            asset = asset[om.end():]
        tk = TICKER.findall(asset)
        at = ATYPE.findall(asset)
        atype = at[-1] if at else ""
        name = re.sub(r"\s*\[[A-Z]{2}\]\s*", " ", asset)
        name = TICKER.sub("", name).strip(" -")
        rows.append({
            "ticker": tk[-1].replace(".", "-") if tk else None,
            "asset": re.sub(r"\s+", " ", name)[:120],
            "asset_type": {"ST": "stock", "OP": "option"}.get(atype, atype.lower() or "other"),
            "type": TX_TYPES.get(b["tx"], b["tx"]),
            "traded": _parse_date(b["tdate"]),
            "owner": owner,
            "amount": re.sub(r"\s+", " ", amt),
            "description": " ".join(b["desc"])[:300] or None,
        })
    return rows


def fetch_recent(since: date, seen: set[str], sleep: float = 0.3) -> tuple[list[dict], list[dict]]:
    """Return (transactions, unparsed_filings) for PTRs filed on/after `since`
    that we haven't processed before."""
    years = sorted({since.year, date.today().year})
    filings = []
    for y in years:
        try:
            filings += fetch_index(y)
        except Exception as e:  # index for a new year may not exist yet in early January
            print(f"[house] index {y} unavailable: {e}")
    filings = [f for f in filings if f["filed"] >= since.isoformat() and f"H:{f['doc_id']}" not in seen]
    filings.sort(key=lambda f: f["filed"])
    print(f"[house] {len(filings)} new PTR filings to read")

    txs, unparsed = [], []
    for f in filings:
        key = f"H:{f['doc_id']}"
        try:
            r = _get(f["url"])
            text = pdf_text(r.content)
            rows = parse_ptr_text(text)
        except Exception as e:
            print(f"[house] {f['doc_id']} failed: {e}")
            unparsed.append({**f, "reason": "download/parse error"})
            continue
        if not rows:
            unparsed.append({**f, "reason": "scanned paper filing" if len(text.strip()) < 50 else "no stock rows found"})
        for j, row in enumerate(rows):
            txs.append({
                "id": f"{key}:{j}",
                "chamber": "House",
                "politician": f["politician"],
                "district": f["district"],
                "filed": f["filed"],
                "source_url": f["url"],
                **row,
            })
        seen.add(key)
        time.sleep(sleep)
    return txs, unparsed
