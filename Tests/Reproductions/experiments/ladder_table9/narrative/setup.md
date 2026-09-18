### Inputs and preparation

We use the five report texts and per-report JSON files in [the comparison dataset](../../../data/LADDER_table_9_data).

The authors provided a spreadsheet of their manually annotated ground truth on reports linked to malware documentation on MITRE. They provided guidance on interpreting the spreadsheet which we used to convert it to the per-report jsons. The jsons include all the details contained in the spreadsheet.

We reference the mitre link provided to find the reports. The raw LADDER predictions included in the spreadsheet contain substrings, which we use to verify we have found the correct reports. Where possible, we use the ORKL.eu version of the report. Otherwise, we parse the report ourselves. Note that this is not guaranteed to be the equivalent text ran as input in the original experiment published in Table 9 of [6].

The JSON metadata contain ground truth and embedded tool predictions. Those predictions are retained as paper-reference evidence. The F1 values published in Table 9 of [6] are 0.64 for LADDER, 0.15 for AttacKG, and 0.14 for TTPDrill.

We audited the transcribed embedded predictions against the printed Table 9 counts using unique technique-code sets and the standard micro precision, recall, and F1 definitions. The following table distinguishes exact count agreement from agreement that appears only after rounding the metrics.

| Tool | Table 9 TP / FN / FP | Embedded TP / FN / FP | Table 9 P / R / F1 | Embedded P / R / F1 | Comparison |
|---|---:|---:|---:|---:|---|
| TTPDrill | 22 / 43 / 231 | 22 / 43 / 227 | 0.09 / 0.34 / 0.14 | 0.088353 / 0.338462 / 0.140127 | The metrics round to the published values, but the embedded predictions contain four fewer false positives. |
| AttacKG | 12 / 53 / 85 | 12 / 53 / 85 | 0.12 / 0.18 / 0.15 | 0.123711 / 0.184615 / 0.148148 | The counts agree exactly and the metrics round to the published values. |
| LADDER | 41 / 24 / 22 | 41 / 24 / 21 | 0.65 / 0.63 / 0.64 | 0.661290 / 0.630769 / 0.645669 | The embedded predictions contain one fewer false positive; precision and F1 round to 0.66 and 0.65 rather than the published 0.65 and 0.64. |

The TTPDrill predictions for `litepower.json` are stored under the transcribed key `TPDrill_results`, whereas the other four files use `TTPDrill_results`; the runner recognizes both spellings. Consequently, AttacKG's embedded predictions fully reconstruct its printed row, TTPDrill's reconstruct the printed metrics but not the FP count, and LADDER's do not fully reconstruct either the printed counts or the printed metrics.

### Tools and execution paths

Each tool runs directly from its staged repository in its own environment and through its framework adapter. Direct LADDER uses the notebook-derived CLI, and its launcher supplies the matching native libraries. AttacKG and TTPDrill reuse the direct-call functions verified for the AttacKG exercise. Source revisions, model staging, and necessary corrections are documented in the container setup READMEs for [LADDER](../../../../../Docker_Setup/LADDER/README.md), [AttacKG](../../../../../Docker_Setup/AttacKG/README.md), and [TTPDrill](../../../../../Docker_Setup/TTPDrill/README.md).

### Scoring

The runner normalizes each report's technique predictions to sets, compares them with its captured ground truth, and computes per-report TP/FP/FN. It retains both micro and document-average precision, recall, and F1. The comparison's paper column quotes the published Table 9 F1 values. The report independently recalculates the embedded-reference rows from the five JSON records, while the current original and framework rows come from the saved execution summaries. These are distinct reference sources, and the audit above shows where their counts or rounded metrics differ.

## References

[1] G. Husari, E. Al-Shaer, M. Ahmed, B. Chu, and X. Niu, “TTPDrill: Automatic and accurate extraction of threat actions from unstructured text of CTI sources,” in *Proc. 33rd Annu. Comput. Secur. Appl. Conf. (ACSAC)*, 2017, pp. 103–115.

[2] Z. Li, J. Zeng, Y. Chen, and Z. Liang, “AttacKG: Constructing technique knowledge graph from cyber threat intelligence reports,” in *Proc. Eur. Symp. Res. Comput. Secur. (ESORICS)*, 2022, pp. 589–609.

[6] M. T. Alam, D. Bhusal, Y. Park, and N. Rastogi, “Looking beyond IOCs: Automatically extracting attack patterns from external CTI,” in *Proc. 26th Int. Symp. Res. Attacks, Intrusions Defenses (RAID)*, 2023, pp. 92–108.
