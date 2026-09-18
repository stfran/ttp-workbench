# RAF-AG adapter and container setup

## Source
- Paper: K. Mai, J. Lee, R. Beuran, R. Hotchi, S. E. Ooi, T. Kuroda, and Y. Tan, “RAF-AG: Report analysis framework for attack path generation,” *Computers & Security*, vol. 148, Art. no. 104125, 2025, doi: 10.1016/j.cose.2024.104125.
- Tool: RAF-AG
- Repository: `https://github.com/cyb3rlab/RAF-AG.git`
- Commit: `f2868edc1be6a09fc51b1d57907799602ddaa0eb`
- License: BSD 3-Clause License
- License URL: https://github.com/cyb3rlab/RAF-AG?tab=BSD-3-Clause-1-ov-file
- TTP-WorkBench image tag: `ttp-workbench:raf-ag`

## What this container does

The Dockerfile builds a frozen RAF-AG runtime, installs the upstream requirements, adds compatibility packages that were missing or version-sensitive, downloads the required spaCy models and coreference support, unpacks upstream data archives, and copies in a helper used to align RAF-AG output with TTP-WorkBench's common schema.

## Changes and adaptation steps

- Clone the repository at the pinned commit.
- Upgrade packaging tools with `setuptools<81`: spaCy 3.5 still requires `pkg_resources`, absent in newer setuptools.
- Replace the unavailable `fitz==0.0.1.dev2` requirement with `pymupdf`.
- Pin NumPy below 2.0 to avoid compatibility failures with older dependencies.
- Reinstall compatible `thinc==8.1.12`, `spacy==3.5.0`, and `fire` to resolve package/runtime errors observed during artifact setup.
- Install `nvidia-cuda-nvcc-cu12==12.8.93` and point TensorFlow XLA at its CUDA
  tree. GPU passthrough alone supplies driver access, not the `ptxas` and
  `libdevice.10.bc` files XLA needs to compile RAF-AG's TensorFlow kernels.
- Set `PYTHONHASHSEED=0`. RAF-AG converts sets to lists in prediction-critical
  paths, so fixing the hash seed makes tied predictions repeatable across executions.
- Sort RAF-AG's filesystem enumerations during setup. The upstream code loads
  technique candidates from unsorted directory listings, which can lead to
  equal-score candidates being treated differently across different filesystems.
- Download `en_core_web_lg` and `en_core_web_trf` spaCy models.
- Install `coreferee` and its English model.
- Unzip all archives under `/opt/RAF-AG/data` so the upstream data layout is ready for execution.
- Download Sentence Encoder into `/opt/RAF-AG/data/tf_hub` during the image build, avoiding a first-inference download.
- Copy `matcher.py` into the project. This helper reconstructs sentence/sub-string level TTP evidence from RAF-AG's output files for TTP-WorkBench.
- Remove the upstream demo reports from the campaign input directory so adapter-provided inputs are not mixed with demo data.

## Files in this directory

- `Dockerfile`: builds the RAF-AG image.
- `matcher.py`: helper script used by the adapter to post-process RAF-AG output.
- `patch_determinism.py`: makes upstream directory traversal repeatable across
  filesystems without changing the loaded files or prediction scores.

## Build

From the `Docker_Setup/` directory:

```bash
bash setup_containers.sh --engine docker --raf-ag
```

## Smoke test

From the repository root:

```bash
python Tests/poc_tests/run_poc_tests.py --engine docker --adapter RAF-AG --device cpu
```

CPU is the portable default. A CUDA-complete rebuilt image can be checked with
`--device cuda`. GPU passthrough is opt-in because we observed that RAF-AG's dependencies are
brittle and the working GPU path provided leads to only a slight speed improvement.

Expected behavior: the adapter stages reports into RAF-AG's campaign input directory, runs the upstream pipeline, invokes `matcher.py`, and writes normalized outputs under `Tests/poc_tests/results/raf-ag/`.

## Notes

- RAF-AG produces graph/path artifacts beyond TTP labels. TTP-WorkBench extracts the TTP predictions needed for benchmark comparisons.
- The build downloads large NLP models and can take longer than lightweight adapters.
- The adapter includes bulk-processing support because RAF-AG is relatively expensive when launched once per report.
- We observed timeout-like behavior when processing too many reports in a single batch between container refreshes. A conservative choice is 20 reports, but reduce if you observe sequential timeouts after a period of successful runs.
