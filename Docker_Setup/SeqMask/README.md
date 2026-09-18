# SeqMask adapter and container setup

## Source
- Paper: W. Ge and J. Wang, “SeqMask: Behavior extraction over cyber threat intelligence via multi-instance learning,” *The Computer Journal*, vol. 67, no. 1, pp. 253–273, Nov. 2022, doi: 10.1093/comjnl/bxac172.
- Tool: SeqMask
- Repository: `https://github.com/MuscleFish/SeqMask.git`
- Commit: `f3686599065a58927562fbb5c4bf71c075a687ba`
- License: MIT License, Copyright (c) 2021 Ge Wenhan
- License URL: https://github.com/MuscleFish/SeqMask?tab=MIT-1-ov-file
- Retained license: `UPSTREAM_LICENSE.txt`
- TTP-WorkBench image tag: `ttp-workbench:seqmask`

## What this container does

The setup script downloads and verifies the trained model/FastText archive from the configured mirror. The Dockerfile builds a frozen SeqMask runtime from that staged archive, installs older dependency versions needed by the original code, runs the upstream `test.py` best-effort to populate caches, and copies in a CLI for TTP-WorkBench.

## Changes and adaptation steps

- Clone the repository at the pinned commit.
- Use Python 3.9 and an isolated virtual environment to match the older dependency stack more reliably.
- Download and verify `ttp-workbench-seqmask-fasttext-models-v1.zip` on the host. If reconstructing the bundle manually, the upstream project points to Baidu-hosted model files at `https://pan.baidu.com/s/1AE3qvMP3GNJ3cvoCEjxz_A` with password `vfyg`.
- Use 7z to extract the FastText archive and all model zip files.
- Pin legacy-compatible dependency versions before installing requirements, including `numpy<2`, `scipy<1.11`, `joblib==1.3.2`, `nltk==3.6.7`, `Cython<3`, and `gensim==3.8.3` installed without build isolation.
- Install the local `requirements.txt` used for the SeqMask build.
- Download NLTK resources required by the original code.
- Run `test.py` during image build to verify that the required model artifacts are available.
- Add `seqmask_cli.py`, a CLI that exposes the upstream model variants and returns predictions in a form the adapter can parse.
- In bulk mode, record a failing input as an error JSON and continue with the next input. The adapter propagates that record's error. The model is loaded once per CLI batch.

## External artifacts

The SeqMask external artifact bundle was previously mirrored at:

- Record: `https://zenodo.org/records/20370771`
- DOI: `10.5281/zenodo.20370771`
- File: `SeqMask_FastTextModels.zip`
- Download URL: `https://zenodo.org/records/20370771/files/SeqMask_FastTextModels.zip?download=1`
- License: MIT License / upstream SeqMask artifact license


## Files in this directory

- `Dockerfile`: builds the SeqMask image.
- `setup_seqmask.sh`: downloads and verifies the configured artifact, then builds the image.
- `requirements.txt`: dependency set used for the adapted runtime.
- `seqmask_cli.py`: CLI used by the adapter.
- FastTextModels directory (Zenodo version only): The trained models originally hosted on Baidu as described in main project

## Build

From the `Docker_Setup/` directory:

```bash
bash setup_containers.sh --engine docker --seqmask
```

The top-level installer obtains the SeqMask component from the monolithic
`models.zip` and passes its staged local path to this builder. Use
`install.sh --models-archive /path/to/models.zip` to bypass the automatic
Zenodo download.

## Smoke test

From the repository root:

```bash
python Tests/poc_tests/run_poc_tests.py --engine docker --adapter SeqMask
```

Expected behavior: the adapter copies the reports into the container, calls `seqmask_cli.py`, and maps positive tactic/technique predictions into the common schema.

## Notes and limitations

- The SeqMask models are included in the Zenodo version. Otherwise download manually from Baidu.
- The original paper does not specify a single probability threshold for accepting a TTP prediction. The TTP-WorkBench adapter exposes a tunable threshold and defaults to the settings used in our benchmark path (threshold 0.5). 
- The benchmark defaults use the AR_Mask/simple vector-mask attention path for tactic and technique predictions.
