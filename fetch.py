"""Download House Clerk filing indexes, PTR PDFs, and member data. Caches everything under cache/."""
import io, os, sys, zipfile, json, time, urllib.request, concurrent.futures as cf, datetime

UA = {"User-Agent": "Mozilla/5.0 (congress-tracker; research/learning project)"}
BASE = "https://disclosures-clerk.house.gov/public_disc"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
YEARS = [int(y) for y in os.environ.get("YEARS", "2025,2026").split(",")]


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def read_index(year):
    z = zipfile.ZipFile(io.BytesIO(get(f"{BASE}/financial-pdfs/{year}FD.zip")))
    txt = z.read(f"{year}FD.txt").decode("utf-8-sig", "replace")
    rows = []
    lines = txt.splitlines()
    head = lines[0].split("\t")
    for ln in lines[1:]:
        p = ln.split("\t")
        if len(p) < len(head):
            continue
        rows.append(dict(zip(head, [x.strip() for x in p])))
    return rows


def fetch_pdf(year, docid):
    path = os.path.join(CACHE, "pdf", f"{docid}.pdf")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return docid, "cached"
    try:
        data = get(f"{BASE}/ptr-pdfs/{year}/{docid}.pdf")
    except Exception as e:
        return docid, f"error: {e}"
    with open(path, "wb") as f:
        f.write(data)
    return docid, "ok"


def main():
    os.makedirs(os.path.join(CACHE, "pdf"), exist_ok=True)
    filings = []
    for y in YEARS:
        rows = [r for r in read_index(y) if r.get("FilingType") == "P"]
        print(f"{y}: {len(rows)} PTR filings in index")
        filings += rows
    with open(os.path.join(CACHE, "filings.json"), "w") as f:
        json.dump(filings, f)
    jobs = [(int(r["Year"]), r["DocID"]) for r in filings]
    stats = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for docid, st in ex.map(lambda a: fetch_pdf(*a), jobs):
            k = st.split(":")[0]
            stats[k] = stats.get(k, 0) + 1
            if k == "error":
                print(docid, st, file=sys.stderr)
    print("PDFs:", stats)
    for name in ["legislators-current.json", "legislators-historical.json",
                 "committees-current.json", "committee-membership-current.json"]:
        try:
            with open(os.path.join(CACHE, name), "wb") as f:
                f.write(get(f"https://unitedstates.github.io/congress-legislators/{name}"))
        except Exception as e:
            print("member data failed", name, e, file=sys.stderr)
    print("done", datetime.datetime.utcnow().isoformat())


if __name__ == "__main__":
    main()
