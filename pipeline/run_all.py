#!/usr/bin/env python3
"""
run_all.py
==========
Runs the whole pipeline end to end.

    python3 run_all.py -c config.yml
"""
import argparse, os, subprocess, sys, time

STAGES = ["01_ingest_cad.py", "02_build_model.py",
          "03_place_sensors.py", "04_emit_ontology.py"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", required=True)
    ap.add_argument("--from-stage", type=int, default=1)
    args = ap.parse_args()
    here = os.path.dirname(os.path.abspath(__file__))
    t0 = time.time()
    for i, s in enumerate(STAGES, 1):
        if i < args.from_stage:
            continue
        r = subprocess.run([sys.executable, os.path.join(here, "scripts", s),
                            "-c", args.config])
        if r.returncode != 0:
            sys.exit(f"stage {s} failed")
    print(f"\ndone in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
