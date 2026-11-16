"""Parse cached House + Senate filings into public/data/trades.json (and copy the static site)."""
import glob, html, json, os, re, shutil, subprocess, datetime, collections, statistics
from parse_house import parse as parse_house_text

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "cache")
PUB = os.path.join(ROOT, "public")

HOUSE_CODES = {"ST": "Stock", "OP": "Option", "GS": "Gov't security / muni", "CS": "Corporate bond/note",
               "OT": "Other", "HN": "Hedge/private fund", "OI": "Non-public investment", "CT": "Crypto",
               "PS": "Private stock", "OL": "LLC/partnership", "VA": "Variable annuity", "AB": "Asset-backed / LLC",
               "ET": "ETF", "EF": "ETF", "MF": "Mutual fund", "RS": "Restricted stock", "RP": "Real property"}
OWNERS = {"": "Self", "SP": "Spouse", "JT": "Joint", "DC": "Dependent", "Self": "Self", "Spouse": "Spouse",
          "Joint": "Joint", "Child": "Dependent"}


def pdate(s):
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            d = datetime.datetime.strptime(s, fmt).date()
            return d if 2000 <= d.year <= datetime.date.today().year + 1 else None
        except ValueError:
            pass
    return None


def amount(s):
    s = re.sub(r"\s+", " ", (s or "")).strip()
    nums = [int(float(x.replace(",", ""))) for x in re.findall(r"\$([\d,]+(?:\.\d+)?)", s)]
    if "Over" in s and nums:
        return s, nums[0] + 1, None
    if len(nums) >= 2:
        return f"${nums[0]:,} – ${nums[1]:,}", nums[0], nums[1]
    if len(nums) == 1:
        return f"${nums[0]:,} (exact)", nums[0], nums[0]
    return s or "unknown", None, None


def option_info(text):
    t = text or ""
    m = re.search(r"\b(call|put)s?\b", t, re.I)
    if not m:
        return None
    o = {"kind": m.group(1).capitalize()}
    sm = re.search(r"strike(?: price)?(?: of)?:?\s*\$?\s*([\d,]+(?:\.\d+)?)", t, re.I) or re.search(r"\$([\d,]+(?:\.\d+)?)", t)
    if sm:
        o["strike"] = float(sm.group(1).replace(",", ""))
    em = re.search(r"(?:expir\w*(?: date)?(?: of)?|exp\.?)\s*:?\s*(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d\d-\d\d)", t, re.I)
    if em:
        d = pdate(em.group(1))
        o["exp"] = d.isoformat() if d else em.group(1)
    cm = re.search(r"([\d,]+)\s+(?:call|put)\s+option", t, re.I)
    if cm:
        o["contracts"] = int(cm.group(1).replace(",", ""))
    return o


def clean_ticker(t):
    t = (t or "").strip().upper()
    return t if re.fullmatch(r"[A-Z][A-Z0-9.\-$/]{0,9}", t) and t not in ("--", "N/A") else ""


# ---------------- members ----------------
def load_members():
    def j(n):
        p = os.path.join(CACHE, n)
        return json.load(open(p)) if os.path.exists(p) else []
    cur, hist = j("legislators-current.json"), j("legislators-historical.json")
    recent = [m for m in hist if any(t.get("end", "") >= "2025-01-01" for t in m.get("terms", []))]
    people = cur + recent
    comms = {c["thomas_id"]: c["name"] for c in j("committees-current.json")} if cur else {}
    for c in (j("committees-current.json") or []):
        for sc in c.get("subcommittees", []):
            comms[c["thomas_id"] + sc["thomas_id"]] = c["name"] + " — " + sc["name"]
    mem_comms = collections.defaultdict(list)
    cm = j("committee-membership-current.json") or {}
    for cid, lst in cm.items():
        if cid in comms and len(cid) == 4:  # top-level committees only
            for x in lst:
                mem_comms[x.get("bioguide")].append(comms[cid])
    out = []
    for m in people:
        t = [x for x in m["terms"] if x.get("end", "") >= "2025-01-01"] or m["terms"][-1:]
        last = t[-1]
        out.append({"bioguide": m["id"]["bioguide"], "last": m["name"].get("last", ""),
                    "first": m["name"].get("first", ""), "nick": m["name"].get("nickname", ""),
                    "official": m["name"].get("official_full", ""), "party": last.get("party", ""),
                    "state": last.get("state", ""), "district": last.get("district"),
                    "chambers": {x["type"] for x in t}, "committees": sorted(set(mem_comms.get(m["id"]["bioguide"], [])))})
    return out


