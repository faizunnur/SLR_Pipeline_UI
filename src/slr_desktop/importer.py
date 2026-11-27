"""
importer.py -- read records a researcher exported from a database website.

Scopus, Web of Science, IEEE Xplore, Embase and most library databases only give
an API key to people whose institution pays for one, but every one of them lets
a signed-in reader export a result list as a file. This module turns such a file
into the same record shape search.py produces (source, title, authors, year,
venue, doi, url, abstract), so an exported file can join a review exactly like
an API search result: de-duplicated, screened, counted in the PRISMA flow.

Formats:
  .ris  .txt    RIS tagged text (Scopus, Web of Science, IEEE Xplore, EndNote, Zotero)
  .nbib         MEDLINE tagged text (PubMed "Citation manager" export)
  .bib          BibTeX
  .csv  .tsv    a spreadsheet with a title column (Scopus and IEEE Xplore CSV exports)

Nothing here touches the network or a review folder: parse_file() only reads the
one file it is given and returns plain dicts.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

FIELDS = ("title", "authors", "year", "venue", "doi", "url", "abstract")
MAX_BYTES = 60 * 1024 * 1024
FORMATS = {".ris": "RIS", ".txt": "RIS", ".nbib": "MEDLINE", ".bib": "BibTeX",
           ".bibtex": "BibTeX", ".csv": "CSV", ".tsv": "CSV"}


class ImportProblem(Exception):
    """The file cannot be imported; the message says why in plain words."""


# ---------------------------------------------------------------------------
# cleaning shared by every format
# ---------------------------------------------------------------------------
_DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.I)
_YEAR_RE = re.compile(r"(?<!\d)(1[5-9]\d\d|20\d\d)(?!\d)")


def _text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_doi(value) -> str:
    """'https://doi.org/10.1/x', 'doi:10.1/X.' and '10.1/x [doi]' all become '10.1/x'."""
    m = _DOI_RE.search(str(value or ""))
    return m.group(0).rstrip(".,;)]}>\"'").lower() if m else ""


def clean_year(value) -> str:
    m = _YEAR_RE.search(str(value or ""))
    return m.group(1) if m else ""


def _record(title="", authors=(), year="", venue="", doi="", url="", abstract="") -> dict:
    if isinstance(authors, str):
        authors = [authors]
    names = [_text(a) for a in authors if _text(a)]
    url = _text(url)
    return {"title": _text(title).rstrip("."), "authors": "; ".join(names), "year": clean_year(year),
            "venue": _text(venue), "doi": clean_doi(doi) or clean_doi(url),
            "url": url if url.lower().startswith(("http://", "https://")) else "",
            "abstract": _text(abstract)}


# ---------------------------------------------------------------------------
# RIS and MEDLINE: one "TAG - value" per line, continuation lines indented
# ---------------------------------------------------------------------------
_RIS_LINE = re.compile(r"^([A-Z][A-Z0-9])  ?- ?(.*)$")
_MEDLINE_LINE = re.compile(r"^([A-Z]{2,4}) *- (.*)$")


def _tagged(text: str, line_re, end_tag: str | None, start_tag: str) -> list[dict]:
    """Records as {tag: [values]}. A record ends at `end_tag` (RIS 'ER') or, for
    formats without one, when `start_tag` appears again or a blank line follows."""
    records, current, last = [], {}, None
    for raw in text.splitlines():
        line = raw.rstrip()
        m = line_re.match(line)
        if m:
            tag, value = m.group(1), m.group(2).strip()
            if end_tag and tag == end_tag:
                if current:
                    records.append(current)
                current, last = {}, None
                continue
            if tag == start_tag and start_tag in current:
                records.append(current)
                current = {}
            current.setdefault(tag, []).append(value)
            last = tag
        elif not line.strip():
            if not end_tag and current:
                records.append(current)
                current = {}
            last = None
        elif last and current:
            current[last][-1] = (current[last][-1] + " " + line.strip()).strip()
    if current:
        records.append(current)
    return records


def _first(rec: dict, *tags) -> str:
    for tag in tags:
        for value in rec.get(tag, []):
            if value.strip():
                return value
    return ""


def parse_ris(text: str) -> list[dict]:
    out = []
    for rec in _tagged(text, _RIS_LINE, "ER", "TY"):
        authors = rec.get("AU") or rec.get("A1") or rec.get("A2") or []
        out.append(_record(
            title=_first(rec, "TI", "T1", "CT", "BT"), authors=authors,
            year=_first(rec, "PY", "Y1", "DA"),
            venue=_first(rec, "JO", "JF", "T2", "JA", "J2", "T3", "BT"),
            doi=_first(rec, "DO") or next((v for v in rec.get("M3", []) + rec.get("N1", [])
                                           if clean_doi(v)), ""),
            url=_first(rec, "UR", "L2", "L1"), abstract=_first(rec, "AB", "N2")))
    return out


def parse_medline(text: str) -> list[dict]:
    out = []
    for rec in _tagged(text, _MEDLINE_LINE, None, "PMID"):
        ids = rec.get("LID", []) + rec.get("AID", [])
        doi = next((v for v in ids if "[doi]" in v.lower()), "")
        pmid = _first(rec, "PMID")
        out.append(_record(
            title=_first(rec, "TI", "BTI"), authors=rec.get("FAU") or rec.get("AU") or [],
            year=_first(rec, "DP", "DEP"), venue=_first(rec, "JT", "TA"), doi=doi,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid.isdigit() else "",
            abstract=_first(rec, "AB")))
    return out


# ---------------------------------------------------------------------------
# BibTeX
# ---------------------------------------------------------------------------
_LATEX_ACCENT = re.compile(r"""\\[`'^"~=.uvHtcdbkr]\s*\{?\s*\\?([A-Za-z])\s*\}?""")
_LATEX_CMD = re.compile(r"\\[A-Za-z]+\s*\{([^{}]*)\}")
_LATEX_SYMBOLS = {r"\&": "&", r"\%": "%", r"\_": "_", r"\$": "$", r"\#": "#", "~": " ",
                  "--": "-", r"\ss": "ss", r"\o": "o", r"\O": "O", r"\ae": "ae", r"\AE": "AE"}


def _detex(value: str) -> str:
    value = _LATEX_ACCENT.sub(r"\1", value)
    for _ in range(3):
        value = _LATEX_CMD.sub(r"\1", value)
    for tex, plain in _LATEX_SYMBOLS.items():
        value = value.replace(tex, plain)
    return _text(value.replace("{", "").replace("}", "").replace("\\", ""))


def _bib_value(text: str, i: int) -> tuple[str, int]:
    """Read one field value starting at text[i]: {braced}, "quoted", a bare word,
    or several of those joined with #. Returns (value, index after it)."""
    parts, n = [], len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        ch = text[i]
        if ch == "{":
            depth, j = 1, i + 1
            while j < n and depth:
                depth += (text[j] == "{") - (text[j] == "}")
                j += 1
            parts.append(text[i + 1:j - 1])
            i = j
        elif ch == '"':
            depth, j = 0, i + 1
            while j < n and not (text[j] == '"' and depth == 0 and text[j - 1] != "\\"):
                depth += (text[j] == "{") - (text[j] == "}")
                j += 1
            parts.append(text[i + 1:j])
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ",}#\n":
                j += 1
            parts.append(text[i:j].strip())
            i = j
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i < n and text[i] == "#":
            i += 1
            continue
        break
    return "".join(parts), i


def parse_bibtex(text: str) -> list[dict]:
    out = []
    for m in re.finditer(r"@\s*([A-Za-z]+)\s*[{(]", text):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        i = text.find(",", m.end())
        if i < 0:
            continue
        i += 1
        fields: dict[str, str] = {}
        while i < len(text):
            fm = re.compile(r"\s*([A-Za-z][\w\-]*)\s*=\s*").match(text, i)
            if not fm:
                break
            value, i = _bib_value(text, fm.end())
            fields.setdefault(fm.group(1).lower(), value)
            while i < len(text) and text[i] in " \t\r\n":
                i += 1
            if i < len(text) and text[i] == ",":
                i += 1
            else:
                break
        authors = [_detex(a) for a in re.split(r"\s+and\s+", fields.get("author", "")
                                               or fields.get("editor", "")) if a.strip()]
        out.append(_record(
            title=_detex(fields.get("title", "")), authors=authors,
            year=fields.get("year", "") or fields.get("date", ""),
            venue=_detex(fields.get("journal", "") or fields.get("journaltitle", "")
                         or fields.get("booktitle", "") or fields.get("series", "")),
            doi=fields.get("doi", ""), url=fields.get("url", ""),
            abstract=_detex(fields.get("abstract", ""))))
    return out


# ---------------------------------------------------------------------------
# CSV / TSV
# ---------------------------------------------------------------------------
# Column names the big databases use, lower-cased; the first one present wins.
_COLUMNS = {
    "title": ("title", "document title", "article title", "item title", "ti", "primary title"),
    "authors": ("authors", "author", "author full names", "author names", "au", "creators"),
    "year": ("year", "publication year", "pubyear", "py", "date", "publication date", "cover date"),
    "venue": ("source title", "publication title", "journal", "journal title", "venue", "so",
              "source", "book title", "conference name", "journal/book"),
    "doi": ("doi", "di", "digital object identifier"),
    "url": ("link", "url", "pdf link", "article url", "urls"),
    "abstract": ("abstract", "ab", "abstract note", "summary"),
}


def parse_csv(text: str) -> list[dict]:
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel_tab if sample.count("\t") > sample.count(",") else csv.excel
    reader = csv.reader(io.StringIO(text, newline=""), dialect)
    try:
        header = [_text(c).lower() for c in next(reader)]
    except StopIteration:
        return []
    where = {field: next((header.index(name) for name in names if name in header), None)
             for field, names in _COLUMNS.items()}
    if where["title"] is None:
        raise ImportProblem(
            "This spreadsheet has no title column. The first row must name the columns, and one "
            "of them must be called Title (found: " + ", ".join(c for c in header[:12] if c) + ").")
    out = []
    for row in reader:
        get = lambda f: row[where[f]] if where[f] is not None and where[f] < len(row) else ""  # noqa: E731
        authors = [a for a in re.split(r"\s*;\s*", get("authors")) if a]
        out.append(_record(title=get("title"), authors=authors, year=get("year"), venue=get("venue"),
                           doi=get("doi"), url=get("url"), abstract=get("abstract")))
    return out


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    return raw.decode("cp1252", errors="replace")


def detect_format(path: Path, text: str) -> str:
    """By content first (an RIS export saved as .txt is common), then by extension."""
    head = text[:4000]
    if re.search(r"^TY  - ", head, re.M):
        return "RIS"
    if re.search(r"^PMID *- ", head, re.M):
        return "MEDLINE"
    if re.search(r"^\s*@\s*[A-Za-z]+\s*[{(]", head, re.M):
        return "BibTeX"
    if FORMATS.get(path.suffix.lower()) == "CSV":
        return "CSV"
    raise ImportProblem(
        f"{path.name} does not look like a reference export. Export the results again as "
        "RIS, BibTeX or CSV (in the database, choose Export, then one of those formats).")


def parse_file(path) -> dict:
    """Read one exported file. Returns
    {"format", "records": [dict of FIELDS], "n_no_title": how many entries were
    dropped for having no title}."""
    path = Path(path)
    if not path.is_file():
        raise ImportProblem(f"{path} is not a file.")
    if path.stat().st_size > MAX_BYTES:
        raise ImportProblem(f"{path.name} is larger than {MAX_BYTES // (1024 * 1024)} MB. "
                            "Export the results in smaller batches and import each one.")
    try:
        text = _decode(path.read_bytes())
    except OSError as e:
        raise ImportProblem(f"Could not read {path.name}: {e}")
    fmt = detect_format(path, text)
    parsed = {"RIS": parse_ris, "MEDLINE": parse_medline, "BibTeX": parse_bibtex, "CSV": parse_csv}[fmt](text)
    records = [r for r in parsed if r["title"]]
    if not records:
        raise ImportProblem(f"No records with a title were found in {path.name} (read as {fmt}).")
    return {"format": fmt, "records": records, "n_no_title": len(parsed) - len(records)}
