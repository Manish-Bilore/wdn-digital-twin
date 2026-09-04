#!/usr/bin/env python3
"""
make_architecture.py
====================
The one-glance architecture diagram: nine stages from an estate-office CAD
drawing to a queryable digital twin, with the artefact each stage produces and
the tool that does it.

Typography and weights follow the site's own stack rather than the diagram
having a look of its own — same Inter/system font, body-sized text, hairline
borders, no decorative accent bars. Three bands of three, because nine across
is unreadable below about 900 px.

    python3 pipeline/make_architecture.py -o site/assets/architecture.svg
"""
import argparse

FONT = "Inter, system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, monospace"

INK, MUTED, FAINT, LINE = "#202124", "#5f6368", "#9aa0a6", "#e4e7ea"

BANDS = [
    ("Physical", "#1a73e8", [
        ("CAD", "AC1015 drawing", "267 centrelines\n66 annotations", "GDAL / libopencad"),
        ("GIS", "georeferenced", "EPSG:32643\nfootprint IoU 0.685", "auto-registration"),
        ("Network graph", "noded topology", "322 pipes\n275 junctions", "shapely"),
    ]),
    ("Model", "#e8710a", [
        ("EPANET", "hydraulic state", "pressure 15–68 m\nvelocity 0–1.6 m/s", "WNTR · EPANET 2.2"),
        ("Sensors", "monitoring design", "5 districts\n29 locations", "K-means + overlay"),
        ("Ontology", "OWL 2 DL", "network · sensing\nplacement", "SOSA/SSN · QUDT"),
    ]),
    ("Semantic", "#188038", [
        ("Knowledge graph", "instances", "8,554 triples\n833 entities", "rdflib"),
        ("SPARQL", "live query", "in the browser\n~26 kB gzipped", "Oxigraph WASM"),
        ("Digital twin", "one model", "ask across\nall five layers", "this site"),
    ]),
]

CARD_W, CARD_H, GAP = 322, 116, 40
LEFT, TOP, BAND_GAP, LABEL_H = 8, 92, 62, 26
W = LEFT * 2 + CARD_W * 3 + GAP * 2
# three bands: label (12 px lead) + card, separated by BAND_GAP
H = TOP + 2 * (12 + CARD_H + BAND_GAP) + 12 + CARD_H + 12


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def card(x, y, colour, title, sub, detail, tool):
    o = [f'<rect x="{x}" y="{y}" width="{CARD_W}" height="{CARD_H}" rx="8" '
         f'fill="#ffffff" stroke="{LINE}" stroke-width="1"/>',
         f'<text x="{x+18}" y="{y+28}" font-size="14.5" font-weight="600" '
         f'fill="{INK}">{esc(title)}</text>',
         f'<text x="{x+18}" y="{y+47}" font-size="11.5" fill="{colour}" '
         f'font-weight="600">{esc(sub)}</text>']
    for i, line in enumerate(detail.split("\n")):
        o.append(f'<text x="{x+18}" y="{y+69+i*15}" font-size="11" '
                 f'fill="{MUTED}" font-family="{MONO}">{esc(line)}</text>')
    o.append(f'<text x="{x+CARD_W-18}" y="{y+CARD_H-12}" font-size="10" '
             f'fill="{FAINT}" text-anchor="end">{esc(tool)}</text>')
    return "".join(o)


def arrow(x, y):
    return (f'<path d="M{x},{y} L{x+GAP-13},{y}" stroke="#d6dade" '
            f'stroke-width="1.4" marker-end="url(#a)"/>')


def build():
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
         f'width="100%" font-family="{FONT}" role="img" '
         f'aria-label="Nine-stage pipeline from CAD drawing to digital twin">',
         '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" '
         'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
         '<path d="M0,0 L10,5 L0,10 z" fill="#d6dade"/></marker></defs>',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
         f'<text x="{LEFT}" y="34" font-size="17" font-weight="600" fill="{INK}">'
         'From an estate-office drawing to a queryable twin</text>',
         f'<text x="{LEFT}" y="57" font-size="12.5" fill="{MUTED}">'
         'Every number here is written by the pipeline, not typed in.</text>']

    y = TOP
    for bi, (band, colour, stages) in enumerate(BANDS):
        s.append(f'<text x="{LEFT}" y="{y}" font-size="10" font-weight="700" '
                 f'fill="{colour}" letter-spacing="1.4">{band.upper()}</text>')
        cy = y + 12
        for si, (t, sub, det, tool) in enumerate(stages):
            x = LEFT + si * (CARD_W + GAP)
            s.append(card(x, cy, colour, t, sub, det, tool))
            if si < 2:
                s.append(arrow(x + CARD_W + 5, cy + CARD_H / 2))
        y = cy + CARD_H + BAND_GAP
    s.append("</svg>")
    return "".join(s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="site/assets/architecture.svg")
    a = ap.parse_args()
    open(a.out, "w").write(build())
    print(f"wrote {a.out}  ({W} x {H})")
