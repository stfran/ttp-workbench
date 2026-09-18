### Relationship to our Table 6 outcome

The results of the current run are in the following table. The columns labeled Table 6 Paper and Table 6 Repro. give the reference samples-average-F1 values for Table 6 of our submission. “This run” columns are populated from this run using the natively executed original tool and the framework version of the tool.

<!-- table6-results -->

NOTE: Row 1 of Table 6 of our submission reports both the Paper and Repro. columns as 0.600, but the original result in Table 2 of [8] is reported to two decimal places as 0.60, so both should use the same precision. This editorial error will be corrected in the camera-ready version of the submitted paper.

`✓` means reproduced/comparable and `N` means nondeterminism.

### Interpreting this run

<!-- run-interpretation -->

### Checks on this run

These checks read the saved encoded prediction files and inference status entries; they do not infer prediction equality from matching aggregate scores or rerun the service.

<!-- run-checks -->

<!-- saved-evidence -->

A full-run PASS supports C2 for this prompt-only configuration and these 9,532 procedures. It does not validate the paper's retrieval-augmented configurations, guarantee the behavior of a future model-service snapshot, or turn stochastic prediction differences into execution failures.
