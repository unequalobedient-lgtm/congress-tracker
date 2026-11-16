"""Parse House Clerk PTR text (pdftotext -layout) into trade rows."""
import re

DATE = r"\d{1,2}/\d{1,2}/\d{4}"
TX = re.compile(
    r"^(?P<lead>\s*)(?:(?P<owner>SP|JT|DC)\s+)?(?P<asset>.*?)\s*"
    r"(?<!\S)(?P<type>S \(partial\)|P|S|E)\s+(?P<txd>" + DATE + r")\s+(?P<nd>" + DATE + r")"
    r"(?:\s+(?P<amt>.*?))?\s*$")
LABEL = re.compile(r"^\s*([A-Z])(?:\s+([A-Z]))?\s*:\s?(.*)$")
STOP = re.compile(r"^\s*(\* For the complete list|I\s+P\s+O\s*$|C\s+S\s*$|I\s+V\s+D\s*$|Digitally Signed)")
HEADER = re.compile(r"^\s*(ID\s+Owner\s+Asset|Type\s+Date|Gains >|\$200\?|Filing ID #)")
LABELS = {("F", "S"): "status", ("D", None): "desc", ("C", None): "comment",
          ("L", None): "location", ("S", "O"): "subholding"}


def parse(text):
    head = {}
    for key, pat in [("name", r"Name:\s*(.+)"), ("status", r"Status:\s*(.+)"),
                     ("statedst", r"State/District:\s*(\S+)"), ("signed", r"Digitally Signed:.*?(" + DATE + r")")]:
        m = re.search(pat, text)
        head[key] = m.group(1).strip() if m else ""
    m = re.search(r"Filing ID #(\d+)", text)
    head["filing_id"] = m.group(1) if m else ""
    rows, cur, last_label, stopped = [], None, None, False
    for line in text.splitlines():
        if HEADER.match(line):
            continue
        if STOP.match(line):
            if cur:
                rows.append(cur); cur = None
            last_label = None
            continue
        m = TX.match(line)
        if m and m.group("asset") is not None:
            if cur:
                rows.append(cur)
            amt = (m.group("amt") or "").strip()
            cur = {"owner": m.group("owner") or "", "asset": m.group("asset").strip(), "type": m.group("type"),
                   "txdate": m.group("txd"), "notdate": m.group("nd"), "amount": amt,
                   "status": "", "desc": "", "comment": "", "subholding": "", "location": ""}
            last_label = None
            continue
        if not cur:
            continue
        if not line.strip():
            last_label = "blank" if last_label else None
            continue
        lm = LABEL.match(line)
        if lm and (lm.group(1), lm.group(2)) in LABELS:
            last_label = LABELS[(lm.group(1), lm.group(2))]
            cur[last_label] = lm.group(3).strip()
            continue
        if last_label in ("desc", "comment"):
            cur[last_label] += " " + line.strip()
            continue
        if last_label is None:  # continuation of asset / amount columns
            for tok in re.split(r"\s{3,}", line.strip()):
                if tok.startswith("$") or tok.startswith("Over"):
                    cur["amount"] = (cur["amount"] + " " + tok).strip()
                else:
                    cur["asset"] = (cur["asset"] + " " + tok).strip()
    if cur:
        rows.append(cur)
    return head, rows
