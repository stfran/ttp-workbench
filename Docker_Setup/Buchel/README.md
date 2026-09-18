# Büchel generative adapter setup

## Source
- Paper: M. Büchel, T. Paladini, S. Longari, M. Carminati, S. Zanero, H. Binyamini, G. Engelberg, D. Klein, G. Guizzardi, M. Caselli, A. Continella, M. van Steen, A. Peter, and T. van Ede, “SoK: Automated TTP extraction from CTI reports—Are we there yet?” in *Proc. 34th USENIX Secur. Symp.*, 2025.
- Tool/workflow: Generation portion of Büchel et al., *SoK: Automated TTP Extraction from CTI Reports — Are We There Yet?*
- Upstream artifact bundles: `generation.zip` and `ext_tools.zip` from
  `https://zenodo.org/records/16753555`
- License: Creative Commons Attribution Share Alike 4.0 International
- License URL: https://creativecommons.org/licenses/by-sa/4.0/
- Model base: Llama 3.1 Community License; see `LLAMA_3.1_LICENSE.txt` and
  `NOTICE`. Built with Llama.
- TTP-WorkBench app image: `localhost/generation_app:latest`
- TTP-WorkBench Ollama image: `localhost/generation_ollama:latest`

The top-level installer obtains one monolithic `models.zip` and stages its
Büchel assets locally. Setup installs the supplied AnnoCTR checkpoint from the
staged Bosch-only component archive. The Table 9 reproduction uses the
published TRAM checkpoint embedded in the same `models.zip`.
The monolithic archive retains the Llama 3.1 license and attribution notice.


## What this setup does
The upstream artifact already produces a containerized generative experiment stack in `generation.zip`. `setup_buchel.sh` downloads and verifies both that bundle and `ext_tools.zip`, extracts them into `buchel_generation/`, adds the supplied merged checkpoints to the generation app image, copies in the adapter CLI, and builds the Compose images used by `BuchelAdapter`. The extracted `ext_tools/dataset/bosch_test.json` is subsequently used when compiling the reproduction inputs.

## Changes and adaptation steps

- Download, verify, and unpack the upstream Zenodo `generation.zip` and
  `ext_tools.zip` bundles.
- Reuse the staged Bosch-only component archive from `models.zip`, then install the merged AnnoCTR checkpoint under `/workspace/finetuning/output` in the generation app image.
- Stage the published Zenodo TRAM checkpoint for the Table 9 reproduction. The evaluator runner calls this checkpoint `sft_tram`; it does not train or execute the retained local TRAM checkpoint.
- Copy `buchel_cli.py` into the unpacked Compose project. The CLI reuses the upstream modules, prompt strategies, and fine-tuning code paths while exposing command-line inference over supplied report text.
- Patch Dockerfile `FROM` lines to include `docker.io/` prefixes when needed to avoid short-name ambiguity in Podman environments.
- Add `rich>=13` and `peft==0.17.1` to the upstream requirements file during setup.
- Rewrite the Compose file to support selected engines:
  - Docker: GPU reservations and persistent host-mounted experiment output.
  - Podman: GPU device mappings and SELinux `:Z` volume labels.
- Change the app service command to `sleep infinity` so the container remains available for adapter-driven inference rather than immediately running the original experiment entry point.
- Inject a model/tokenizer save block into `supervised_finetuning.py` with `--patch-trainer-save`. This supports persisting SFT outputs for reuse by the adapter.
- Preserve the ability to run the original experiment script by using `--up --run-experiment`.
- Set explicit embedding context and retry limits because the configured context limit caused an embedding exception that the released client retried indefinitely.

## Build

From the `Docker_Setup/` directory:

```bash
bash setup_containers.sh --engine docker --buchel
```

Or directly from this directory:

```bash
./setup_buchel.sh --docker
```

For a complete installation from an already-downloaded monolithic archive, run
`install.sh --models-archive /path/to/models.zip` from the repository root.

## Smoke test

From the repository root:

```bash
python Tests/poc_tests/run_poc_tests.py --engine docker --adapter Buchel
```

## Notes and limitations

- This is a heavyweight adapter and is not recommended as the first artifact smoke test.
- GPU support is expected for the default generative/SFT path.
- The setup creates two images: the app image used by `BuchelAdapter` and an Ollama image used to serve the local model.
- The adapter defaults to the generative configuration used in the benchmark path: raw prompting with the selected Llama-3.1-8B/SFT configuration when available.
- The artifact-evaluation runner never trains missing SFT models; a missing staged checkpoint is an execution failure.
- Our benchmark experiments used the following parameters, representing the best reported results in Table 9 of *SoK: Automated TTP Extraction from CTI Reports — Are We There Yet?*
```
    document_level=False,
    rag=False,
    fsp=False,
    quant_4bit_model=False,
    sft=True,
    sft_name="mitre_sentence_tram",
```
