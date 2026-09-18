# Benchmark figure reproduction results

This analysis regenerates Figures 4--6 and 8 from the saved benchmark predictions. The paper figures are fixed references; the fresh figures are recomputed from the same prediction archive against the currently compiled ground truth. No model inference is performed.

## Run conditions

| Figure | Data and queue | Scoring | Label treatment |
|---|---|---|---|
| 4(a) | 281 author-labeled CTRs | Generous, micro precision/recall/F1 | Modernized codes; non-collapse |
| 4(b) | Never-before-seen curated prior-work CTRs; rcATT-derived ground truth excluded | Generous, micro precision/recall/F1 | Modernized codes; non-collapse |
| 5 | Combined benchmark; support >= 50; TTP in at least three tools' capacities; TTP-LLM excluded because it predicts tactics only | Per-TTP F1 and best-tool oracle | Modernized codes; non-collapse |
| 6 | TTPDrill/rcATT and AttacKG/RAF-AG direct queues | Generous metrics on the joint-capacity report intersection | Previously seen reports excluded; non-collapse |
| 8 | All direct tool pairs with at least 10 eligible reports | Generous metrics on each joint-capacity report intersection | Previously seen reports excluded; non-collapse |

As stated in the submission, a direct comparison evaluates tools only on reports whose ground-truth TTP set is within the intersection of the tools' TTP capacities, while resolving updated TTP codes and excluding reports previously seen by either tool.

## Input verification

- Archive: `benchmark_results.zip`
- SHA-256: `1a17e0a9a2c0856455d352350e01057eae313c86bbb4dc58491dedd57b05f8ae`
- Verified archive files: 38
- Every archived file was compared byte-for-byte with its materialized counterpart.

The archive does match the files under `results/on_curated_data` and `results/on_author_labeled_data`. The comparison below separately tests whether today's ground-truth compilation reproduces the evaluation CSV preserved in that archive.

| Evaluation CSV | Reference rows | Fresh rows | Changed matched rows | Maximum score delta | Maximum count delta | Result |
|---|---:|---:|---:|---:|---:|---|
| Author-labeled | 24 | 24 | 8 | 0.0024 | 1 | differs |
| Curated | 24 | 24 | 8 | 0.0041 | 7 | differs |

A difference here indicates ground-truth or evaluation-input drift; it does not mean the archived prediction files failed verification. The preserved CSV remains the paper reference and the fresh CSV remains the result of this run.

## Direct queue checks

- TTPDrill vs rcATT: 61 reports (paper: 61).
- AttacKG vs RAF-AG: 27 reports (paper: 27).

## Paper reference and fresh result

| Figure | Paper reference | Fresh reproduction |
|---|---|---|
| 4(a) | [![Paper 4(a)](paper_figures/benchmark_ours.png)](paper_figures/benchmark_ours.png) | [![Fresh 4(a)](reproduced_figures/benchmark_ours.png)](reproduced_figures/benchmark_ours.png) |
| 4(b) | [![Paper 4(b)](paper_figures/benchmark_others_non_prov.png)](paper_figures/benchmark_others_non_prov.png) | [![Fresh 4(b)](reproduced_figures/benchmark_others_non_prov.png)](reproduced_figures/benchmark_others_non_prov.png) |
| 5 | [![Paper 5](paper_figures/ttp_boxplots_w_oracle.png)](paper_figures/ttp_boxplots_w_oracle.png) | [![Fresh 5](reproduced_figures/ttp_boxplots_w_oracle.png)](reproduced_figures/ttp_boxplots_w_oracle.png) |
| 6(a) | [![Paper 6(a)](paper_figures/tool_comparison_pairs_TTPDrill_rcATT.png)](paper_figures/tool_comparison_pairs_TTPDrill_rcATT.png) | [![Fresh 6(a)](reproduced_figures/direct_comparison/tool_comparison_pairs_TTPDrill_rcATT.png)](reproduced_figures/direct_comparison/tool_comparison_pairs_TTPDrill_rcATT.png) |
| 6(b) | [![Paper 6(b)](paper_figures/tool_comparison_pairs_AttacKG_RAF-AG.png)](paper_figures/tool_comparison_pairs_AttacKG_RAF-AG.png) | [![Fresh 6(b)](reproduced_figures/direct_comparison/tool_comparison_pairs_AttacKG_RAF-AG.png)](reproduced_figures/direct_comparison/tool_comparison_pairs_AttacKG_RAF-AG.png) |
| 8 | [![Paper 8](paper_figures/tool_comparison_pairs_10tools.png)](paper_figures/tool_comparison_pairs_10tools.png) | [![Fresh 8](reproduced_figures/direct_comparison/tool_comparison_pairs_10tools.png)](reproduced_figures/direct_comparison/tool_comparison_pairs_10tools.png) |

Commands and console output are retained in `reproduced_figures/run.log`.
