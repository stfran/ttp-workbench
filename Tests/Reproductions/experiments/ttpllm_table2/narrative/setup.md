### Inputs and preparation

The runner uses the copied [MITRE procedures](../../../data/MITRE_Procedures.csv) and [encoded tactic labels](../../../data/MITRE_Procedures_encoded.csv). Procedure and label rows must remain aligned. It retains the 14-column tactic encoding for comparison with the published table.

### Tools and execution paths

TTP-LLM runs directly from its staged repository in its own environment and through its framework adapter. Both paths call the `main.py` and receive the project-root `config.ini` without recording its credentials. Both request the `gpt-3.5-turbo` model and the prompt-only arguments.

Framework execution uses bulk batches of 100 input procedures, invoking inference and postprocessing once per batch. Each procedure still receives its own sequential API request with the original prompt and model settings. The adapter retains input/output mapping and checks all chunk-row counts before accepting a batch. Completed batches' raw outputs are saved before proceeding. The original tool's 20-row loader cap is removed in both native and container setup, and labels are never sliced to conceal incomplete predictions.

Note that the original experiment in [8] did not report multiple trials, so we do not either because it would be expensive and non-comparable.

### Scoring

Saved predictions are encoded into the tactic columns and checked against the gold-label array's shape. The classification report gives per-tactic precision, recall, F1, and support, with zero-division scores set to zero. The comparison exposes per-tactic F1/support and samples-average F1. Samples averaging operates over the procedure examples; it is not a macro average of the per-tactic F1 values. The paper's samples-average reference is 0.60.

## References

[8] R. Fayyazi, R. Taghdimi, and S. J. Yang, “Advancing TTP analysis: Harnessing the power of large language models with retrieval augmented generation,” in *Proc. Annu. Comput. Secur. Appl. Conf. Workshops (ACSACW)*, 2024, pp. 255–261.
