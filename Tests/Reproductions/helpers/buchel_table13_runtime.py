"""Experiment-only runtime helpers; compatible with standard AttacKG's Python 3.8.

Original execution runs the byte-identical archive evaluator via runpy. A return
observer saves predictions without changing the native function or its results.
Framework replay changes only extraction imports/function in a disposable copy.
"""
import argparse
import ast
from contextlib import contextmanager
import difflib
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def inventory():
    return {'python': sys.version, 'executable': sys.executable,
            'packages': {d.metadata['Name']: d.version for d in importlib.metadata.distributions()}}


def variant_source(source, variant):
    if variant != 'capped':
        raise ValueError("Buchel Table 13 supports only the released capped AttacKG configuration")
    return source


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(str(path))
    try:
        yield
    finally:
        os.chdir(str(previous))


def prepare(payload, target, manifest):
    """Compare every selected file, copying only additions/byte differences."""
    import shutil
    spec = json.loads((payload / 'spec.json').read_text())
    records = []
    patches = []
    for relative in spec['files']:
        src, dst = payload / 'files' / relative, target / relative
        before = digest(dst) if dst.is_file() else None
        after = digest(src)
        record = {'path': relative, 'before_sha256': before, 'after_sha256': after,
                  'action': 'unchanged' if before == after else ('replace' if before else 'add')}
        if before != after:
            if dst.is_symlink():
                raise ValueError('Refusing to edit through container symlink: ' + str(dst))
            if src.suffix in ('.py', '.csv', '.json') and src.stat().st_size < 200000:
                patches.extend(difflib.unified_diff(
                    dst.read_text().splitlines(True) if before else [], src.read_text().splitlines(True),
                    fromfile='image/' + relative, tofile='experiment/' + relative))
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(src), str(dst))
            if digest(dst) != after:
                raise ValueError('Container copy verification failed: ' + relative)
        records.append(record)
    # Standard-only template JSONs would otherwise also be loaded by os.listdir.
    expected = set(spec['files'])
    if spec['tool'] == 'attackg':
        for path in sorted((target / 'templates').glob('*.json')):
            relative = path.relative_to(target).as_posix()
            if relative not in expected:
                records.append({'path': relative, 'action': 'remove',
                                'before_sha256': digest(path), 'after_sha256': None})
                path.unlink()
    for relative, after in spec.get('assets', {}).items():
        dst = target / relative
        before = digest(dst) if dst.is_file() else None
        records.append({'path': relative, 'before_sha256': before, 'after_sha256': after,
                        'asset': True, 'action': 'unchanged' if before == after else ('replace' if before else 'add')})
    manifest.write_text(json.dumps(dict(spec, runtime=inventory(), files=records), indent=2))
    manifest.with_suffix('.diff').write_text(''.join(patches))


def _extract(tool, target, source, output):
    sys.path.insert(0, str(target))
    if tool == 'attackg':
        import main
        with tempfile.TemporaryDirectory(prefix='buchel-') as directory:
            prefix = str(Path(directory) / 'result')
            main.main({'logPath': '', 'mode': 'techniqueIdentification', 'ctiText': '',
                       'reportPath': str(source), 'outputPath': prefix, 'templatePath': './templates'})
            raw = json.loads(Path(prefix + '_techniques.json').read_text())
    else:
        import inference
        import distance
        phrases = inference.infer(source.read_text())
        raw = distance.get_all_attack_patterns('\n'.join(phrases), th=0.6) if phrases else {}
    output.write_text(json.dumps(raw, indent=2, default=lambda x: x.item()))


def extract(tool, target, source, output):
    with working_directory(target):
        return _extract(tool, target, source, output)


def replay_source(source, tool):
    """Keep native grouping/scoring verbatim; replace extraction only."""
    lines = source.splitlines(True)
    edits = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import) and any(n.name in ('main', 'inference', 'distance') for n in node.names):
            if len(node.names) != 1:
                raise ValueError('Unexpected mixed extraction import')
            edits.append((node.lineno - 1, node.end_lineno, ''))
        if isinstance(node, ast.FunctionDef) and node.name == tool:
            edits.append((node.lineno - 1, node.end_lineno,
                          'def ' + tool + '(*args, **kwargs):\n    return next(_ae_predictions)\n'))
    if len(edits) != (2 if tool == 'attackg' else 3):
        raise ValueError('Unexpected native runner structure')
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = [replacement]
    return ''.join(lines)


