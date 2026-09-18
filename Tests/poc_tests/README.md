# Proof of Concept Tests

This directory contains three reports from prior work as examples for using an adapter on files. We pick one each from AnnoCTR [11], TRAM [7], an RAF-AG [3] as these are the largest sets of manually annotated reports. 


To run the proof concpet tests:
```
python Tests/poc_tests/run_poc_tests.py --engine podman --adapter all
```

```
python Tests/poc_tests/run_poc_tests.py --engine docker --adapter TTPDrill AttacKG
```

To compare the native and framework paths on the same reports after a complete
`install.sh --all` installation:

```
python Tests/poc_tests/run_poc_tests.py --engine docker --adapter TRAM \
  --backend both --fidelity-policy hybrid --device cuda
```

## Fidelity policies and `PASS`

Fidelity policies apply when `--backend both` compares the native and framework
paths. The comparison normalizes each report to a set of unique ATT&CK tactic
and technique identifiers.

- `strict`: `PASS` requires exact native/framework prediction-set agreement for
  every report.
- `diagnostic`: prediction differences and Jaccard agreement are reported, but
  differences do not cause failure.
- `hybrid`: exact agreement is required for AttacKG, LADDER, TTPDrill,
  Orbinato, RAF-AG, rcATT, SeqMask, and TRAM. Büchel and TTP-LLM use diagnostic
  comparison because their generation paths may vary.

Missing, duplicate, malformed, unexpected, or error-bearing results fail under
every policy. Jaccard is reported as evidence and is not used as a pass
threshold. With `--backend both`, an overall `PASS` means both paths passed
functional validation and the applicable fidelity policy passed. Under
`diagnostic`, `PASS` does not imply exact prediction agreement; consult the
reported agreement, exact-report count, and Jaccard values.


## References

[3] K. Mai, J. Lee, R. Beuran, R. Hotchi, S. E. Ooi, T. Kuroda, and Y. Tan, “RAF-AG: Report analysis framework for attack path generation,” *Computers & Security*, vol. 148, Art. no. 104125, 2025.

[7] MITRE, “Threat Report ATT&CK Mapper (TRAM),” 2023. [Online]. Available: <https://github.com/center-for-threat-informed-defense/tram/>. [Accessed: Apr. 30, 2025].

[11] L. Lange, M. Müller, G. Haratinezhad Torbati, D. Milchevski, P. Grau, S. C. Pujari, and A. Friedrich, “AnnoCTR: A dataset for detecting and linking entities, tactics, and techniques in cyber threat reports,” in *Proc. Joint Int. Conf. Comput. Linguistics, Lang. Resources Eval. (LREC-COLING)*, 2024, pp. 1147–1160.