def norm(s):
    return re.sub(r"[^a-z]", "", (s or "").lower().replace("é", "e").replace("á", "a").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ñ", "n"))


def match_house(members, last, first, statedst):
    st, dist = statedst[:2], statedst[2:]
    dist = int(dist) if dist.isdigit() else None
    cands = [m for m in members if "rep" in m["chambers"] and m["state"] == st]
    for m in cands:
        if norm(m["last"]) == norm(last) or norm(last) in norm(m["official"]):
            return m
    for m in cands:
        if dist is not None and m["district"] in (dist, 0) and norm(last)[:4] in norm(m["official"]):
            return m
    return None


def match_senate(members, last, first):
    last = last.split(",")[0].strip()
    cands = [m for m in members if "sen" in m["chambers"] and (norm(m["last"]) == norm(last) or norm(last) in norm(m["official"]))]
    if len(cands) > 1:
        f = norm(first.split()[0]) if first else ""
        c2 = [m for m in cands if norm(m["first"]).startswith(f[:3]) or norm(m["nick"]).startswith(f[:3])]
        cands = c2 or cands
    return cands[0] if cands else None


# ---------------- House ----------------
def house_trades(members):
    filings = json.load(open(os.path.join(CACHE, "filings.json")))
    os.makedirs(os.path.join(CACHE, "txt"), exist_ok=True)
    trades, skipped, parsed_docs, empty_docs = [], [], 0, 0
    for f in filings:
        doc = f["DocID"]
        pdf = os.path.join(CACHE, "pdf", doc + ".pdf")
        txtp = os.path.join(CACHE, "txt", doc + ".txt")
        if not os.path.exists(pdf):
            continue
        if not os.path.exists(txtp) or os.path.getmtime(txtp) < os.path.getmtime(pdf):
            subprocess.run(["pdftotext", "-layout", pdf, txtp], capture_output=True)
        text = open(txtp, errors="replace").read() if os.path.exists(txtp) else ""
        name = " ".join(x for x in [f.get("Prefix"), f.get("First"), f.get("Last"), f.get("Suffix")] if x)
        if len(re.sub(r"\s", "", text)) < 50 or "Filing ID" not in text:
            skipped.append({"chamber": "House", "member": name, "filed": f.get("FilingDate"),
                            "url": f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{f['Year']}/{doc}.pdf"})
            continue
        head, rows = parse_house_text(text)
        parsed_docs += 1
        if not rows:
            empty_docs += 1
        m = match_house(members, f["Last"], f["First"], f["StateDst"])
        filed = pdate(head["signed"]) or pdate(f["FilingDate"])
        url = f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{f['Year']}/{doc}.pdf"
        for i, r in enumerate(rows):
            a = re.sub(r"\s+", " ", r["asset"])
            cm = re.search(r"\[([A-Z]{2})\]", a)
            code = cm.group(1) if cm else ""
            tk = re.findall(r"\(([A-Z][A-Z0-9.\-$/]{0,9})\)", a)
            name_clean = re.sub(r"\s*\[[A-Z]{2}\]\s*", " ", a)
            if tk:
                name_clean = name_clean.replace(f"({tk[-1]})", "")
            trades.append({
                "chamber": "House", "doc": doc, "row": i, "url": url,
                "filer": f"{f['First']} {f['Last']}".strip(), "statedst": f["StateDst"],
                "member": m, "owner": OWNERS.get(r["owner"], r["owner"]),
                "asset": name_clean.strip(" -"), "ticker": clean_ticker(tk[-1]) if tk else "",
                "code": code, "atype": HOUSE_CODES.get(code, code or "Unknown"),
                "type": r["type"], "txdate": pdate(r["txdate"]), "notdate": pdate(r["notdate"]),
                "filed": filed, "amount_raw": r["amount"], "desc": re.sub(r"\s+", " ", r["desc"]).strip(),
                "comment": re.sub(r"\s+", " ", r["comment"]).strip(),
                "amended": "amend" in r["status"].lower(), "cap_gt_200": None,
            })
    return trades, skipped, parsed_docs


# ---------------- Senate ----------------
def senate_trades(members):
    idxp = os.path.join(CACHE, "senate", "_index.json")
    if not os.path.exists(idxp):
        return [], [], 0
    reports = json.load(open(idxp))
    trades, skipped, docs = [], [], 0
    for rep in reports:
        url = "https://efdsearch.senate.gov" + rep["url"]
        if "/view/ptr/" not in rep["url"]:
            skipped.append({"chamber": "Senate", "member": f"{rep['first']} {rep['last']}", "filed": rep["filed"], "url": url})
            continue
        uid = rep["url"].rstrip("/").split("/")[-1]
        p = os.path.join(CACHE, "senate", uid + ".html")
        if not os.path.exists(p):
            continue
        s = open(p, errors="replace").read()
        docs += 1
        fm = re.search(r"Filed\s+(\d\d/\d\d/\d{4})", s)
        filed = pdate(fm.group(1)) if fm else pdate(rep["filed"])
        m = match_senate(members, rep["last"], rep["first"])
        tb = s[s.find("<tbody>"):s.find("</tbody>")]
        for i, tr in enumerate(re.findall(r"<tr>(.*?)</tr>", tb, re.S)):
            td = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x))).strip()
                  for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(td) < 9:
                continue
            _, txd, owner, ticker, asset, atype, ttype, amt, comment = td[:9]
            typ = {"Purchase": "P", "Sale (Full)": "S", "Sale (Partial)": "S (partial)", "Exchange": "E"}.get(ttype, ttype)
            code = {"Stock": "ST", "Stock Option": "OP"}.get(atype, "")
            desc = ""
            if code == "OP":
                mm = re.search(r"Option Type:.*$", asset)
                if mm:
                    desc, asset = mm.group(0), asset[:mm.start()].strip()
            trades.append({
                "chamber": "Senate", "doc": uid, "row": i, "url": url,
                "filer": f"{rep['first']} {rep['last']}", "statedst": m["state"] if m else "",
                "member": m, "owner": OWNERS.get(owner, owner), "asset": asset, "ticker": clean_ticker(ticker),
                "code": code, "atype": "Option" if code == "OP" else atype, "type": typ,
                "txdate": pdate(txd), "notdate": None, "filed": filed, "amount_raw": amt, "desc": desc,
                "comment": "" if comment == "--" else comment, "amended": "amend" in rep["title"].lower(),
            })
    return trades, skipped, docs