def _native(tool, target, output, predictions=None):
    import shutil
    sys.path.insert(0, str(target))
    script = target / ('test_' + tool + '_bosch.py')
    source = script.read_text()
    output.mkdir(parents=True, exist_ok=True)
    runtime_name = 'scoring_runtime.json' if predictions is not None else 'runtime.json'
    (output / runtime_name).write_text(json.dumps(inventory(), indent=2))
    rows = []
    raw_dir = output / 'raw'
    raw_dir.mkdir(exist_ok=True)
    if predictions is not None:
        rows = json.loads(predictions.read_text())
        if not isinstance(rows, list) or not rows:
            raise ValueError('No replay predictions')
        import pandas as pd
        data_path = '../../dataset/bosch_test.json' if tool == 'attackg' else '../../../dataset/bosch_test.json'
        expected = ['input_%04d' % i for i in range(pd.read_json(data_path).document.nunique())]
        if [r.get('id') for r in rows] != expected or any(
                r.get('error') or r.get('status') in ('failed', 'error') or
                not isinstance(r.get('ttps'), list) or
                not all(isinstance(t, str) and t for t in r['ttps']) for r in rows):
            raise ValueError('Incomplete, failed, or reordered replay predictions')
        updated = replay_source(source, tool)
        (output / 'native_replay.py').write_text(updated)
        (output / 'native_replay.diff').write_text(''.join(difflib.unified_diff(
            source.splitlines(True), updated.splitlines(True), fromfile=script.name, tofile='native_replay.py')))
        exec(compile(updated, str(script), 'exec'), {'__name__': '__main__',
             '_ae_predictions': iter([r['ttps'] for r in rows])})
    else:
        last_mapping = [{}]
        # Observe function returns only. No source edit or alteration of locals.
        def observe(frame, event, value):
            if event == 'return' and isinstance(value, dict) and (
                    tool == 'ladder' and frame.f_code.co_filename == str(target / 'distance.py') and
                    frame.f_code.co_name == 'get_all_attack_patterns'):
                last_mapping[0] = value
            elif event == 'return' and isinstance(value, list) and (
                    frame.f_code.co_filename == str(script) and frame.f_code.co_name == tool):
                index = len(rows)
                raw = frame.f_locals['out'] if tool == 'attackg' else (last_mapping[0] if value else {})
                if set(raw) != set(value):
                    raise ValueError('Native observation does not match returned predictions')
                (raw_dir / ('input_%04d.json' % index)).write_text(
                    json.dumps(raw, indent=2, default=lambda x: x.item()))
                rows.append({'id': 'input_%04d' % index, 'ttps': value})
                (output / 'predictions.partial.json').write_text(json.dumps(rows, indent=2))
        old_profile = sys.getprofile()
        sys.setprofile(observe)
        try:
            result = runpy.run_path(str(script), run_name='__main__')
        finally:
            sys.setprofile(old_profile)
        if len(rows) != len(result['test_docs']) or not rows:
            raise ValueError('Native observation is incomplete')
    shutil.copyfile(str(target / 'bosch_scores.txt'), str(output / 'bosch_scores.txt'))
    (output / 'parsed').mkdir(exist_ok=True)
    (output / 'parsed/predictions.json').write_text(json.dumps(rows, indent=2))


def native(tool, target, output, predictions=None):
    with working_directory(target):
        return _native(tool, target, output, predictions)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['prepare', 'extract', 'native', 'verify'])
    parser.add_argument('--tool', choices=['attackg', 'ladder'], required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--payload', type=Path)
    parser.add_argument('--predictions', type=Path)
    args = parser.parse_args()
    if args.mode == 'verify':
        manifest = json.loads(args.output.read_text())
        for record in manifest['files']:
            path = args.target / record['path']
            actual = digest(path) if path.is_file() else None
            if actual != record['after_sha256']:
                raise ValueError('Final file verification failed: ' + str(path))
        # ``prepare`` may run with the base image's interpreter while a derived
        # experiment image is still being assembled.  Record the interpreter
        # that performs the final verification so the saved runtime describes
        # the environment that actually performs inference.
        manifest['runtime'] = inventory()
        manifest['verified'] = True
        args.output.write_text(json.dumps(manifest, indent=2))
    elif args.mode == 'prepare':
        prepare(args.payload.resolve(), args.target.resolve(), args.output.resolve())
    elif args.mode == 'extract':
        extract(args.tool, args.target.resolve(), args.input.resolve(), args.output.resolve())
    else:
        native(args.tool, args.target.resolve(), args.output.resolve(),
               args.predictions.resolve() if args.predictions else None)


if __name__ == '__main__':
    main()
