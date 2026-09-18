"""Optional Table 6 coverage. No optional experiment has a Markdown renderer."""

OPTIONAL = {
    "orbinato_fig3": dict(title="Orbinato Figure 3", runner="reproduce_orbinato_fig3.py", tools=["Orbinato"], row=4, paper={"Orbinato": .46}),
    "rcatt_table6": dict(
        title="rcATT Table 6",
        runner="reproduce_rcatt_table6.py",
        tools=["rcATT"],
        row=5,
        paper={"rcATT": .093},
        submission_repro={"rcATT": {"techniques/micro_f0.5": .278}},
    ),
    "rafag_table6": dict(
        title="RAF-AG Table 6",
        runner="reproduce_rafag_table6.py",
        tools=["RAF-AG", "AttacKG"],
        row=7,
        paper={"RAF-AG": .708, "AttacKG": .393},
        paper_metrics={
            "RAF-AG": {"document_precision": .717, "document_recall": .722, "document_f1": .708},
            "AttacKG": {"document_precision": .337, "document_recall": .535, "document_f1": .393},
        },
        submission_repro={
            "RAF-AG": {"document_f1": .496},
            "AttacKG": {"document_f1": .181},
        },
    ),
    "seqmask_table7": dict(
        title="SeqMask Table 7", runner="reproduce_seqmask.py", tools=["SeqMask", "rcATT"], row=9, table="7",
        paper={"SeqMask": .755, "rcATT": .021},
        paper_metrics={
            "SeqMask": {"micro_precision": .8315, "micro_recall": .6910, "micro_f1": .7548},
            "rcATT": {"micro_precision": .0106, "micro_recall": .8496, "micro_f1": .0207},
        },
        submission_repro={"SeqMask": {"micro_f1": .888}, "rcATT": {"micro_f1": .153}},
    ),
    "seqmask_table14": dict(
        title="SeqMask Table 14", runner="reproduce_seqmask.py", tools=["SeqMask", "rcATT"], row=10, table="14",
        paper={"SeqMask": .621, "rcATT": .154},
        paper_metrics={
            "SeqMask": {"micro_precision": .8417, "micro_recall": .4922, "micro_f1": .6211},
            "rcATT": {"micro_precision": .8149, "micro_recall": .0891, "micro_f1": .1544},
        },
        submission_repro={"SeqMask": {"micro_f1": .756}, "rcATT": {"micro_f1": .304}},
    ),
    "seqmask_table15": dict(
        title="SeqMask Table 15", runner="reproduce_seqmask.py", tools=["SeqMask", "rcATT"], row=11, table="15",
        paper={"SeqMask": .515, "rcATT": .349},
        paper_metrics={
            "SeqMask": {"micro_precision": .6861, "micro_recall": .4118, "micro_f1": .5147},
            "rcATT": {"micro_precision": .2113, "micro_recall": .9999, "micro_f1": .3488},
        },
        submission_repro={"SeqMask": {"micro_f1": .046}, "rcATT": {"micro_f1": .861}},
    ),
}
for spec in OPTIONAL.values():
    spec["report_mode"] = "data_only"
