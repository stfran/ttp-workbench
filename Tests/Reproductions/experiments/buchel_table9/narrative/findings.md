### Relationship to our Table 6 outcome

The results of the current run are in the following table. The columns labeled Table 6 Paper and Table 6 Repro. give the reference configuration-grid-average F1 values from experiment 2 of our submission. “This run” columns use the matching 16-cell grid from the natively executed original tool and the framework version of the tool.

<!-- table6-results -->

<!-- table6-scope-note -->

We ultimately consider the result close enough to be reproduced/comparable
under the documented conditions, so the outcome should be read as `✓ N C`:
`✓` means reproduced/comparable, `N` means nondeterminism, and `C` means
code/model/artifact drift. The absence of `C` from experiment 2 in the
submitted Table 6 is an editorial error that we will correct in revision.

[Büchel et al.'s artifact appendix](https://www.usenix.org/system/files/usenixsecurity25-appendix-buechel.pdf)
describes this condition directly. It states that the paper used Unsloth
2024.12.4 with Transformers 4.44.2, that the authors could no longer reproduce
that dependency combination during artifact preparation, and that their newer
dependency pair changes generation behavior, precision, recall, and F1. The
differences observed in our reproduction are consistent with the behavior that
the Büchel artifact documents. We therefore retain the successful reproduction
classification while using `C` to qualify why an exact numerical match is not
expected from the released artifact environment.

### Interpreting this run

Keep base and fine-tuned results separate, and distinguish the `sft_tram_local` checkpoint retained in this run from the published Zenodo checkpoint reported as `sft_tram`. Check that all cells contributing to an average executed and used the intended data and models.

Missing retrieval dependencies, unavailable weights, and incomplete inference are execution limitations, not evidence of poor model performance. Matching isolated scores does not establish equivalence of the full experiment. Current-run findings require inspection of the corresponding raw outputs and retained ID-based metrics.
