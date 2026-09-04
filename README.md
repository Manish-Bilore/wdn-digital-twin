# An ontology-driven digital water distribution network

The IIT Bombay campus water network, connected end to end: an estate-office
AutoCAD drawing amping the existing water distribution network, an EPANET 2.2 hydraulic model generated on top of it, an optimised water-quality
sensor layout, the IoT hardware that would sit at those locations, and an OWL
knowledge graph that ties all of it together, with live SPARQL in the browser.

**[Open the site](https://manish-bilore.github.io/wdn-digital-twin/)**

| | |
|---|---|
| Network | 322 pipes, 275 junctions, 18.8 km |
| Model | EPANET 2.2, Hazen-Williams, 2.5 peak factor |
| Monitoring | 29 nodes across 5 district metered areas |
| Graph | 8,554 triples, SOSA/SSN + QUDT aligned |
| Payload | ~80 kB gzipped — the whole demonstrator runs client-side |

## Layout

```
pipeline/     wdnkit package + five numbered stages
site/         Quarto site, including the interactive explorer
outputs/      a committed pipeline run, so the site builds without the DEM
w3id/         redirect config for https://w3id.org/iitb/wdn
```

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
apt install gdal-bin

python pipeline/run_all.py -c config.yml
python pipeline/scripts/05_export_web.py -o outputs -s site
quarto render site
```

## Honest limits

The georeference is solved by footprint optimisation against a digitised
boundary, not surveyed — roughly 10–30 m. Elevations are FABDEM at 30 m
posting. Pressures are modelled from one steady-state run and inherit both.
48 island-bridging pipes are not in the original drawing; they are the shortest
connectors that make the network solvable and are reported on every run.

## Licence

Code MIT. Data and ontology CC BY 4.0. The network is digitised from an
AutoCAD drawing provided by the IIT Bombay estate office.
