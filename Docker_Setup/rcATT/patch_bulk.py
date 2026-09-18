"""Add optional preloaded state without changing the upstream single-report path."""
import argparse
from pathlib import Path


def replace_once(path, before, after):
    text = path.read_text()
    if after in text:
        return
    if text.count(before) != 1:
        raise RuntimeError("Unexpected upstream source at {}: {!r}".format(path, before))
    path.write_text(text.replace(before, after, 1))


def patch(root):
    replace_once(root / "classification_tools/__init__.py",
                 "def predict(report_to_predict, post_processing_parameters):",
                 "def predict(report_to_predict, post_processing_parameters, cached_pipelines=None):")
    replace_once(root / "classification_tools/__init__.py",
                 "\tpipeline_tactics = joblib.load('classification_tools/data/pipeline_tactics.joblib')\n\tpipeline_techniques = joblib.load('classification_tools/data/pipeline_techniques.joblib')",
                 "\tif cached_pipelines is None:\n\t\tpipeline_tactics = joblib.load('classification_tools/data/pipeline_tactics.joblib')\n\t\tpipeline_techniques = joblib.load('classification_tools/data/pipeline_techniques.joblib')\n\telse:\n\t\tpipeline_tactics, pipeline_techniques = cached_pipelines")
    replace_once(root / "rcATT_cmd.py", "def predict(report_to_predict_file, output_file, title, date):",
                 "def predict(report_to_predict_file, output_file, title, date, cached_parameters=None, cached_pipelines=None):")
    replace_once(root / "rcATT_cmd.py", '\tparameters = joblib.load("classification_tools/data/configuration.joblib")',
                 '\tparameters = cached_parameters if cached_parameters is not None else joblib.load("classification_tools/data/configuration.joblib")')
    replace_once(root / "rcATT_cmd.py", "clt.predict(report_to_predict, parameters)",
                 "clt.predict(report_to_predict, parameters, cached_pipelines=cached_pipelines)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    patch(parser.parse_args().root)
