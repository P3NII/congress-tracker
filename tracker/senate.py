"""Senate Periodic Transaction Reports from the official eFD site (free).

https://efdsearch.senate.gov — the site asks visitors to acknowledge its
data-use statement (no commercial use, no credit/solicitation use) before
searching; this script sends that acknowledgement the same way the web form
does. Personal research use like this tracker is what the site allows.
Electronic PTRs are HTML tables; paper PTRs are scanned images and get listed
with a link instead.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

ROOT = "https://efdsearch.senate.gov"
UA = "Mozilla/5.0 (personal research script; congress-tracker)"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.get(f"{ROOT}/search/home/", timeout=45)
    r.raise_for_status()
    token = BeautifulSoup(r.text, "html.parser").find("input", {"name": "csrfmiddlewaretoken"})["value"]
    s.post(f"{ROOT}/search/home/",
           data={"prohibition_agreement": "1", "csrfmiddlewaretoken": token},
           headers={"Referer": f"{ROOT}/search/home/"}, timeout=45).raise_for_status()
    return s


def _list_reports(s: requests.Session, since: date) -> list[dict]:
    csrf = s.cookies.get("csrftoken") or s.cookies.get("csrf")
    out, start = [], 0
    while True:
        data = {
            "start": str(start), "length": "100",
            "report_types": "[11]",  # 11 = Periodic Transaction Report
            "filer_types": "[]",
            "submitted_start_date": since.strftime("%m/%d/%Y 00:00:00"),
            "submitted_end_date": "", "candidate_state": "", "senator_state": "",
            "office_id": "", "first_name": "", "last_name": "",
            "order[0][column]": "4", "order[0][dir]": "desc",
        }
        r = s.post(f"{ROOT}/search/report/data/", data=data, timeout=45,
                   headers={"Referer": f"{ROOT}/search/", "X-CSRFToken": csrf or ""})
        r.raise_for_status()
        rows = r.json().get("data", [])
        for first, last, office, link_html, filed in rows:
            m = re.search(r'href="([^"]+)"', link_html)
            if not m:
                continue
            href = m.group(1)
            out.append({
                "politician": f"{first.strip()} {last.strip()}".strip(),
                "office": BeautifulSoup(office, "html.parser").get_text(" ", strip=True),
                "url": ROOT + href,
                "paper": "/paper/" in href,
                "filed": datetime.strptime(filed, "%m/%d/%Y").date().isoformat(),
                "doc_id": href.rstrip("/").split("/")[-1],
            })
        if len(rows) < 100:
            break
        start += 100
        time.sleep(0.5)
    return out


def _amt(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_ptr_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    heads = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
    rows = []
    for tr in table.find("tbody").find_all("tr") if table.find("tbody") else []:
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < len(heads):
            continue
        rec = dict(zip(heads, cells))
        ticker = rec.get("ticker", "").strip()
        ticker = None if ticker in ("", "--") else ticker.split()[0].replace(".", "-")
        tx = rec.get("type", "").lower()
        typ = "buy" if tx.startswith("purchase") else ("sell (partial)" if "partial" in tx
                                                    else "sell" if tx.startswith("sale") else tx or "other")
        atype = rec.get("asset type", "").lower()
        try:
            traded = datetime.strptime(rec.get("transaction date", ""), "%m/%d/%Y").date().isoformat()
        except ValueError:
            traded = None
        rows.append({
            "ticker": ticker,
            "asset": rec.get("asset name", "")[:120],
            "asset_type": "option" if "option" in atype else ("stock" if "stock" in atype else atype or "other"),
            "type": typ,
            "traded": traded,
            "owner": rec.get("owner", "").lower() or "self",
            "amount": _amt(rec.get("amount", "")),
            "description": (rec.get("comment", "") or "").strip("- ") or None,
        })
    return rows


def fetch_recent(since: date, seen: set[str], sleep: float = 0.5) -> tuple[list[dict], list[dict]]:
    s = _session()
    reports = [r for r in _list_reports(s, since) if f"S:{r['doc_id']}" not in seen]
    print(f"[senate] {len(reports)} new PTR filings to read")
    txs, unparsed = [], []
    for rep in reports:
        key = f"S:{rep['doc_id']}"
        if rep["paper"]:
            unparsed.append({**rep, "chamber": "Senate", "reason": "scanned paper filing"})
            seen.add(key)
            continue
        try:
            r = s.get(rep["url"], timeout=45, headers={"Referer": f"{ROOT}/search/"})
            r.raise_for_status()
            rows = parse_ptr_html(r.text)
        except Exception as e:
            print(f"[senate] {rep['doc_id']} failed: {e}")
            unparsed.append({**rep, "chamber": "Senate", "reason": "download/parse error"})
            continue
        for j, row in enumerate(rows):
            txs.append({"id": f"{key}:{j}", "chamber": "Senate", "politician": rep["politician"],
                        "district": "", "filed": rep["filed"], "source_url": rep["url"], **row})
        seen.add(key)
        time.sleep(sleep)
    return txs, unparsed