# ---------------- assemble ----------------
def dedup(trades):
    """Amendments re-list trades: when the same trade appears in several filings, keep only the latest filing's copy."""
    def key(t):
        return (t["chamber"], t["member"]["bioguide"] if t["member"] else t["filer"], t["owner"],
                t["ticker"] or re.sub(r"\W", "", t["asset"].lower())[:40], t["type"], t["txdate"], t["amount_raw"])
    groups = collections.defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    out, removed = [], 0
    for g in groups.values():
        docs = {t["doc"]: t["filed"] or datetime.date.min for t in g}
        if len(docs) > 1:
            keep = max(docs, key=lambda d: (docs[d], d))
            kept = [t for t in g if t["doc"] == keep]
            removed += len(g) - len(kept)
            for t in kept:
                t["amended"] = True
            out += kept
        else:
            out += g
    return out, removed


def find_clusters(rows, window=30):
    """Same ticker bought by 2+ different members with transaction dates within `window` days."""
    by = collections.defaultdict(list)
    for i, r in enumerate(rows):
        if r.get("tk") and r["ty"] == "P" and r.get("td") and not r.get("bad") and r["cls"] in ("stock", "option"):
            by[r["tk"]].append((datetime.date.fromisoformat(r["td"]), i))
    out = []
    for tk, lst in by.items():
        lst.sort()
        i = 0
        while i < len(lst):
            j = i
            while j + 1 < len(lst) and (lst[j + 1][0] - lst[i][0]).days <= window:
                j += 1
            grp = lst[i:j + 1]
            mem = {rows[k]["m"] for _, k in grp}
            if len(mem) >= 2:
                out.append({"tk": tk, "start": grp[0][0].isoformat(), "end": grp[-1][0].isoformat(),
                            "members": sorted(mem), "idx": [k for _, k in grp],
                            "lastfiled": max(rows[k].get("fd") or "" for _, k in grp)})
                i = j + 1
            else:
                i += 1
    out.sort(key=lambda c: (c["end"], len(c["members"])), reverse=True)
    return out


