# rcATT adapter and container setup

## Source
- Paper: V. Legoy, M. Caselli, C. Seifert, and A. Peter, “Automated retrieval of ATT&CK tactics and techniques for cyber threat reports,” arXiv:2004.14322, 2020.
- Tool: rcATT
- Repository: `https://github.com/vlegoy/rcATT.git`
- Commit: `f82f7fd456279abefcd3e0b50e8056345c11aeb7`
- License: MIT License, Copyright (c) 2019 Valentine Legoy
- License URL: https://github.com/vlegoy/rcATT?tab=MIT-1-ov-file
- TTP-WorkBench image tag: `ttp-workbench:rcatt`

## What this container does

The Dockerfile builds a frozen rcATT runtime, installs a reconstructed requirements file, downloads NLTK resources, and applies a small patch to the result-saving code so empty prediction cases do not crash STIX object generation.

## Changes and adaptation steps

- Clone the upstream repository at the pinned commit.
- Install Python 3.7 from the Deadsnakes PPA to better match the age of the upstream code.
- Install dependencies from the local `requirements.txt`, which captures the package versions needed to make the upstream tool run.
- Download required NLTK resources (`punkt`, `stopwords`, `wordnet`) into `/opt/nltk_data`.
- Patch `classification_tools/save_results.py` to handle empty TTP sets when constructing STIX objects. The patch supplies a fallback marking-definition object reference and enables `allow_custom=True`, preventing failures when a report produces no predictions.
- Use the original `rcATT_cmd.py` CLI for prediction. The adapter copies a report into the container, calls `rcATT_cmd.py -p`, and maps rcATT's ATT&CK object references to TTP codes in the common schema.
- Apply `patch_bulk.py` after the empty-STIX patch. It adds optional cached configuration/pipelines to the upstream prediction functions without changing their default single-report behavior.

## Files in this directory

- `Dockerfile`: builds the rcATT image.
- `requirements.txt`: dependency set used for the adapted runtime.

## Build

From the `Docker_Setup/` directory:

```bash
bash setup_containers.sh --engine docker --rcatt
```

## Smoke test

From the repository root:

```bash
python Tests/poc_tests/run_poc_tests.py --engine docker --adapter rcATT
```

Expected behavior: rcATT writes predictions as STIX-like objects, and the adapter converts those object references into normalized ATT&CK TTP codes.

## Notes

- rcATT is lightweight
- The adapter focuses on report-level TTPs extracted extracted at the CLI. It does not use the GUI.
