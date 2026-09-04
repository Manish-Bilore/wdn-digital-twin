#!/usr/bin/env python3
"""
04_emit_ontology.py
===================
Writes the ABox for the WDN sensor ontology from the pipeline tables, optionally
merges the TBox, and validates that the result parses.

    python3 scripts/04_emit_ontology.py -c config.yml

Outputs:
    <name>_instances.ttl   the ABox
    <name>_full.ttl/.owl   TBox + ABox (when ontology.tbox_path is set)
"""
import argparse, json, os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wdnkit import ontology
from wdnkit.config import Config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", required=True)
    args = ap.parse_args()
    cfg = Config.load(args.config)
    out = cfg.outdir
    name = cfg.get("project.name", "wdn")

    if not cfg.get("ontology.emit", True):
        print("[4/4] ontology emission disabled"); return

    nodes = pd.read_csv(os.path.join(out, "nodes_clustered.csv"))
    pipes = pd.read_csv(os.path.join(out, "pipes.csv"))
    sel = pd.read_csv(os.path.join(out, "optimal_sensor_nodes.csv"))
    meta = json.load(open(os.path.join(out, "02_model.json")))

    # the filter columns live in nodes_clustered; carry them onto the selection
    fcols = [c for c in ("f1_topology", "f2_elevation", "f3_pressure",
                         "f4_demand", "dma_mean_elev", "dma_p_lo", "dma_p_hi",
                         "dma_mean_demand") if c in nodes.columns]
    sel = sel.merge(nodes[["node_id"] + fcols], on="node_id", how="left")

    print("[4/4] emitting ontology instances")
    abox = os.path.join(out, f"{name}_instances.ttl")
    path, n = ontology.emit(nodes, pipes, sel, cfg,
                            meta.get("demand", {}), abox, crs=meta.get("crs"))
    print(f"    -> {path}")

    try:
        from rdflib import Graph
        g = Graph(); g.parse(abox, format="turtle")
        print(f"    ABox parses: {len(g)} triples")
        tbox = cfg.get("ontology.tbox_path")
        if tbox and os.path.exists(tbox):
            full = Graph(); full.parse(tbox, format="turtle"); full.parse(abox, format="turtle")
            ttl = os.path.join(out, f"{name}_full.ttl")
            owl = os.path.join(out, f"{name}_full.owl")
            full.serialize(ttl, format="turtle")
            full.serialize(owl, format="xml")
            print(f"    merged TBox + ABox: {len(full)} triples")
            print(f"    -> {ttl}\n    -> {owl}")
        elif tbox:
            print(f"    ! ontology.tbox_path not found: {tbox}")
    except ImportError:
        print("    ! rdflib not installed; skipped validation")


if __name__ == "__main__":
    main()
