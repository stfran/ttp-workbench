# Benchmark figure reproduction

This directory regenerates Figures 4--6 and Figure 8 of the TTP-WorkBench
paper from preserved prediction JSON files. It is an analysis-only
reproduction: it does not run adapters, containers, model inference, or API
requests.

Run this after the top-level setup has compiled `Datasets/curated_reports` and
`Datasets/author_labeled_reviewed`:

```bash
python Tests/Benchmark_results/reproduce_benchmark_figures.py
```

The runner verifies `benchmark_results.zip` against every materialized file
below the ignored `results/` directory. If `results/` is absent, it safely
extracts the archive before analysis. Use `--verify-only` to perform only this
archive check. The top-level `install.sh` obtains the single monolithic
`models.zip`, stages its nested benchmark-results archive locally, extracts it
into `Tests/Benchmark_results/results`, and verifies every materialized file.
Pass `install.sh --models-archive PATH` to reuse an already-downloaded
`models.zip`; otherwise the installer uses `artifact_sources.env`.

Fresh CSVs and figures are written to the ignored `reproduced_figures/`
directory. `REPORT.md` records the run conditions, compares the archived and
fresh evaluation CSVs, checks the direct-comparison report counts, and places
the submitted figures beside the fresh figures. Fixed submitted figures are
stored in `paper_figures/` under the filenames used by the paper's LaTeX.

## Run-condition queue

| Queue | Figure | Conditions |
|---|---|---|
| `author_summary` | 4(a) | Author-labeled reports; generous micro metrics; modernized, non-collapsed labels |
| `curated_summary` | 4(b) | Curated reports; generous micro metrics; exclude rcATT-derived ground truth and each tool's previously seen reports; modernized, non-collapsed labels |
| `per_ttp_oracle` | 5 | Combined previously unseen reports; support >= 50; TTP in at least three tools' capacities; omit tactic-only TTP-LLM; non-collapse |
| `direct_examples` | 6 | TTPDrill/rcATT and AttacKG/RAF-AG; joint-capacity report intersection; generous metrics; non-collapse |
| `direct_all` | 8 | All tool pairs with at least 10 eligible reports; joint-capacity report intersection; generous metrics; non-collapse |

Following the submission, a direct comparison evaluates tools only on reports whose complete
ground-truth TTP set is within the intersection of both tools' prediction
capacities. Updated TTP codes are resolved, and reports previously evaluated
by either tool are excluded.

If the compiled ground truth is in a non-default location, pass
`--author-ground-truth` and/or `--curated-ground-truth`. This is useful for a
read-only audit or a temporary extraction; the top-level setup uses the
defaults.

## Analysis sources

The portable programs under `analysis/` consolidate the scripts that produced
the original `eval_*.csv` files under `../TTP-Extraction-Study/Tests/` with the
final plotting programs developed under `Dev/`. Relative development paths
were replaced with explicit repository paths, the two historical evaluators
were consolidated into one, and the pairwise program was renamed
`direct_compare.py`. The metric definitions and paper plotting parameters are
otherwise retained.

The six submitted reference files are:

- `benchmark_ours.pdf` — Figure 4(a)
- `benchmark_others_non_prov.pdf` — Figure 4(b)
- `ttp_boxplots_w_oracle.pdf` — Figure 5
- `tool_comparison_pairs_TTPDrill_rcATT.pdf` — Figure 6, left
- `tool_comparison_pairs_AttacKG_RAF-AG.pdf` — Figure 6, right
- `tool_comparison_pairs_10tools.pdf` — Figure 8
