This exercise maps to the submission's **E2 / C2** artifact-evaluation criteria: selected original-versus-framework fidelity, and independent checking of the reproduction analysis in experiment 6, Table 6. These are two different evaluation questions.

| Evaluation question | Evidence and criterion | What it establishes |
|---|---|---|
| Did the selected execution paths work? | All four inference calls have recorded PASS status; each tool/backend supplies eight unique, expected report IDs with valid prediction lists. | Functionality for the AttacKG and TTPDrill integrations on this input. |
| Did the framework preserve the available tools' document-level predictions? | AttacKG and TTPDrill are expected to be deterministic predictors. For each tool, require exact equality of the original and framework code sets for every report: 8/8 matches, with no missing/extra IDs. | C2 document-level prediction fidelity for AttacKG and TTPDrill integrations. |
| Does the run support the published reproduction analysis? | Separately compare current results with the selected paper counts. | Evidence consistent with experiment 6's `✗ MC` outcome. Evaluators are not required to attain the prior paper's score to validate our documented inability to reproduce it. `M` is directly evidenced by the analysis discussed in the setup; `C` remains a probable explanation, not an isolated causal finding. |
