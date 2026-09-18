### Inputs and preparation

The runner loads the 1,597 sentence rows in [bosch_test.json](../../../data/buchel/bosch_test.json) and retains the released grouping into 34 documents. It uses the Büchel label definitions for the 10-, 25-, 50-, and 118-label scopes. The open-scope calculation keeps unfiltered predictions while ground truth is restricted to the released technique labels.

The files distributed with the artifact require one scope-label distinction. The values identified as the published 50-label results equal the archived 25-label rows. Table 6 of our submission therefore averaged the archived 25-, 118-label, and open values when comparing with the paper. The runner also reports the actual 50-label row and its corresponding 50/118/open average so that the two interpretations are not mixed.

| Tool variant | Published “50” F1 / archived 25 F1 | 118-label F1 | Open F1 | Historical 25/118/open average |
|---|---:|---:|---:|---:|
| LADDER / Büchel release | 0.2403 | 0.2234 | 0.1807 | 0.2148 |
| AttacKG / Büchel release | 0.4278 | 0.3214 | 0.2375 | 0.3289 |

### Tools and execution paths

The artifact for [9] contains versions and setup instructions for LADDER and AttacKG under `ext_tools.zip`. These versions and setup details are different than the original repositories, so to reproduce Table 13 of [9], we use the code and setup details in `ext_tools.zip`. For direct execution without the framework we run `test_attackg_bosch.py` or `test_ladder_bosch.py` from `ext_tools.zip` in the corresponding `.venv` created during setup. The selected dataset is placed at the runner's expected relative path.

The Büchel LADDER variant changes model loading and inference data handling. The Büchel AttacKG variant uses different Python and package versions and caps graph search at 10,000 iterations. These variants are not interchangeable with the versions in the tools' original repositories, and this experiment does not attribute observed performance differences to any single change.

<!-- attackg-execution-environment -->

Native-runner executions retain `bosch_scores.txt`, including the released 10-label scope. The runner, archive, data, model, image, runtime, and temporary-change hashes accompany the result. Historical outputs from earlier workflows remain unchanged.

Source revisions, model staging, and necessary corrections are documented in the setup READMEs for [Büchel](../../../../../Docker_Setup/Buchel/README.md), [LADDER](../../../../../Docker_Setup/LADDER/README.md), and [AttacKG](../../../../../Docker_Setup/AttacKG/README.md). Model presence alone does not establish that a particular run completed inference.

### Scoring

The original scorer computes each grouped document's precision, recall, and F1, then averages across documents. Empty predictions with empty ground truth receive 1. The runner retains the released percentage-rounding procedure before converting scores to the 0–1 scale.

Saved framework predictions are scored through a generated replay copy of the corresponding released runner whose extractor function alone is replaced. Direct predictions and scores come from the released runner itself. The native document grouping, label filtering, metric functions, and rounding stay in use.

The comparison labels the paper-reference rows `published50_archived25`, while keeping actual 25-, 50-, 118-label, and open scores separate. Historical 25/118/open and actual 50/118/open averages must not be conflated.

## References

[9] M. Büchel, T. Paladini, S. Longari, M. Carminati, S. Zanero, H. Binyamini, G. Engelberg, D. Klein, G. Guizzardi, M. Caselli, A. Continella, M. van Steen, A. Peter, and T. van Ede, “SoK: Automated TTP extraction from CTI reports—Are we there yet?” in *Proc. 34th USENIX Secur. Symp.*, 2025.
