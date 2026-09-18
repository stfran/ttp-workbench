# TTPDrill container and adapter setup

## Source
- Paper: G. Husari, E. Al-Shaer, M. Ahmed, B. Chu, and X. Niu, “TTPDrill: Automatic and accurate extraction of threat actions from unstructured text of CTI sources,” in *Proc. 33rd Annu. Comput. Secur. Appl. Conf. (ACSAC)*, 2017, pp. 103–115.
- Tool: TTPDrill
- Repositories and commits used:
  - `https://github.com/ccsnow127/TTPDrill-1.0.git` at `48c99ae855e625ad9b9cdc71e7f6a597db898c99`
  - `https://github.com/SkyBulk/TTPDrill-0.3.git` at `78435268f26e71c966af53321a4a6037a6bb7853`
- License: None, Copyright 2020 CyberDNA Center, UNC Charlotte
- TTP-WorkBench image tag: `ttp-workbench:ttpdrill`

## What this container does

The Dockerfile builds a working TTPDrill runtime by combining the usable parts of two historical TTPDrill repositories, installing Stanford CoreNLP, applying a targeted parsing fix, and installing the Python dependencies captured in this directory.

## Changes and adaptation steps

- Use two pinned upstream repositories because no single historical repository provided the best match to the original paper and a complete runnable code path.
- Created a merged `/opt/TTPDrill` tree by copying TTPDrill 0.3 first and overlaying TTPDrill 1.0 on top.
- Download and install Stanford CoreNLP `2018-10-05`, which TTPDrill relies on for NLP processing.
- Install OpenJDK 8 for CoreNLP compatibility.
- Add a targeted patch to `relation_miner.py` that converts the CoreNLP output string to JSON with `json.loads(output)` before later processing.
- Install dependencies from the local `requirements.txt`, since the upstream artifacts did not include a sufficient requirements file.
- Download NLTK resources into `/opt/nltk_data` and set `NLTK_DATA` accordingly.
- Keep the original TTPDrill execution path. The adapter starts CoreNLP, stages the input at the location expected by TTPDrill, runs `main.py`, captures stdout, and parses TTP-related output into the common schema.

## Files in this directory

- `Dockerfile`: builds the merged TTPDrill/CoreNLP image.
- `requirements.txt`: dependency set used for the adapted runtime.

## Build

From the `Docker_Setup/` directory:

```bash
bash setup_containers.sh --engine docker --ttpdrill
```

## Smoke test

From the repository root:

```bash
python Tests/poc_tests/run_poc_tests.py --engine docker --adapter TTPDrill
```

Expected behavior: the adapter starts CoreNLP inside the container, runs TTPDrill on each PoC input file, and writes normalized outputs under `Tests/poc_tests/results/ttpdrill/`.

## Notes and limitations

- TTPDrill outputs multiple entity/action types. TTP-WorkBench extracts the TTP predictions relevant to report-level and per-substring evaluation.
