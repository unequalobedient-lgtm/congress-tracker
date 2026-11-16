"""Senate eFD fetcher: accept the terms page, list PTRs, cache each PTR's HTML under cache/senate/."""
import os, re, json, time, http.cookiejar, urllib.request, urllib.parse, concurrent.futures as cf

ROOT = "https://efdsearch.senate.gov"
UA = "Mozilla/5.0 (congress-tracker; research/learning project)"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "senate")


class Session:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def req(self, url, data=None, headers=None, tries=4):
        h = {"User-Agent": UA, "Referer": ROOT + "/search/"}
        h.update(headers or {})
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        for i in range(tries):
            try:
                with self.op.open(urllib.request.Request(url, data=body, headers=h), timeout=60) as r:
                    return r.read().decode("utf-8", "replace")
            except Exception:
                if i == tries - 1:
                    raise
                time.sleep(3 * (i + 1))

    def cookie(self, name):
        for c in self.jar:
            if c.name == name:
                return c.value
        return ""

    def accept_terms(self):
        home = self.req(ROOT + "/search/home/")
        m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', home)
        if not m:
            raise RuntimeError("Senate terms page format changed")
        self.req(ROOT + "/search/home/", {"prohibition_agreement": "1", "csrfmiddlewaretoken": m.group(1)},
                 {"Referer": ROOT + "/search/home/"})


def list_ptrs(s, start_date="01/01/2025 00:00:00"):
    out, start = [], 0
    while True:
        csrf = s.cookie("csrftoken")
        raw = s.req(ROOT + "/search/report/data/", {
            "start": str(start), "length": "100", "report_types": "[11]", "filer_types": "[]",
            "submitted_start_date": start_date, "submitted_end_date": "", "candidate_state": "",
            "senator_state": "", "office_id": "", "first_name": "", "last_name": "", "csrftoken": csrf,
        }, {"X-CSRFToken": csrf})
        d = json.loads(raw)
        rows = d.get("data", [])
        for first, last, full, link, filed in rows:
            m = re.search(r'href="([^"]+)"', link)
            title = re.sub(r"<[^>]+>", "", link).strip()
            out.append({"first": first, "last": last, "full": full, "url": m.group(1) if m else "",
                        "title": title, "filed": filed})
        start += len(rows)
        if not rows or start >= d.get("recordsTotal", 0):
            break
    return out


def main():
    os.makedirs(CACHE, exist_ok=True)
    s = Session()
    s.accept_terms()
    reports = list_ptrs(s)
    print(f"Senate: {len(reports)} PTR reports listed")
    with open(os.path.join(CACHE, "_index.json"), "w") as f:
        json.dump(reports, f)

    def grab(r):
        if "/view/ptr/" not in r["url"]:
            return "paper"
        uid = r["url"].rstrip("/").split("/")[-1]
        p = os.path.join(CACHE, uid + ".html")
        if os.path.exists(p) and os.path.getsize(p) > 1000:
            return "cached"
        html = s.req(ROOT + r["url"])
        if "<table" not in html:
            return "notable"
        with open(p, "w") as f:
            f.write(html)
        return "ok"

    stats = {}
    with cf.ThreadPoolExecutor(4) as ex:
        for st in ex.map(grab, reports):
            stats[st] = stats.get(st, 0) + 1
    print("Senate pages:", stats)


if __name__ == "__main__":
    main()
