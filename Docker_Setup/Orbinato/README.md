# Orbinato adapter and container setup

## Source
- Paper: V. Orbinato, M. Barbaraci, R. Natella, and D. Cotroneo, “Automatic mapping of unstructured cyber threat intelligence: An experimental study (practical experience report),” in *Proc. 33rd IEEE Int. Symp. Softw. Rel. Eng. (ISSRE)*, 2022, pp. 181–192.
- Tool: Orbinato et al., `cti-to-mitre-with-nlp`
- Repository: `https://github.com/dessertlab/cti-to-mitre-with-nlp.git`
- Commit: `a8cacf3185d098c686e0d88768a619a03a4d76d1`
- License: Creative Commons Attribution Share Alike 4.0 International
- License URL: https://creativecommons.org/licenses/by-sa/4.0/
- TTP-WorkBench image tag: `ttp-workbench:orbinato`

## What this container does

The setup script downloads and verifies the data/word-vector bundle and models we trained during our reproduction experiments. The Dockerfile then builds an Orbinato runtime environment from experiment runner code, installs the original requirements plus current `torch`/`transformers`, adds the data/work-verctor and trained models, creates an import-safe document-analysis module, and copies in the CLI wrapper.

The adapter can run several Orbinato model variants. If a requested model is missing, the adapter runs the corresponding training script inside the container and commits the trained artifacts back into the image for later reuse. This is not the default behavior because we include the trained models from our experiments, but others can delete them and stage new data to train new models.


## Changes and adaptation steps

- Clone the upstream repository at the pinned commit.
- Install the upstream Python requirements.
- Install NLTK resources used by the preprocessing pipeline.
- Install `torch`, `torchvision`, and `transformers<5` needed by the transformer/SecBERT code path (`encode_plus` errors on Transformers 5).
- Download and verify `ttp-workbench-orbinato-additional-files-v1.zip` on the host, then unpack it into `/opt/Orbinato` during the Docker build. The bundle is a copy of the upstream `additional_files.zip` originally distributed through figshare.
- Create `document_analysis_import.py` by copying `document_analysis.py` and stripping the bottom run block so the original document-analysis functions can be imported safely by the CLI.
- Add `orbinato_cli.py`, which reuses the original preprocessing, training, and inference routines for document analysis.
- Add `secbert_train.py` to support the SecBERT training/inference path incorporated from the original notebook workflow (`cti-to-mitre-with-nlp/Colab_notebook/trained_secBert.ipynb`).
- Verify and unpack `ttp-workbench-orbinato-models-v1.zip` into `/opt/Orbinato/src` during the build.

## External artifacts

The Orbinato external artifact bundle was originally published at:

- Record: `https://zenodo.org/records/20370771`
- DOI: `10.5281/zenodo.20370771`
- File: `additional_files.zip`
- Download URL: `https://zenodo.org/records/20370771/files/additional_files.zip?download=1`
- License: Creative Commons Attribution Share Alike 4.0 International

## Files in this directory

- `Dockerfile`: builds the Orbinato runtime image.
- `setup_orbinato.sh`: obtains the model and additional-file component archives staged from the monolithic `models.zip`, verifies their SHA-256 checksums, and copies them into the ignored build context before invoking the build.
- `orbinato_cli.py`: CLI used by the adapter.
- `secbert_train.py`: helper script for training the SecBERT variant.

## Build

From the `Docker_Setup/` directory:

```bash
bash setup_containers.sh --engine docker --orbinato
```

For a complete installation from an already-downloaded monolithic archive:

```bash
bash install.sh --all --engine docker --models-archive /path/to/models.zip
```

## Smoke test

From the repository root:

```bash
python Tests/poc_tests/run_poc_tests.py --engine docker --adapter Orbinato
```

Expected reproduction behavior: the image contains all supplied classifier variants; a missing or mismatched archive is a build failure.

## Notes

- GPU is recommended for inference.
- The adapter derives aggregate report-level TTP predictions from per-sentence predictions using a tunable probability threshold. The default follows the threshold used as a key cutoff discussed in Figure 3 of `Automatic mapping of unstructured cyber threat intelligence: an experimental study:(practical experience report)`.
