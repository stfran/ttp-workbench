# TTP-LLM adapter and container setup

## Source
- Paper: R. Fayyazi, R. Taghdimi, and S. J. Yang, “Advancing TTP analysis: Harnessing the power of large language models with retrieval augmented generation,” in *Proc. Annu. Comput. Secur. Appl. Conf. Workshops (ACSACW)*, 2024, pp. 255–261, doi: 10.1109/ACSACW65225.2024.00036.
- Tool: TTP-LLM
- Repository: `https://github.com/RezzFayyazi/TTP-LLM.git`
- Commit: `7b8ce19aa608769e86ecf7bddb6dc089e023ffa9`
- License: MIT License, Copyright (c) 2024 Reza Fayyazi
- License URL: https://github.com/RezzFayyazi/TTP-LLM?tab=MIT-1-ov-file
- TTP-WorkBench image tag: `ttp-workbench:ttp-llm`

## What this container does

The Dockerfile builds a runnable TTP-LLM environment, installs an updated requirements file, installs PyTorch, fixes the prediction column name, and removes the upstream prompt-only loader's 20-row cap.

## Changes and adaptation steps

- Clone the upstream repository at the pinned commit.
- Copy in `requirements_updated.txt` and install it in an isolated virtual environment.
- Install `torch`, `torchvision`, and `torchaudio` before the remaining requirements.
- Patch `decoder_only/postprocess.py` by inserting `df.columns = ["result"]` at the expected location. This avoids a key/column mismatch during post-processing.
- Remove `[:20]` from `pd.read_csv(csv_file)` in `decoder_only/prompt_only.py` so it processes more than the first 20 input rows. 
- Replace the upstream unbounded OpenAI retry loop with configurable per-request
  and per-invocation limits. Retry records expose only structured status and
  rate-limit metadata.
- Leave the original `main.py` as the execution entry point. The adapter prepares the input CSV expected by TTP-LLM, runs the original main/postprocess flow, and converts the resulting CSV into the common schema.

## Files in this directory

- `Dockerfile`: builds the TTP-LLM image.
- `patch_prompt_only.py`: applies the bounded, diagnostic retry policy during
  the image build and to the pinned native reproduction source.
- `requirements_updated.txt`: dependency set used for the adapted runtime.

## Build

From the `Docker_Setup/` directory:

```bash
bash setup_containers.sh --engine docker --ttp-llm
```

## Smoke test

From the repository root:

```bash
python Tests/poc_tests/run_poc_tests.py --engine docker --adapter TTP-LLM
```

## Reproduction runs and API limits

Table 2's full profile evaluates 9,532 procedures twice: once through the
original tool and once through the framework adapter. This requires 19,064
OpenAI requests, plus any smoke-test requests. Before starting a full run,
compare that total with the API project's requests-per-day limit. In
particular, a 10,000-RPD limit requires the run to span at least two quota
windows.

The framework writes and validates one checkpoint per input. A 100-record batch
is resumable only when every output in the batch is present and valid.

To resume only Table 2, pass its existing experiment run directory:

```bash
bash Tests/Reproductions/run_all.sh \
  --experiment ttpllm_table2 \
  --resume-run Tests/Reproductions/experiments/ttpllm_table2/runs/<run-id>
```

TTP-LLM defaults to at most three retries for one request and 25 total retries
per native batch. Override these deliberately with `--api-max-retries` and
`--api-max-total-retries` when invoking `reproduce_ttpllm_table2.py`. Retry
diagnostics contain structured exception and rate-limit metadata but exclude
keys, prompts, responses, and raw exception messages.

## Notes

- This adapter requires an OpenAI-compatible API key/configuration. Place the required values in the repository-level `config.ini` before running the adapter.
- Container execution passes credential variable names in the Docker/Podman
  argument vector and supplies their values only through the engine process
  environment, so keys are not exposed by ordinary process listings or command
  crash records.
- The original CLI exposes several model/prompt-style settings, but the reliably tested path in this artifact is the raw prompt path used for the reported reproduction/benchmark runs.
- Running this adapter can incur API cost and can be nondeterministic because it depends on LLM responses.
- The adapter chunks long reports into overlapping windows before calling the upstream code path.
- OpenAI may block some dangerous looking threat report text. Though the TTP-LLM code does not such an error, we deduced this happened in one case in our benchmark run based on repeated attempts to process the raw report. We ultimately manually defanged the affected report.
