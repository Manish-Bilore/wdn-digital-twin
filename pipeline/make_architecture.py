#!/usr/bin/env python3
"""
make_architecture.py
====================
Generates the one-glance architecture diagram: the nine stages that take an
estate-office CAD drawing to a queryable digital twin, with the artefact each
stage produces and the tool that does it.

Laid out as three bands of three so it stays legible on a phone; a single row
of nine would be unreadable below about 900 px.

    python3 pipeline/make_architecture.py -o site/assets/architecture.svg
"""
import argparse

W, H = 1180, 604
BANDS = [
    ("Physical", "#1a73e8", [
        ("CAD", "AC1015 drawing", "267 centrelines\n66 annotations", "GDAL / libopencad"),
        ("GIS", "georeferenced", "EPSG:32643\nIoU 0.685", "footprint registration"),
        ("Network graph", "noded topology", "322 pipes\n275 junctions", "shapely"),
    ]),
    ("Model", "#e8710a", [
        ("EPANET", "hydraulic state", "pressure 15-68 m\nvelocity 0-1.6 m/s", "WNTR / EPANET 2.2"),
        ("Sensors", "monitoring design", "5 districts\n29 locations", "K-means + overlay"),
        ("Ontology", "OWL 2 DL", "network + sensing\n+ placement", "SOSA/SSN · QUDT"),
    ]),
    ("Semantic", "#188038", [
        ("Knowledge graph", "instances", "8,554 triples\n833 entities", "rdflib"),
        ("SPARQL", "live query", "in the browser\n~26 kB gzipped", "Oxigraph WASM"),
        ("Digital twin", "one model", "ask across\nall five layers", "this site"),
    ]),
]

CARD_W, CARD_H, GAP = 320, 128, 42
LEFT, TOP, BAND_GAP = 78, 96, 42


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def card(x, y, colour, title, sub, detail, tool):
    o = [f'<rect x="{x}" y="{y}" width="{CARD_W}" height="{CARD_H}" rx="10" '
         f'fill="#ffffff" stroke="{colour}" stroke-width="1.6"/>',
         f'<rect x="{x}" y="{y}" width="5" height="{CARD_H}" rx="2.5" fill="{colour}"/>',
         f'<text x="{x+20}" y="{y+30}" font-size="17" font-weight="700" '
         f'fill="#202124">{esc(title)}</text>',
         f'<text x="{x+20}" y="{y+50}" font-size="12.5" fill="{colour}" '
         f'font-weight="600">{esc(sub)}</text>']
    for i, line in enumerate(detail.split("\n")):
        o.append(f'<text x="{x+20}" y="{y+72+i*16}" font-size="12" '
                 f'fill="#5f6368" font-family="ui-monospace,Menlo,monospace">'
                 f'{esc(line)}</text>')
    o.append(f'<text x="{x+CARD_W-18}" y="{y+CARD_H-13}" font-size="11" '
             f'fill="#9aa0a6" text-anchor="end">{esc(tool)}</text>')
    return "".join(o)


def arrow_right(x, y):
    return (f'<path d="M{x},{y} L{x+GAP-14},{y}" stroke="#c8ccd0" '
            f'stroke-width="2" marker-end="url(#a)"/>')


def arrow_wrap(x_end, y_row, y_next):
    """Serpentine connector from the end of one band to the start of the next."""
    mid = y_row + CARD_H + (y_next - y_row - CARD_H) / 2
    return (f'<path d="M{x_end},{y_row+CARD_H/2} '
            f'C{x_end+46},{y_row+CARD_H/2} {x_end+46},{mid} {W-52},{mid} '
            f'L{LEFT-26},{mid} '
            f'C{LEFT-52},{mid} {LEFT-52},{y_next+CARD_H/2} {LEFT-8},{y_next+CARD_H/2}" '
            f'fill="none" stroke="#dadce0" stroke-width="2" '
            f'stroke-dasharray="5 4" marker-end="url(#a)"/>')


def build():
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
         f'width="100%" role="img" aria-label="Pipeline architecture">',
         '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" '
         'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
         '<path d="M0,0 L10,5 L0,10 z" fill="#c8ccd0"/></marker></defs>',
         f'<rect width="{W}" height="{H}" fill="#fbfbfc"/>',
         f'<text x="{LEFT}" y="44" font-size="20" font-weight="700" fill="#202124">'
         'From an estate-office drawing to a queryable twin</text>',
         f'<text x="{LEFT}" y="68" font-size="13" fill="#5f6368">'
         'Nine stages. Every number below is produced by the pipeline, not typed in.</text>']

    ys = []
    for bi, (band, colour, stages) in enumerate(BANDS):
        y = TOP + bi * (CARD_H + BAND_GAP)
        ys.append(y)
        s.append(f'<text x="{LEFT-56}" y="{y+CARD_H/2+4}" font-size="11.5" '
                 f'font-weight="700" fill="{colour}" transform="rotate(-90 '
                 f'{LEFT-56} {y+CARD_H/2+4})" text-anchor="middle" '
                 f'letter-spacing="1.2">{band.upper()}</text>')
        for si, (t, sub, det, tool) in enumerate(stages):
            x = LEFT + si * (CARD_W + GAP)
            s.append(card(x, y, colour, t, sub, det, tool))
            if si < len(stages) - 1:
                s.append(arrow_right(x + CARD_W + 6, y + CARD_H / 2))
        if bi < len(BANDS) - 1:
            s.append(arrow_wrap(LEFT + 2 * (CARD_W + GAP) + CARD_W,
                                y, TOP + (bi + 1) * (CARD_H + BAND_GAP)))
    s.append("</svg>")
    return "".join(s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="site/assets/architecture.svg")
    a = ap.parse_args()
    open(a.out, "w").write(build())
    print("wrote", a.out)
