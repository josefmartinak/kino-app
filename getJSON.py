import requests
import json
import html
import re
import unicodedata
from lxml import etree
from datetime import datetime, timezone
from pathlib import Path
import sys

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ORGID = 1756
EMAIL = "kino@mufrenstat.cz"

SOAP_URL = "https://data-centrala.colosseum.eu/ColosseumDataService.asmx"

WEB_BASE = "https://www.kinofrenstat.cz/program/"

# GitHub Pages publikuje /docs jako statický web
OUTPUT_DIR = Path("docs")
OUTPUT_JSON = OUTPUT_DIR / "kino-frenstat.json"
OUTPUT_META = OUTPUT_DIR / "kino-frenstat.meta.json"
# https://josefmartinak.github.io/kino-app/kino-frenstat.json


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[-\s]+", "-", text)
    return text


def build_session() -> requests.Session:
    session = requests.Session()

    retry = Retry(
        total=6,
        connect=6,
        read=6,
        backoff_factor=5,  # 5s, 10s, 20s...
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def soap_request() -> str:
    args = f"ORGID={ORGID};NAME=venues,titlesEx,eventsEx;EMAIL={EMAIL}"

    body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema"
               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <Document xmlns="http://colosseum.eu/ColosseumDataService">
      <id>GET_EXPORT</id>
      <args>{args}</args>
      <data></data>
    </Document>
  </soap:Body>
</soap:Envelope>"""

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "http://colosseum.eu/ColosseumDataService/Document",
        "User-Agent": "kino-frenstat-json/1.1",
    }

    session = build_session()

    # důležité: timeout jako (connect, read)
    # connect 20s, read 900s (15 minut) – SOAP export může být velký/pomalý
    r = session.post(SOAP_URL, data=body.encode("utf-8"), headers=headers, timeout=(20, 900))
    r.raise_for_status()
    return r.text


_AMP_FIX_RE = re.compile(r'&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)')


def sanitize_xml_ampersands(s: str) -> str:
    """
    Colosseum export někdy vrací nevalidní XML (typicky neescapované '&' v textu).
    Tohle opraví jen "holé" ampersandy, aby XML bylo well-formed.
    """
    return _AMP_FIX_RE.sub("&amp;", s)


def extract_export_xml(soap_response: str) -> etree._Element:
    # 1) parse SOAP (tady bývá XML OK)
    soap_parser = etree.XMLParser(recover=True, huge_tree=True)
    root = etree.fromstring(soap_response.encode("utf-8"), soap_parser)

    doc_nodes = root.xpath("//*[local-name()='DocumentResult']")
    if not doc_nodes or doc_nodes[0].text is None:
        raise RuntimeError("SOAP response does not contain DocumentResult text.")

    # 2) vybalit vnitřní XML export
    raw_xml = html.unescape(doc_nodes[0].text)

    # uložit pro debug
    Path("raw.xml").write_text(raw_xml, encoding="utf-8")

    # 3) sanitizace a strict parse (bez recover), aby se strom nerozbil
    cleaned = sanitize_xml_ampersands(raw_xml)

    strict_parser = etree.XMLParser(recover=False, huge_tree=True)
    try:
        return etree.fromstring(cleaned.encode("utf-8"), strict_parser)
    except Exception as e:
        # fallback: ještě uložit očištěnou verzi a zkusit recover (jen aby se nevypnul export úplně)
        Path("raw.cleaned.xml").write_text(cleaned, encoding="utf-8")
        recover_parser = etree.XMLParser(recover=True, huge_tree=True)
        try:
            return etree.fromstring(cleaned.encode("utf-8"), recover_parser)
        except Exception:
            raise RuntimeError(f"Export XML parse failed even after sanitization: {e}") from e


def _name_norm(s: str) -> str:
    s = (s or "").strip().lower()
    # odstraní diakritiku pro robustnější match (kinosál vs kinosal)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s


def generate_program(export_root: etree._Element) -> list[dict]:
    # ===== VENUE =====
    # export může mít názvy jako "Kinosál" (bez přesného substringu "kino")
    kino_objid = None
    candidates: list[tuple[int, int, str]] = []

    for venue in export_root.xpath("//venues//Table"):
        name_raw = venue.findtext("EXNAZEV") or ""
        name = _name_norm(name_raw)
        objid = int(venue.findtext("OBJID") or 0)
        if not objid:
            continue

        # priority matching
        if "kino frenstat" in name or "kino frenstat" in name:
            candidates.append((3, objid, name_raw))
        elif "kinosal" in name:
            candidates.append((2, objid, name_raw))
        elif "kino" in name:
            candidates.append((1, objid, name_raw))

    if candidates:
        candidates.sort(reverse=True)
        kino_objid = candidates[0][1]

    if not kino_objid:
        raise RuntimeError("Kino OBJID not found in venues export (no venue matched kino/kinosal).")

    # ===== TITLES =====
    titles: dict[int, dict] = {}
    for title in export_root.xpath("//titlesEx//Table"):
        tprid = int(title.findtext("TPRID", "0") or 0)
        if not tprid:
            continue

        titles[tprid] = {
            "name": title.findtext("NAZEV", "") or "",
            "dabing": int(title.findtext("FILM_DABING", "0") or 0),
            "titulky": int(title.findtext("FILM_TITULKY", "0") or 0),
            "description": title.findtext("POZNAMKA", "") or "",
            "image": title.findtext("OBRAZEK", "") or "",
        }

    # ===== EVENTS =====
    program: list[dict] = []

    # POZOR: u rozbitého XML se po "recover" mohou Table elementy zanořit,
    # proto používáme //eventsEx//Table (descendants), ne jen přímé děti.
    for event in export_root.xpath("//eventsEx//Table"):
        objid = int(event.findtext("OBJID", "0") or 0)
        if objid != kino_objid:
            continue

        tprid = int(event.findtext("TPRID", "0") or 0)
        datum = (event.findtext("DATUM_OD", "") or "").strip()

        title_data = titles.get(tprid)
        if not title_data:
            continue

        # jazyk
        if title_data["dabing"] == 1:
            lang = "CZ dabing"
        elif title_data["titulky"] == 1:
            lang = "Originál + titulky"
        else:
            lang = "Originál"

        # datum výstup
        datum_out = datum
        sort_key = datum  # fallback
        try:
            # Colosseum často vrací ISO; když je bez timezone, fromisoformat stále projde (naivní datetime)
            dt = datetime.fromisoformat(datum)
            datum_out = dt.strftime("%Y-%m-%d %H:%M")
            sort_key = dt.isoformat()
        except Exception:
            pass

        slug = slugify(title_data["name"])

        program.append(
            {
                "datetime": datum_out,
                "title": title_data["name"],
                "language": lang,
                "description": title_data["description"],
                "image": title_data["image"],
                "program_url": f"{WEB_BASE}{tprid}-{slug}",
                "_sort": sort_key,  # interní; odstraníme níž
            }
        )

    program.sort(key=lambda x: x.get("_sort", ""))
    for item in program:
        item.pop("_sort", None)

    return program


def atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        soap_resp = soap_request()
        export_root = extract_export_xml(soap_resp)
        program = generate_program(export_root)

        out_json = json.dumps(program, indent=2, ensure_ascii=False)
        atomic_write(OUTPUT_JSON, out_json)

        meta = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "items": len(program),
        }
        atomic_write(OUTPUT_META, json.dumps(meta, indent=2, ensure_ascii=False))

        print(f"OK: wrote {OUTPUT_JSON} ({len(program)} items)")
        return 0

    except Exception as e:
        # DŮLEŽITÉ: workflow neshazujeme; zůstane poslední validní JSON
        print(f"ERROR: generation failed: {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
