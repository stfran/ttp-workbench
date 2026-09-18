### Relationship to our Table 6 outcome

The results of the current run are in the following table. The columns labeled Table 6 Paper and Table 6 Repro. give the reference micro-F1 values for Table 6 of our submission, including the camera-ready correction noted below. Table 6 Paper rounds to three decimal places Eight report micro-f1, which we compute  by pooling the TP/FP/FN counts published in Table 4 of [2] for the eight selected reports. The reproduced micro-F1 values score tool predictions against the captured repository labels and not the counts (see Detailed results / Per-document technique comparison). This run micro-F1 columns for the original tool and framework are populated automatically from the run's outcome. For complete eight-report runs, Table 6 Repro., this run original, and this run framework are expected to agree for each tool to the reported rounding precision. 

<!-- table6-results -->

NOTE: Row 6 of Table 6 of our submission labels the metric as “Document Avg. F1,” but it should be labeled "Micro-F1". TTPDrill was reported as 0.042, when it should be 0.045. Both are editorial errors and will be corrected in the camera ready version of the submitted paper.


Our analysis from these results are summarized in Table 6, experiment 6, page 9 of our submitted paper. We record an unsuccessful reproduction of the experiment originally published in Table 4 of [2] with outcome codes `M` and `C`. We write this as `✗ MC` here: `✗` means unable to reproduce, `M` means missing/changed data, and `C` means code/model/artifact drift. The following table describes our analysis of these results.

| Table 6 code | Concrete evidence in this reproduction | Interpretation and limit |
|---|---|---|
| `✗` — unable to reproduce | Reproduced per-document counts differ from the published counts, and F1 remains substantially below the eight-report paper reference under both document averaging and micro-averaging. | We expect AttacKG and TTPDrill to be deterministic with fixed inputs, artifacts, and scoring, so we use a tighter difference tolerance than for stochastic methods. Only differences due to reported rounding are acceptable, and the gaps exceed rounding differences. |
| M — missing/changed data | Only eight reports have ground-truth annotations in the captured repository document; five transcribed label counts differ from the corresponding paper counts. The other eight report texts are available, but their ground truth was not available or attained in correspondence with the authors. | The exact published evaluation data cannot be established. Matching a paper count does not prove identical labels. |
| C — code/model/artifact drift | The experiment uses available, pinned code and staged assets, including the documented TTPDrill 0.3/1.0 combination, rather than a verified snapshot of the historical experiment. We reported changed technique-output counts to the authors; the reply also identified a manual statistics adjustment absent from our executable procedure. | This is a probable-cause assessment based on the deterministic nature of the tools and the variation between published counts and reproduced counts. We cannot separate the effects of code drift from the ground-truth differences. |

The averaging and manual-weighting details remain unresolved. This is a limitation of this experiment rather than an additional outcome code that is not present in Table 6.
