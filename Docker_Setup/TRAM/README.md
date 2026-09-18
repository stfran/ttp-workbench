# TRAM adapter and container setup

## Source:
- Paper: None
- Tool: Threat Report ATT&CK mapper (TRAM)
- Repository: `https://github.com/center-for-threat-informed-defense/tram.git`
- Commit: `f29793d8d665f7f552898696e00065ef24a29a20`
- License: Apache-2.0
- License URL: https://github.com/center-for-threat-informed-defense/tram?tab=Apache-2.0-1-ov-file
- TTP-WorkBench image tag: `ttp-workbench:tram`


## What this container does

The Dockerfile creates a compact TRAM inference image focused on the pretrained multi-label SciBERT model rather than the full web application. It installs the Python libraries needed for document parsing and transformer inference, clones the upstream repository for provenance, copies in a CLI, and downloads the pretrained model files from the public TRAM model storage location.

## Changes and adaptation steps

- Clone the upstream TRAM repository at the pinned commit for provenance
- Install `torch`, `torchvision`, `torchaudio`, `transformers`, `pandas`, `python-docx`, `pdfplumber`, and `beautifulsoup4` into `/opt/venv`.
- Copy `predict_multi_label.py` into `/opt/TRAM`. This CLI reproduces the multi-label prediction path needed for TTP-WorkBench batch execution. It replicates the logic in tram/user_notebooks/predict_multi_label.ipynb
- Download the pretrained SciBERT multi-label model files  from the TRAM Azure blob model location into `/opt/TRAM/scibert_multi_label_model/`.
- Skip the TRAM web UI and database stack because TTP-WorkBench only needs deterministic CLI inference over supplied report text.

## Files in this directory

- `Dockerfile`: builds the TRAM inference image.
- `requirements.txt`: shared pinned dependencies for the container and native environment.
- `predict_multi_label.py`: CLI used by the adapter for single-report and bulk prediction.

## Build

From the `Docker_Setup/` directory:

```bash
bash setup_containers.sh --engine docker --tram
```

## Smoke test

From the repository root:

```bash
python Tests/poc_tests/run_poc_tests.py --engine docker --adapter TRAM
```

Expected behavior: the adapter writes report text into the container, runs `predict_multi_label.py`, and receives aggregate and per-substring predictions already close to the common output format.

The full project installer also stages the pinned source, creates an isolated
native environment, and copies the same pretrained model from the built image
for paired native/framework fidelity testing.

## Notes and limitations

- TRAM is primarily an analyst-facing web application, but this adapter targets the pretrained multi-label model and inference path for reproducible benchmark execution.
- TRAM predicts only a limited subset of ATT&CK techniques. Benchmark interpretation should account for this TTP capacity.
