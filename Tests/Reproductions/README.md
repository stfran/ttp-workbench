# Reproduction suite of prior work experiments

This directory reproduces the eleven prior-work experiments summarized in Table 6 of our submission. The runners automate the experiments used in our reproducibility analysis. The four core experiments generate detailed `REPORT.md` evidence for the artifact evaluation. The optional Büchel Table 9 experiment also has a narrative report; the remaining optional experiments retain machine-readable results and comparisons.

## Experiments

`--all` runs these selectors in order:

| Selector | Reproduction and fresh evaluation | Tool setup |
|---|---|---|
| `attackg_table4` | AttacKG Table 4 technique extraction: AttacKG and TTPDrill, each run directly and through the framework on the 8 reports with captured labels. | [AttacKG](../../Docker_Setup/AttacKG/README.md), [TTPDrill](../../Docker_Setup/TTPDrill/README.md) |
| `ladder_table9` | LADDER Table 9 technique extraction: LADDER, AttacKG, and TTPDrill, each run directly and through the framework on 5 recovered reports. | [LADDER](../../Docker_Setup/LADDER/README.md), [AttacKG](../../Docker_Setup/AttacKG/README.md), [TTPDrill](../../Docker_Setup/TTPDrill/README.md) |
| `buchel_table13` | Büchel Table 13 on AnnoCTR/Bosch: the Büchel-released LADDER and capped AttacKG variants, each run directly and through the framework. | [Büchel](../../Docker_Setup/Buchel/README.md), [LADDER](../../Docker_Setup/LADDER/README.md), [AttacKG](../../Docker_Setup/AttacKG/README.md) |
| `ttpllm_table2` | TTP-LLM Table 2 prompt-only procedure-to-tactic experiment, run directly and through the framework. | [TTP-LLM](../../Docker_Setup/TTP-LLM/README.md) |
| `buchel_table9` | Büchel Table 9 generative experiments: Raw, FSP, RAG, and FSP+RAG strategies over Bosch and TRAM with base and fine-tuned models, directly and through the framework. | [Büchel](../../Docker_Setup/Buchel/README.md) |
| `orbinato_fig3` | Orbinato Figure 3: eight staged classifiers (MLP, logistic regression, multinomial NB, SVM OvR, CNN, LSTM, pretrained LSTM, and SecBERT), directly and through the framework on 6 reports at thresholds 0.1 through 0.8; consolidation regenerates the comparison plots. | [Orbinato](../../Docker_Setup/Orbinato/README.md) |
| `rcatt_table6` | rcATT Table 6: direct and framework predictions on 121 retained training-data records, scored separately for tactics and techniques. | [rcATT](../../Docker_Setup/rcATT/README.md) |
| `rafag_table6` | RAF-AG Table 6: RAF-AG and AttacKG, each run directly and through the framework on 30 recovered reports. | [RAF-AG](../../Docker_Setup/RAF-AG/README.md), [AttacKG](../../Docker_Setup/AttacKG/README.md) |
| `seqmask_table7` | SeqMask Table 7: SeqMask and rcATT, each run directly and through the framework on 6,509 records. | [SeqMask](../../Docker_Setup/SeqMask/README.md), [rcATT](../../Docker_Setup/rcATT/README.md) |
| `seqmask_table14` | SeqMask Table 14: SeqMask and rcATT, each run directly and through the framework on 4,938 records. | [SeqMask](../../Docker_Setup/SeqMask/README.md), [rcATT](../../Docker_Setup/rcATT/README.md) |
| `seqmask_table15` | SeqMask Table 15: SeqMask and rcATT, each run directly and through the framework on 1,286 records. | [SeqMask](../../Docker_Setup/SeqMask/README.md), [rcATT](../../Docker_Setup/rcATT/README.md) |

`--core` runs the first four selectors through `ttpllm_table2`. `--optional`
runs `buchel_table9` and the remaining selectors. Büchel Table 9 is optional
because its full configuration grid is too long-running for the standard
artifact-evaluation path. `--all` runs both groups in the table order above.