def main():
    members = load_members()
    ht, hskip, hdocs = house_trades(members)
    st, sskip, sdocs = senate_trades(members)
    allt, removed = dedup(ht + st)
    # compact output
    mem_ix, mems = {}, []
    rows = []
    for t in allt:
        m = t["member"]
        mk = m["bioguide"] if m else "?" + t["filer"]
        if mk not in mem_ix:
            mem_ix[mk] = len(mems)
            if m:
                nm = (m["nick"] or m["first"]) + " " + m["last"]
                mems.append({"id": m["bioguide"], "name": nm, "party": m["party"][:1] if m["party"] else "?",
                             "state": m["state"], "ch": t["chamber"], "sd": t["statedst"], "committees": m["committees"]})
            else:
                mems.append({"id": "", "name": t["filer"], "party": "?", "state": t["statedst"][:2],
                             "ch": t["chamber"], "sd": t["statedst"], "committees": []})
        band, lo, hi = amount(t["amount_raw"])
        cls = "option" if t["code"] == "OP" else ("stock" if t["code"] in ("ST", "ET", "EF") else "other")
        am_ = re.fullmatch(r"([A-Z]{1,5}) Options?", t["asset"].strip())
        if am_ or re.match(r"(call|put) option contracts", t["desc"], re.I) or re.match(r"(CALL|PUT) .*\$[\d.,]+ EXP", t["desc"]):
            cls = "option"
            if am_ and not t["ticker"]:
                t["ticker"] = {"BRKB": "BRK.B"}.get(am_.group(1), am_.group(1))
        if cls == "stock" and re.match(r"exercised .*options", t["desc"], re.I):
            t["comment"] = ("Option exercise. " + t["comment"]).strip()
        opt = option_info(t["desc"] or t["asset"]) if cls == "option" else None
        delay = (t["filed"] - t["txdate"]).days if t["filed"] and t["txdate"] else None
        if delay is not None and delay < 0:
            delay = None
        rows.append({
            "m": mem_ix[mk], "ow": t["owner"], "a": t["asset"], "tk": t["ticker"], "cls": cls, "at": t["atype"],
            "ty": t["type"], "td": t["txdate"].isoformat() if t["txdate"] else None,
            "fd": t["filed"].isoformat() if t["filed"] else None, "dl": delay, "amt": band, "lo": lo, "hi": hi,
            "ds": t["desc"][:300], "cm": t["comment"][:200], "op": opt, "am": t["amended"],
            "u": t["url"].replace("https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/", "h:").replace("https://efdsearch.senate.gov/search/view/ptr/", "s:"),
        })
        if delay is None and t["filed"] and t["txdate"] and t["txdate"] > t["filed"]:
            rows[-1]["bad"] = 1  # transaction date after filing date: likely a typo on the form
        rows[-1] = {k: v for k, v in rows[-1].items() if v is not None and v != "" and v is not False}
    rows.sort(key=lambda r: (r.get("fd") or "", r.get("td") or ""), reverse=True)
    clusters = find_clusters(rows)
    tds = sorted(r["td"] for r in rows if r.get("td") and not r.get("bad"))
    fds = sorted(r["fd"] for r in rows if r.get("fd"))
    meta = {
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes"),
        "trades": len(rows), "house_trades": sum(1 for r in rows if mems[r["m"]]["ch"] == "House"),
        "senate_trades": sum(1 for r in rows if mems[r["m"]]["ch"] == "Senate"),
        "house_filings_parsed": hdocs, "senate_reports_parsed": sdocs,
        "skipped_paper": {"House": len(hskip), "Senate": len(sskip)}, "dupes_removed": removed,
        "tx_range": [tds[0], tds[-1]] if tds else None, "filed_range": [fds[0], fds[-1]] if fds else None,
        "unmatched_members": sorted({m["name"] for m in mems if not m["id"]}),
        "clusters": len(clusters),
        "median_delay": statistics.median([r["dl"] for r in rows if r.get("dl") is not None]) if rows else None,
    }
    os.makedirs(os.path.join(PUB, "data"), exist_ok=True)
    with open(os.path.join(PUB, "data", "trades.json"), "w") as f:
        json.dump({"meta": meta, "members": mems, "trades": rows, "clusters": clusters, "skipped": hskip + sskip}, f, separators=(",", ":"))
    for fn in os.listdir(os.path.join(ROOT, "site")):
        shutil.copy(os.path.join(ROOT, "site", fn), os.path.join(PUB, fn))
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
