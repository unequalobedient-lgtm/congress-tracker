# Congress Trades

A free, always-on tracker of U.S. congressional stock & option trades, built from official disclosures:

- **House** – Periodic Transaction Reports from the House Clerk (`disclosures-clerk.house.gov`), parsed from the PDFs with `pdftotext`. Scanned/paper filings are skipped (and counted).
- **Senate** – Periodic Transaction Reports from Senate eFD (`efdsearch.senate.gov`).
- **Members** – party/state from [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators).

GitHub Actions runs `fetch.py` → `senate.py` → `build.py` daily and deploys `public/` to GitHub Pages.

**Research & learning only — not financial advice.** Disclosures are filed weeks after trades, and amounts are reported only as ranges.