The results distinguish two fixed historical references from two fresh
measurements. The first reference is the result published by the prior work;
the second is the result from our initial reproduction reported in Table 6 of
our submission. A new run then reports its original-tool result (without the
framework) and framework result (with the framework). Neither historical
reference is treated as a prediction produced by the current run.

## Runtime location

Generated dependencies default to the ignored `.runtime/external_tools`
directory below this README. For an existing installation, set an absolute
override before running setup or experiments:

```bash
export REPRO_EXTERNAL_ROOT=/path/to/external_tools
```

Do not run two suites concurrently: several upstream tools use fixed temporary
or output filenames. Never reuse a result directory across scopes or datasets,
except through the explicit resume options below.

## Interrupted runs

To continue an interrupted end-to-end run, use:

```bash
bash claims/claims.sh --resume-run claims/runs/<run-id>
```

To resume only one reproduction experiment, pass its selector and existing run
directory:

```bash
bash Tests/Reproductions/run_all.sh \
  --experiment <selector> \
  --resume-run Tests/Reproductions/experiments/<selector>/runs/<run-id>
```

## Sources and data

`setup/setup_sources.sh` is the source of truth for upstream repositories and
exact commit pins. Public Büchel releases and large runtime assets are pinned by
checksum in the setup scripts.

The retained ATT&CK Web Shell page includes a literal one-line PHP web-shell
sample that endpoint-protection software can quarantine when a runner stages it
as a text file. The reproduction-data compiler replaces only that exact sample
with `[DEFANGED PHP WEB-SHELL EXAMPLE: code omitted]` in the rcATT and SeqMask
inputs. It preserves the surrounding page text and all ground-truth labels, and
validation fails if the executable-looking literal remains or the expected
replacement counts change.

## Experiment descriptions

### `attackg_table4` — AttacKG Table 4 [2]

This experiment reproduces the technique-identification portion of Table 4 of [2] with AttacKG [2] and TTPDrill [1], both directly and through the framework. It is experiment 6 in our submitted paper's Table 6. The same eight report texts with captured ground truth are supplied to all four execution paths, and the runner reports document-average and micro precision, recall, and F1. Because both tools are expected to be deterministic, the fidelity check compares the direct and framework technique sets exactly for every report.

Only eight of the paper's sixteen reports have captured ground-truth annotations. Five of those eight label counts differ from the corresponding counts published in Table 4 of [2], and neither document averaging nor micro averaging over the sixteen published count rows recreates the printed statistics. The available pinned code and models are not verified snapshots of the historical experiment, and author correspondence did not resolve data discrepancies or scoring procedure.

### `ladder_table9` — LADDER Table 9 [6]

This experiment reproduces Table 9 of [6] with LADDER [6], AttacKG [2], and TTPDrill [1] on five reports. It is experiment 8 in our submitted paper's Table 6. Each tool runs directly and through its framework adapter, and deterministic fidelity is checked using exact equality of document-level technique sets. The authors supplied a spreadsheet containing manually annotated ground truth and embedded predictions for the tool. We recovered report text using the supplied MITRE links, and prediction substrings as checks. The recovered text is not guaranteed to be the same text used in the experiment published in [6]. 


### `buchel_table13` — Büchel Table 13

This experiment reproduces the LADDER and AttacKG comparison on AnnoCTR data [11] in Table 13 [9]. It is experiment 3 in our submitted paper's Table 6. [11] provides the LADDER and AttacKG versions used in its experiments in `ext_tools.zip`. We use those versions in our reproduction. Each native call uses the released tree and its Büchel external-tools `.venv`. LADDER's framework path temporarily overlays the byte-different Büchel files in a disposable standard LADDER container. AttacKG's framework path derives a temporary image from the standard image, installs Python 3.10 and Büchel's requirements, verifies those versions against the preceding native run, and removes the image after the call. Both paths use the separately staged models, released document grouping and scoring functions, and the Büchel AttacKG variant's 10,000-alignment cap.

We observed that standard AttacKG produced different predictions and performance from the Büchel variant. The variants differ in both their alignment limit and software stack, including newer spaCy, coreferee, and NetworkX versions. The standard AttacKG image remains unchanged for other experiments because its upstream GitHub repository specifies Python 3.8 and the older requirements. The variants are therefore not interchangeable.

