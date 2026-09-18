This exercise maps to the submission's **E2 / C2** artifact-evaluation criteria: selected original-versus-framework fidelity, and independent checking of the reproduction analysis in experiment 8, Table 6. These are two different evaluation questions.

| Evaluation question | Evidence and criterion | What it establishes |
|---|---|---|
| Did the selected execution paths work? | Require all six inference calls to have recorded PASS status; each tool/backend must supply the five unique, expected report IDs with valid prediction lists. | Functionality for the LADDER, AttacKG, and TTPDrill integrations on this input. |
| Did the framework preserve the available tools' document-level predictions? | LADDER, AttacKG, and TTPDrill are expected to be deterministic predictors. For each tool, require exact equality of the original and framework code sets for every report: 5/5 matches, with no missing/extra IDs. | C2 document-level prediction fidelity for the LADDER, AttacKG, and TTPDrill integrations. |
| Does the run support the published reproduction analysis? | Separately compare current results with the selected paper F1 values and captured labels. | Evidence consistent with experiment 8's outcomes: TTPDrill `✗ M`, AttacKG `✓ M`, and LADDER `✗ M`. The recovered reports and converted annotations documented in the setup explain the `M` outcome. |
