import requests
import json
import html
import re
import unicodedata
from lxml import etree
from datetime import datetime
import pathlib

ORGID = 1756
EMAIL = "kino@mufrenstat.cz"

SOAP_URL = "https://data-centrala.colosseum.eu/ColosseumDataService.asmx"

WEB_BASE = "https://www.kinofrenstat.cz/program/"
RES_BASE = "https://online.colosseum.eu/kulturafrenstat/standard/Hall/Index/"

OUTPUT_FILE_PATH = "docs/kino-frenstat.json" # absolute path

# Omezení - jednou za 10 minut

def slugify(text):
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[-\s]+", "-", text)
    return text

def soap_request():
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
    }

    r = requests.post(SOAP_URL, data=body.encode("utf-8"), headers=headers, timeout=180)
    r.raise_for_status()
    return r.text

def extract_export_xml(soap_response):
    parser = etree.XMLParser(recover=True)
    root = etree.fromstring(soap_response.encode("utf-8"), parser)
    doc_nodes = root.xpath("//*[local-name()='DocumentResult']")
    raw_xml = html.unescape(doc_nodes[0].text)
    return etree.fromstring(raw_xml.encode("utf-8"), parser)

def main():

    pathlib.Path("docs").mkdir(exist_ok=True)

    soap_resp = soap_request()
    export_root = extract_export_xml(soap_resp)

    # ===== VENUE =====
    kino_objid = None
    for venue in export_root.xpath("//venues/Table"):
        if "kino" in (venue.findtext("EXNAZEV") or "").lower():
            kino_objid = int(venue.findtext("OBJID"))
            break

    # ===== TITLES =====
    titles = {}
    for title in export_root.xpath("//titlesEx/Table"):
        tprid = int(title.findtext("TPRID", 0))

        titles[tprid] = {
            "name": title.findtext("NAZEV", ""),
            "dabing": int(title.findtext("FILM_DABING", 0)),
            "titulky": int(title.findtext("FILM_TITULKY", 0)),
            "description": title.findtext("POZNAMKA", ""),
            "image": title.findtext("OBRAZEK", "")
        }

    # ===== EVENTS =====
    program = []

    for event in export_root.xpath("//eventsEx/Table"):
        objid = int(event.findtext("OBJID", 0))
        if objid != kino_objid:
            continue

        tprid = int(event.findtext("TPRID", 0))
        prdid = event.findtext("PRDID", "")
        datum = event.findtext("DATUM_OD", "")

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

        try:
            dt = datetime.fromisoformat(datum)
            datum_out = dt.strftime("%Y-%m-%d %H:%M")
        except:
            datum_out = datum

        slug = slugify(title_data["name"])

        program.append({
            "datetime": datum_out,
            "title": title_data["name"],
            "language": lang,
            "description": title_data["description"],
            "image": title_data["image"],
            "program_url": f"{WEB_BASE}{tprid}-{slug}",
            "reservation_url": f"{RES_BASE}{prdid}"
        })

    program.sort(key=lambda x: x["datetime"])

    out = json.dumps(program, indent=2, ensure_ascii=False)
    print(out)
    # 'w' znamená write (zápis)
    with open(OUTPUT_FILE_PATH, "w", encoding="utf-8") as soubor:
        soubor.write(out)

if __name__ == "__main__":
    main()