### `ttpllm_table2` — TTP-LLM Table 2

This experiment reproduces the prompt-only procedure-to-tactic portion of Table 2 of [8] using the released TTP-LLM repository directly and through the framework adapter. It is experiment 1 in our submitted paper's Table 6. Both paths use aligned MITRE procedure and 14-column tactic-label rows, perform inference on GPT-3.5-turbo, and report per-tactic and samples-average precision, recall, and F1. 

The experiment requires a configured model-service credential, incurs API cost, and is subject to service availability, nondeterminism, and model drift. An unavailable model or incomplete prediction array is an execution failure, not a zero-performance result. 
### `buchel_table9` — Büchel Table 9

This experiment reproduces the generative AnnoCTR/Bosch [11] and TRAM experiments in Büchel Table 9. It is experiment 2 in our submitted paper's Table 6. Each backend covers Raw, FSP, RAG, and FSP+RAG with the base model, the supplied AnnoCTR model (`sft_bosch`), and the published Zenodo TRAM model (`sft_tram`). Direct execution calls the released generation routine, while framework execution uses `BuchelAdapter` with the same staged models and native item grouping. The evaluator runner does not train models or execute the locally trained TRAM checkpoint retained in older runs.

The complete grid is long-running and RAG requires the separate Qwen embedding service. Current cells must be mapped to the historical configuration selection before calculating a comparable grid average. Generative variation means that isolated score agreement does not establish execution-path fidelity, and missing weights, retrieval dependencies, or cells are execution limitations rather than poor model performance.

### `orbinato_fig3` — Orbinato Figure 3

This experiment reproduces the experiment with results in Figure 3 of [4]. Orbinato et al. evaluates eight classifiers (MLP, logistic regression, multinomial naive Bayes, SVM OvR, CNN, LSTM, pretrained LSTM, and SecBERT) on six reports. Every classifier runs directly through the released routines and through the framework. We stage trained classifiers as reproduction artifacts because we observed that some training routines did not control for randomness. 

For each sentence and classifier, the evaluation keeps the highest-probability candidate when its probability is strictly greater than each threshold from 0.1 through 0.8. It then aggregates predictions by report, calculates document-level precision, recall, and F1, and regenerates per-report comparison plots. The primary `results/orbinato_figure3_comparison.png` places screenshots from the six paper panels above the corresponding original and framework results using the paper panels' y-axis scales. 

### `rcatt_table6` — rcATT Table 6

This experiment reproduces the experiment whose results are shown in Table 6 of [10]. We run the evaluation directly and through the framework on 121 rows from `unfetter_wiki_preprocessed.csv` data. It extracts tactic and technique labels separately and reports micro and label-wise macro precision, recall, and F0.5. The primary published comparison is technique micro-F0.5. It is unclear whether this data was subsequently used as training data for the released version of rcATT. 
### `rafag_table6` — RAF-AG Table 6

This experiment reproduces the experiment whose results are shown in Table 6 of [3]. It compares RAF-AG and AttacKG on thirty reports, with both tools run directly and through the framework. An audit of the ground truth data in the repository and the performance counts in Table 6 shows slight inconsistencies. We observed changes in RAF-AG's predictions when its underlying dependency versions and filsystem traversal processes differed. RAF-AG is expensive and is processed in conservative batches because larger batches have exhibited sequential timeout behavior.
### `seqmask_table7` — SeqMask Table 7

This experiment reproduces the experiment whose results are shown in Table 7 of [5]. It compares SeqMask and rcATT on MITRE ATT&CK data, running each tool directly and through the framework. SeqMask evaluates tactic and technique labels using the AR_Mask models, while the rcATT baseline retains technique IDs only.

For all three SeqMask experiments, predictions must exceed a probability threshold of 0.5 and are limited to the top 10 labels of each type. Scoring uses the applicable table-specific parent normalization and, for SeqMask, the intersection of the ground-truth and model label spaces. The paper does not specify a universal acceptance threshold, so the threshold and top-k settings are explicit reproduction choices. SeqMask's released evaluation behavior retains per-record model errors as empty predictions rather than silently omitting them.

