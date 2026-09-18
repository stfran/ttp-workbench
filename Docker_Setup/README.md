 # Docker setup index

This directory contains one subdirectory per adapted TTP extraction artifact. Each subdirectory records the container build, local helper scripts, and the minimal changes needed to make the upstream artifact usable through TTP-WorkBench.

## Build one image

From this directory:

```bash
bash setup_containers.sh --engine docker --rcatt
```

Replace `docker` with `podman` if needed. Replace `--rcatt` with any supported tool flag.

## Build all images

```bash
bash setup_containers.sh --engine docker --all
```

Building all images can take a long time and may require GPU support, API credentials, and network access to external model/data hosts. For artifact review, start with a lightweight single-tool build such as `rcATT` or `TTPDrill` before attempting `--all`.

## Reproduction artifact downloads

The repository-level `../artifact_sources.env` identifies one monolithic
`models.zip` on Zenodo. The top-level `install.sh` downloads that archive once,
validates and expands its fixed inventory under
`Tests/Reproductions/.runtime/release_assets/`, and directs each tool setup
script to its local component files. The individual builders do not download
separate TTP-WorkBench model archives during a top-level installation.

Passing `install.sh --models-archive PATH` bypasses `artifact_sources.env` and
uses an already-downloaded `models.zip`. `ZENODO_CURL_CONFIG` can name a local
curl configuration when the configured URL uses an authenticated mirror.

## Tool flags and images

| Tool directory | Build flag | Adapter name | Image/tag |
|---|---:|---|---|
| `AttacKG/` | `--attackg` | `AttacKG` | `ttp-workbench:attackg` |
| `Buchel/` | `--buchel` | `Buchel` | `localhost/generation_app:latest` plus `localhost/generation_ollama:latest` |
| `LADDER/` | `--ladder` | `LADDER` | `ttp-workbench:ladder` |
| `Orbinato/` | `--orbinato` | `Orbinato` | `ttp-workbench:orbinato` |
| `RAF-AG/` | `--raf-ag` | `RAF-AG` | `ttp-workbench:raf-ag` |
| `rcATT/` | `--rcatt` | `rcATT` | `ttp-workbench:rcatt` |
| `SeqMask/` | `--seqmask` | `SeqMask` | `ttp-workbench:seqmask` |
| `TRAM/` | `--tram` | `TRAM` | `ttp-workbench:tram` |
| `TTP-LLM/` | `--ttp-llm` | `TTP-LLM` | `ttp-workbench:ttp-llm` |
| `TTPDrill/` | `--ttpdrill` | `TTPDrill` | `ttp-workbench:ttpdrill` |

## Smoke tests

From the repository root, after building the relevant image and installing the framework package:

```bash
python Tests/poc_tests/run_poc_tests.py --engine docker --adapter rcATT
```

Use the adapter names in the table above. The PoC runner writes raw and normalized outputs under `Tests/poc_tests/results/<adapter>/`.

## Reading the per-tool READMEs

Each tool README explains:

- the upstream repository or artifact bundle and pinned commit/version;
- model/data downloads performed during build;
- targeted dependency or source-code patches;
- CLI/helper files copied into the image;
- how the TTP-WorkBench adapter stages input and parses output;
- important runtime notes such as GPU, API key, or long-running training requirements.
