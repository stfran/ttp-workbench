### Inputs and preparation

The copied datasets are [AnnoCTR/Bosch](../../../data/buchel/bosch_cti_test_ds.json) and [TRAM](../../../data/buchel/test_split.json). The runner retains native dataset-item grouping, sentence-level prompts, and the released per-item aggregation. The framework's raw responses and the direct tool's prediction CSVs have different formats; both must be interpreted using the same scoring scope.

### Tools and execution paths

<!-- model-grid-description -->

The original path calls the released `finetuning_test`; the framework path uses BuchelAdapter with model mounts. RAG also requires the released Qwen embedding service. The authoritative generation image does not contain saved fine-tuned weights, so staged merged weights are supplied separately. Source pins, model provenance, and staging locations are documented in the [reproduction README](../../../README.md). The runner does not automatically train missing models.

### Scoring

The comparison uses technique-ID metrics from the released scorer. Each configuration keeps its own F1, precision, and recall. The submitted Table 6 summarizes a historical configuration-grid average for compactness.

## References

[7] MITRE, “Threat Report ATT&CK Mapper (TRAM),” 2023. [Online]. Available: <https://github.com/center-for-threat-informed-defense/tram/>. [Accessed: Apr. 30, 2025].

[9] M. Büchel, T. Paladini, S. Longari, M. Carminati, S. Zanero, H. Binyamini, G. Engelberg, D. Klein, G. Guizzardi, M. Caselli, A. Continella, M. van Steen, A. Peter, and T. van Ede, “SoK: Automated TTP extraction from CTI reports—Are we there yet?” in *Proc. 34th USENIX Secur. Symp.*, 2025.

[11] L. Lange, M. Müller, G. Haratinezhad Torbati, D. Milchevski, P. Grau, S. C. Pujari, and A. Friedrich, “AnnoCTR: A dataset for detecting and linking entities, tactics, and techniques in cyber threat reports,” in *Proc. Joint Int. Conf. Comput. Linguistics, Lang. Resources Eval. (LREC-COLING)*, 2024, pp. 1147–1160.
