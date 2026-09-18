# AttacKG container and adapter setup

## Source 
- Paper: Z. Li, J. Zeng, Y. Chen, and Z. Liang, “AttacKG: Constructing technique knowledge graph from cyber threat intelligence reports,” in *Proc. Eur. Symp. Res. Comput. Secur. (ESORICS)*, 2022, pp. 589–609.
- Tool: AttacKG
- Repository: https://github.com/li-zhenyuan/Knowledge-enhanced-Attack-Graph/
- Commit: 9120ebea25383bfca1254d2b3088266b3680e47b
- License: MIT License, Copyright (c) 2021 LI Zhenyuan
- License URL: https://github.com/li-zhenyuan/Knowledge-enhanced-Attack-Graph/?tab=MIT-1-ov-file
- TTP-WorkBench image tag: `ttp-workbench:attackg`


## What this container does

The Dockerfile builds a frozen AttacKG runtime from the upstream repository, installs the upstream Python requirements in `/opt/venv`, downloads the templates and `new_cti.model` artifacts from the configured Google Drive folder, and places the project at `/opt/AttacKG`.

AttacKG has an existing single-report CLI entry point (`main.py`) that produces JSON graph outputs. TTP-WorkBench retains that entry point and adds an opt-in bulk coordinator. Neither path replaces the core extraction or matching logic.


## Changes and adaptation steps

- Clone the upstream repository at the pinned commit.
- Install the tool into an isolated virtual environment using Python 3.8.
- Download and unpack the upstream template/model artifacts required by AttacKG.
- The adapter handles staging report text into the container and parsing the JSON graph output into the TTP-WorkBench common schema.
- Single-report adapter calls use `main.py` in `techniqueIdentification` mode with the original templates.
- `patch_bulk.py` adds optional model/template injection parameters to three upstream functions. Their defaults retain the original CLI behavior.
- `attackg_bulk.py` loads `new_cti.model` and the original technique templates once, then calls the same upstream graph and matcher logic independently for each report. Per-report failures are retained without aborting later reports.
- `patch_shortest_path_cache.py` memoizes directed shortest-path lengths while an immutable report graph is matched. AttacKG repeatedly requests the same ordered node-pair distances across templates and candidate alignments, and caching those exact NetworkX results avoids duplicate computation without changing thresholds, traversal, or predictions.

## Files in this directory

- `Dockerfile`: builds the frozen AttacKG image.
- `patch_bulk.py`: idempotent post-clone patch that permits cached resources.
- `patch_shortest_path_cache.py`: idempotent cache-based optimization for graph matching.
- `attackg_bulk.py`: opt-in multi-report coordinator.
- `LICENSE.md`: Original MIT License

## Build

From the `Docker_Setup/` directory:

```bash
bash setup_containers.sh --engine docker --attackg
```

Use `--engine podman` in place of `--engine docker` if using Podman.

## Smoke test

From the repository root, after installing the Python package and building the image:

```bash
python Tests/poc_tests/run_poc_tests.py --engine docker --adapter AttacKG
```

## Notes

- The build depends on network access to original project code and models.
- AttacKG outputs graph artifacts beyond TTP labels. TTP-WorkBench extracts the report-level TTP predictions for benchmarking.
- Bulk processing changes resource utilization, not prediction thresholds or matching logic. A fresh attack graph and matcher are created for every report. A single/bulk parity check produced identical technique/evidence sets.
- Shortest-path memoization changes resource utilization only. Its cache belongs to one report graph, includes unreachable ordered pairs, and is discarded with that graph.
