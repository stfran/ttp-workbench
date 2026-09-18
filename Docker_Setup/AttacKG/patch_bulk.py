#!/usr/bin/env python3
"""Apply the minimal, idempotent resource-injection patch for bulk AttacKG."""
from pathlib import Path
import sys


def replace_once(text, old, new):
    if new in text:
        return text
    if text.count(old) != 1:
        raise RuntimeError("AttacKG source did not match the pinned revision")
    return text.replace(old, new)


def patch(root):
    path = Path(root) / "main.py"
    text = path.read_text()
    text = replace_once(
        text,
        "def report_parsing(text: str) -> Tuple[IoCIdentifier, Doc]:",
        "def report_parsing(text: str, ner_model: IoCNer = None) -> Tuple[IoCIdentifier, Doc]:",
    )
    text = replace_once(
        text,
        '    ner_model = IoCNer("./new_cti.model")\n    doc = ner_model.parse(text_without_ioc)',
        '    if ner_model is None:\n        ner_model = IoCNer("./new_cti.model")\n    doc = ner_model.parse(text_without_ioc)',
    )
    text = replace_once(
        text,
        "def attackGraph_generating(text: str, output: str = None) -> AttackGraph:",
        "def attackGraph_generating(text: str, output: str = None, ner_model: IoCNer = None) -> AttackGraph:",
    )
    text = replace_once(
        text,
        "    iid, doc = report_parsing(text)",
        "    iid, doc = report_parsing(text, ner_model=ner_model)",
    )
    text = replace_once(
        text,
        'def technique_identifying(text: str, technique_list: List[str], template_path: str, output_file: str = "output") -> AttackMatcher:\n    ag = attackGraph_generating(text)\n    if template_path == "":',
        'def technique_identifying(text: str, technique_list: List[str], template_path: str, output_file: str = "output",\n'
        '                          ner_model: IoCNer = None,\n'
        '                          template_list: List[TechniqueTemplate] = None) -> AttackMatcher:\n'
        '    ag = attackGraph_generating(text, ner_model=ner_model)\n'
        '    if template_list is not None:\n'
        '        tt_list = template_list\n'
        '    elif template_path == "":',
    )
    path.write_text(text)


if __name__ == "__main__":
    patch(sys.argv[1] if len(sys.argv) > 1 else ".")
