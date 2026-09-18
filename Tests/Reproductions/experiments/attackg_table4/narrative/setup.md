### How we prepared the comparison data

1. From Table 4 of [2] we transcribed the per-report manual counts and false-negative/false-positive counts into a [16-row reference CSV](../../../data/attackg/attackg_table4_all16_counts.csv). We segment the first eight rows for which we have ground truth data in [attackg_paper_counts.csv](../../../data/attackg/attackg_paper_counts.csv). These 8 reports are the reproduction scope since the repository does not have all ground truth for all 16 and the authors did not provide them when requested.
2. We transcribed the upstream ground truth document into [ground_truth_and_test_labels.json](../../../data/attackg/ground_truth_and_test_labels.json), mapping its report names to paper titles and text filenames. We assumed that grey-highlighted annotations represented manual ground truth and the non-grey annotations represented historic tool predictions. The authors did not confirm this labeling specification, but their format is similar to the prediction output of the tool.
3. We collected and named the report texts for repeatable runs. The data directory contains all 16 text files, but only eight reports have captured ground-truth annotations. We treat the other eight JSON entries with empty label lists as missing annotations, not reports known to contain no techniques. 
4. We checked the transcribed label counts against the counts in Table 4 of [2]. Five of the eight labeled reports have different counts. The local labels total 47 report-level label occurrences, versus 50 in the paper's corresponding manual column. The detailed comparison lists these differences. We have not relabeled reports or removed codes to make the counts agree.

KEY POINT: The [ground_truth_and_test_labels.json](../../../data/attackg/ground_truth_and_test_labels.json) `labels` field is the scoring reference on only eight of the 16 reported results.

### Tools and execution paths

We run the available repository implementations. Here, “current” means the pinned versions selected for this reproduction that matches the version implemented in framework.

| Component | Selected artifact and execution |
|---|---|
| AttacKG, original | Commit `9120ebea25383bfca1254d2b3088266b3680e47b`; `external_tools/AttacKG/.venv` (Python 3.8); upstream `main.py -M techniqueIdentification -T templates`, once per report. |
| AttacKG, framework | Standard `Framework/adapters/attackg_adapter.py`; the same technique-identification CLI through the AttacKG container. The framework caller uses the project-root `.venv`. |
| TTPDrill, original | The documented merged tree: 0.3 commit `78435268f26e71c966af53321a4a6037a6bb7853`, overlaid with 1.0 commit `48c99ae855e625ad9b9cdc71e7f6a597db898c99`; `external_tools/TTPDrill/.venv` (Python 3.9), CoreNLP 2018-10-05, and upstream `main.py`. |
| TTPDrill, framework | Standard `Framework/adapters/ttpdrill_adapter.py`; the corresponding merged tool in its container, called through the project-root `.venv`. |

The pins match the maintained [AttacKG Dockerfile](../../../../../Docker_Setup/AttacKG/Dockerfile) and [TTPDrill Dockerfile](../../../../../Docker_Setup/TTPDrill/Dockerfile). AttacKG uses the staged `templates/` and `new_cti.model/` assets; no model is trained for this exercise. The [AttacKG setup README](../../../../../Docker_Setup/AttacKG/README.md) and [TTPDrill setup README](../../../../../Docker_Setup/TTPDrill/README.md) describe the asset provenance and direct-versus-container setup.

### Scoring 

Both execution paths receive the same eight selected report texts. We compare each saved document-level prediction set with that report's captured label set. Matched codes are TP, extra codes are FP, and missed codes are FN. AttacKG's native runner extracts code strings from its saved technique JSON, while the adapter reads its technique keys; the fidelity check below compares their resulting document-level code sets rather than assuming their parsing is equivalent.

We calculate precision, recall, and F1 per document, then average those values equally across the evaluated documents. We also sum TP/FP/FN across documents and calculate micro precision, recall, and F1. Both calculations are recoverable from the per-document counts published in Table 4 of [2]. Note that neither calculation on all 16 printed rows in Table 4 of [2] recreates the paper's overall values and the paper does not specify any other averaging. The eight-report paper reference is therefore a recalculation on the selected scope.

### What we discussed with the authors

Our September 16, 2025 message asked the authors of [2] to verify our setup and assumptions, help reconcile the Word document with Table 4 of [2], and supply any data or code. We provided reproduction package (without the framework version) and reported missing annotations and prediction-count differences. On October 9, the responding author said report text was available, suggested labeling the remaining reports using the existing examples, and cautioned against counting multiple references to the same entity separately. On October 21, we clarified that the mismatch also involved technique predictions, highlight the Firefox DNS Drakon report as an example where our run returned 17 distinct codes while the paper's counts implied 11 predictions. On October 22, the author mentioned manually reducing the weight of similar techniques, including different C&C implementations, during the statistics step, but was unsure whether that explained the discrepancy. 


The exchange did not reconcile the remaining ground-truth annotations, a complete grouping/weighting rule, an averaging specification, or replacement artifacts that resolve the discrepancy. We do not claim that the authors approved all our assumptions. Since our goal is reproduction from original artifacts, we do not manually vet the ground truth or develop new ground truth.

## References

[1] G. Husari, E. Al-Shaer, M. Ahmed, B. Chu, and X. Niu, “TTPDrill: Automatic and accurate extraction of threat actions from unstructured text of CTI sources,” in *Proc. 33rd Annu. Comput. Secur. Appl. Conf. (ACSAC)*, 2017, pp. 103–115.

[2] Z. Li, J. Zeng, Y. Chen, and Z. Liang, “AttacKG: Constructing technique knowledge graph from cyber threat intelligence reports,” in *Proc. Eur. Symp. Res. Comput. Secur. (ESORICS)*, 2022, pp. 589–609.