### `seqmask_table14` — SeqMask Table 14

This experiment reproduces the experiment whose results are shown in Table 14 of [5]. It compares SeqMask and rcATT on TTPDrill sentence-level data [1], running each tool directly and through the framework. It evaluates technique labels using the AR_Mask model and the 0.5 threshold and top-10 policy described for Table 7 above.

### `seqmask_table15` — SeqMask Table 15

This experiment reproduces the experiment whose results are shown in Table 15 of [5]. It compares SeqMask and rcATT on rcATT data [10], running each tool directly and through the framework. It uses the AR_Mask models and the 0.5 threshold and top-10 policy described for Table 7 above; SeqMask evaluates tactic and technique labels, while the rcATT baseline retains technique IDs only.

## References
[1] G. Husari, E. Al-Shaer, M. Ahmed, B. Chu, and X. Niu, “TTPDrill: Automatic and accurate extraction of threat actions from unstructured text of CTI sources,” in *Proc. 33rd Annu. Comput. Secur. Appl. Conf. (ACSAC)*, 2017, pp. 103–115.

[2] Z. Li, J. Zeng, Y. Chen, and Z. Liang, “AttacKG: Constructing technique knowledge graph from cyber threat intelligence reports,” in *Proc. Eur. Symp. Res. Comput. Secur. (ESORICS)*, 2022, pp. 589–609.

[3] K. Mai, J. Lee, R. Beuran, R. Hotchi, S. E. Ooi, T. Kuroda, and Y. Tan, “RAF-AG: Report analysis framework for attack path generation,” *Computers & Security*, vol. 148, Art. no. 104125, 2025.

[4] V. Orbinato, M. Barbaraci, R. Natella, and D. Cotroneo, “Automatic mapping of unstructured cyber threat intelligence: An experimental study (practical experience report),” in *Proc. 33rd IEEE Int. Symp. Softw. Rel. Eng. (ISSRE)*, 2022, pp. 181–192.

[5] W. Ge and J. Wang, “SeqMask: Behavior extraction over cyber threat intelligence via multi-instance learning,” *The Computer Journal*, vol. 67, no. 1, pp. 253–273, Nov. 2022.

[6] M. T. Alam, D. Bhusal, Y. Park, and N. Rastogi, “Looking beyond IOCs: Automatically extracting attack patterns from external CTI,” in *Proc. 26th Int. Symp. Res. Attacks, Intrusions Defenses (RAID)*, 2023, pp. 92–108.

[7] MITRE, “Threat Report ATT&CK Mapper (TRAM),” 2023. [Online]. Available: <https://github.com/center-for-threat-informed-defense/tram/>. [Accessed: Apr. 30, 2025].

[8] R. Fayyazi, R. Taghdimi, and S. J. Yang, “Advancing TTP analysis: Harnessing the power of large language models with retrieval augmented generation,” in *Proc. Annu. Comput. Secur. Appl. Conf. Workshops (ACSACW)*, 2024, pp. 255–261.

[9] M. Büchel, T. Paladini, S. Longari, M. Carminati, S. Zanero, H. Binyamini, G. Engelberg, D. Klein, G. Guizzardi, M. Caselli, A. Continella, M. van Steen, A. Peter, and T. van Ede, “SoK: Automated TTP extraction from CTI reports—Are we there yet?” in *Proc. 34th USENIX Secur. Symp.*, 2025.

[10] V. Legoy, M. Caselli, C. Seifert, and A. Peter, “Automated retrieval of ATT&CK tactics and techniques for cyber threat reports,” arXiv:2004.14322, 2020.

[11] L. Lange, M. Müller, G. Haratinezhad Torbati, D. Milchevski, P. Grau, S. C. Pujari, and A. Friedrich, “AnnoCTR: A dataset for detecting and linking entities, tactics, and techniques in cyber threat reports,” in *Proc. Joint Int. Conf. Comput. Linguistics, Lang. Resources Eval. (LREC-COLING)*, 2024, pp. 1147–1160.
