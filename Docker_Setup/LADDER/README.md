# LADDER adapter and container setup

## Source
- Paper: M. T. Alam, D. Bhusal, Y. Park, and N. Rastogi, “Looking beyond IOCs: Automatically extracting attack patterns from external CTI,” in *Proc. 26th Int. Symp. Res. Attacks, Intrusions Defenses (RAID)*, 2023, pp. 92–108.
- Tool: LADDER
- Repository: `https://github.com/aiforsec/LADDER.git`
- Commit: `863ec65859700ac13b1308fbe48ad34f4078eb39`
- License: None indicated, rights reserved
- TTP-WorkBench image tag: `ttp-workbench:ladder`


## What this container does

The Dockerfile builds the LADDER attack-pattern inference environment, installs an updated requirements file, downloads the LADDER model artifacts from Google Drive, applies compatibility fixes for newer Python/transformers behavior, and copies in a lightweight CLI used by the TTP-WorkBench adapter.


## Changes and adaptation steps

- Clone the upstream repository at the pinned commit.
- Replace the upstream attack-pattern `requirements.txt` with `requirements_updated.txt` to fix dependency issues.
- Download the two upstream model artifacts with `gdown` into `attack_pattern/models/`.
- Patch `attack_pattern/models.py` so BERT/Roberta classes are imported from `transformers` rather than internal module paths.
- Patch `attack_pattern/dataset.py` to comment out unused `spacy` and `torchtext` imports that otherwise complicate dependency resolution.
- Patch `attack_pattern/inference.py` to adjust model loading to avoid strict-loading errors.
- Builds `ladder_attack_pattern_cli.py`, a lightweight CLI that performs LADDER inference and post-processing, from examples in LADDER/notebooks/attack-pattern-extraction.ipynb.
- Run the CLI test during image build to verify setup and pre-cache model dependencies.

## Files in this directory

- `Dockerfile`: builds the LADDER image.
- `requirements_updated.txt`: dependency set used in place of the upstream requirements file.
- `build_ladder_cli_from_notebook.py`: Builds the CLI that is used by the adapter.

## Build

From the `Docker_Setup/` directory:

```bash
bash setup_containers.sh --engine docker --ladder
```

## Smoke test

From the repository root:

```bash
python Tests/poc_tests/run_poc_tests.py --engine docker --adapter LADDER
```

Expected behavior: the adapter copies the input text into the container, runs the LADDER CLI, and parses the resulting attack-pattern predictions into the common schema.

## Notes

- The image uses a CUDA base image, but the small PoC test may still run on systems where the container runtime can use CPU fallback. Full benchmark runs are faster with GPU support.
- The paper mentions an empirically selected threshold; where the original artifact did not specify more detail, TTP-WorkBench follows the default behavior observed in the notebook/code path, th = 0.6
