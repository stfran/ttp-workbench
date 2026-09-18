"""Pristine archive staging and disposable standard-container integration."""
from contextlib import contextmanager
import difflib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
from zipfile import ZipFile

from helpers.buchel_table13_runtime import digest, variant_source
from helpers.reporting import PROJECT

RUNTIME = Path(__file__).with_name('buchel_table13_runtime.py')
RELATIVE = {'attackg': 'tools/AttacKG', 'ladder': 'tools/LADDER/attack_pattern'}
TARGET = {'attackg': '/opt/AttacKG', 'ladder': '/opt/LADDER/attack_pattern'}
SHORTEST_PATH_PATCH = (PROJECT /
                       'Docker_Setup/AttacKG/patch_shortest_path_cache.py')
BUCHEL_ATTACKG_PYTHON = '/opt/buchel-venv/bin/python'
IMAGE_RUNTIME = '/opt/buchel-experiment/runtime.py'
IMAGE_MANIFEST = '/opt/buchel-experiment/manifest.json'
IMAGE_DIFF = '/opt/buchel-experiment/manifest.diff'
BOOTSTRAP_PACKAGES = {'pip', 'setuptools'}
# The project source is on the native runner's sys.path, so importlib.metadata
# sees its checkout metadata even though it is not installed in the tool venv.
HOST_ONLY_DISTRIBUTIONS = {'ttp-workbench'}


def memoize_shortest_paths(source):
    """Use the same source transformation as the maintained AttacKG image."""
    spec = importlib.util.spec_from_file_location('attackg_shortest_path_cache', SHORTEST_PATH_PATCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.memoized_source(source)


def stage(archive, root, work, data, tool, variant, profile):
    """Never modify the shared external_tools source tree."""
    import pandas as pd
    work.mkdir(parents=True, exist_ok=False)
    with ZipFile(archive) as z:
        for member in z.infolist():
            path = Path(member.filename)
            if path.is_absolute() or '..' in path.parts or path.parts[0] != 'ext_tools':
                raise ValueError('Unexpected archive member: ' + member.filename)
            if ((member.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError('Archive symlinks are not accepted')
        z.extractall(work)
    release = work / 'ext_tools'
    target = release / RELATIVE[tool]
    runner = target / ('test_' + tool + '_bosch.py')
    manifest = {'archive': str(archive), 'archive_sha256': digest(archive), 'tool': tool,
                'variant': variant, 'profile': profile, 'native_runner_sha256': digest(runner),
                'native_runner_modified': False, 'data_source': str(data), 'data_source_sha256': digest(data)}
    table = pd.read_json(data)
    if profile == 'smoke':
        first = table.groupby('document').size().index[0]
        table = table[table.document == first]
        table.to_json(release / 'dataset/bosch_test.json', orient='records')
    else:
        shutil.copyfile(data, release / 'dataset/bosch_test.json')
    manifest['documents'] = int(table.document.nunique())
    manifest['data_used_sha256'] = digest(release / 'dataset/bosch_test.json')
    if tool == 'attackg':
        path = target / 'technique_knowledge_graph/technique_identifier.py'
        source = path.read_text()
        variant_updated = variant_source(source, variant)
        updated = memoize_shortest_paths(variant_updated)
        path.write_text(updated)
        (work / 'variant.diff').write_text(''.join(difflib.unified_diff(
            source.splitlines(True), variant_updated.splitlines(True),
            fromfile='released/'+path.name, tofile=variant+'/'+path.name)))
        (work / 'shortest_path_cache.diff').write_text(''.join(difflib.unified_diff(
            variant_updated.splitlines(True), updated.splitlines(True),
            fromfile=variant+'/'+path.name, tofile=variant+'+distance-cache/'+path.name)))
        manifest['shortest_path_memoization'] = True
        assets = root / RELATIVE[tool] / 'new_cti.model'
        asset_dir = 'new_cti.model'
        required = ['config.cfg', 'meta.json', 'ner/model', 'tok2vec/model', 'tokenizer']
    else:
        assets = root / RELATIVE[tool] / 'models'
        asset_dir = 'models'
        required = ['entity_ext.pt', 'sent_cls.pt']
    if any(not (assets / name).is_file() for name in required):
        raise FileNotFoundError('Required staged model assets missing: ' + str(assets))
    destination = target / asset_dir
    if destination.exists():
        # The published archive has no supplied trained weights; fail on drift.
        if any(destination.iterdir()):
            raise ValueError('Unexpected model contents in archive: ' + str(destination))
        destination.rmdir()
    destination.symlink_to(assets.resolve(), target_is_directory=True)
    manifest['assets'] = {asset_dir + '/' + p.relative_to(assets).as_posix(): digest(p)
                          for p in sorted(assets.rglob('*')) if p.is_file()}
    (work / 'source_manifest.json').write_text(json.dumps(manifest, indent=2))
    return target, manifest


def make_payload(target, work, tool, manifest):
    payload = work / 'payload'
    files = payload / 'files'
    files.mkdir(parents=True)
    if tool == 'attackg':
        selected = [target / 'main.py', target / 'requirements.txt']
        for directory in ('mitre_ttps', 'preprocess', 'report_parser', 'technique_knowledge_graph'):
            selected.extend((target / directory).glob('*.py'))
        selected.extend(target.glob('*regexPattern.json'))
        selected.append(target / 'ioc_replaceWord.json')
        selected.extend((target / 'templates').glob('*.json'))
    else:
        selected = [target / name for name in ('argparser.py', 'config.py', 'distance.py',
                     'inference.py', 'models.py', 'enterprise-techniques.csv')]
    for path in selected:
        relative = path.relative_to(target)
        out = files / relative
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, out)
    spec = dict(manifest, files=sorted(p.relative_to(target).as_posix() for p in selected))
    (payload / 'spec.json').write_text(json.dumps(spec, indent=2))
    return payload


def _inspect(engine, target):
    return json.loads(subprocess.check_output([engine, 'image', 'inspect', target], text=True))[0]


def _container_record(engine, cid, image, *, temporary_image):
    inspected = json.loads(subprocess.check_output([engine, 'inspect', cid], text=True))[0]
    return {
        'container_id': cid,
        'image_id': inspected['Image'],
        'image_tag': image,
        'ephemeral': True,
        'committed': False,
        'temporary_image': temporary_image,
    }


def _copy_image_evidence(ds, work):
    work.mkdir(parents=True, exist_ok=True)
    ds.cp_from(IMAGE_MANIFEST, work / 'container_changes.json')
    ds.cp_from(IMAGE_DIFF, work / 'container_changes.diff')


def _thread_env():
    return {key: os.environ[key] for key in (
        'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'
    ) if key in os.environ}


def _package_key(name):
    return name.strip().lower().replace('_', '-')


def _requirement_names(path):
    names = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        names.add(_package_key(line.split(' @ ', 1)[0].split('==', 1)[0]))
    return names


def _write_native_environment_requirements(native_runtime, requirements, context):
    """Capture native packages omitted by the released requirements file."""
    runtime = json.loads(native_runtime.read_text())
    packages = {_package_key(name): version
                for name, version in runtime['packages'].items()}
    required = _requirement_names(requirements)
    missing_bootstrap = BOOTSTRAP_PACKAGES - packages.keys()
    if missing_bootstrap:
        raise ValueError('Native runtime is missing bootstrap packages: ' +
                         ', '.join(sorted(missing_bootstrap)))
    bootstrap = {name: packages[name] for name in sorted(BOOTSTRAP_PACKAGES)}
    supplemental = {
        name: packages[name]
        for name in sorted(packages.keys() - required - BOOTSTRAP_PACKAGES -
                           HOST_ONLY_DISTRIBUTIONS)
    }
    paths = {
        'bootstrap': context / 'bootstrap-requirements.txt',
        'supplemental': context / 'supplemental-requirements.txt',
    }
    paths['bootstrap'].write_text(
        ''.join(name + '==' + version + '\n' for name, version in bootstrap.items()))
    paths['supplemental'].write_text(
        ''.join(name + '==' + version + '\n' for name, version in supplemental.items()))
    return paths, bootstrap, supplemental


def _write_attackg_containerfile(context):
    """Write the reproducible, run-scoped Büchel AttacKG image recipe."""
    containerfile = context / 'Containerfile'
    containerfile.write_text("""\
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

LABEL org.ttp-workbench.temporary="true" \\
      org.ttp-workbench.experiment="buchel_table13" \\
      org.ttp-workbench.tool="attackg"

COPY runtime.py /opt/buchel-experiment/runtime.py
COPY payload/ /opt/buchel-experiment/payload/
COPY bootstrap-requirements.txt /opt/buchel-experiment/bootstrap-requirements.txt

# Compare the base image with Büchel's released source, copy only byte-different
# files, remove standard-only templates, and verify the staged model assets.
RUN /opt/venv/bin/python /opt/buchel-experiment/runtime.py prepare \\
      --tool attackg \\
      --target /opt/AttacKG \\
      --payload /opt/buchel-experiment/payload \\
      --output /opt/buchel-experiment/manifest.json

# Do not modify the Python 3.8 environment retained by the standard AttacKG
# image.  Büchel's exact requirements are installed in an isolated Python 3.10
# environment used by framework execution and checked against the native run.
RUN apt-get update \\
 && apt-get install -y --no-install-recommends python3.10 python3.10-venv python3.10-distutils \\
 && python3.10 -m venv /opt/buchel-venv \\
 && /opt/buchel-venv/bin/python -m pip install --no-cache-dir \\
      -r /opt/buchel-experiment/bootstrap-requirements.txt \\
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /opt/buchel-experiment/requirements.txt
COPY supplemental-requirements.txt /opt/buchel-experiment/supplemental-requirements.txt
RUN /opt/buchel-venv/bin/pip install --no-cache-dir \\
      -r /opt/buchel-experiment/requirements.txt \\
      -r /opt/buchel-experiment/supplemental-requirements.txt \\
 && cd /opt/AttacKG \\
 && /opt/buchel-venv/bin/python -c "import coreferee, pkg_resources" \\
 && /opt/buchel-venv/bin/python /opt/buchel-experiment/runtime.py verify \\
      --tool attackg \\
      --target /opt/AttacKG \\
      --output /opt/buchel-experiment/manifest.json
""", encoding='utf-8')
    return containerfile


def _image_exists(engine, image):
    result = subprocess.run([engine, 'image', 'inspect', image],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


@contextmanager
def temporary_attackg_image(engine, target, work, manifest, native_runtime,
                            base_image='ttp-workbench:attackg'):
    """Build a derived framework image for this call, then remove it."""
    context = work / 'build_context'
    context.mkdir(parents=True)
    payload = make_payload(target, context, 'attackg', manifest)
    shutil.copyfile(target / 'requirements.txt', context / 'requirements.txt')
    environment_paths, bootstrap, supplemental = _write_native_environment_requirements(
        native_runtime, context / 'requirements.txt', context)
    shutil.copyfile(RUNTIME, context / 'runtime.py')
    containerfile = _write_attackg_containerfile(context)
    image = 'ttp-workbench:attackg-buchel-' + uuid.uuid4().hex[:12]
    base = _inspect(engine, base_image)
    metadata = {
        'base_image_tag': base_image,
        'base_image_id': base['Id'],
        'temporary_image_tag': image,
        'temporary': True,
        'retained_after_run': None,
        'containerfile': str(containerfile),
        'requirements_sha256': digest(context / 'requirements.txt'),
        'native_runtime': str(native_runtime),
        'bootstrap_requirements': bootstrap,
        'supplemental_requirements': supplemental,
        'bootstrap_requirements_sha256': digest(environment_paths['bootstrap']),
        'supplemental_requirements_sha256': digest(environment_paths['supplemental']),
        'payload_spec_sha256': digest(payload / 'spec.json'),
    }
    (work / 'image.json').write_text(json.dumps(metadata, indent=2) + '\n')
    error = None
    try:
        subprocess.run([engine, 'build', '--build-arg', 'BASE_IMAGE=' + base_image,
                        '-t', image, '-f', str(containerfile), str(context)], check=True)
        built = _inspect(engine, image)
        metadata['temporary_image_id'] = built['Id']
        metadata['created'] = built.get('Created')
        (work / 'image.json').write_text(json.dumps(metadata, indent=2) + '\n')
        saved_manifest = subprocess.check_output(
            [engine, 'run', '--rm', '--entrypoint', '/bin/cat', image, IMAGE_MANIFEST],
            text=True)
        (work / 'container_changes.json').write_text(saved_manifest)
        saved_diff = subprocess.check_output(
            [engine, 'run', '--rm', '--entrypoint', '/bin/cat', image, IMAGE_DIFF],
            text=True)
        (work / 'container_changes.diff').write_text(saved_diff)
        yield image, built['Id']
    except BaseException as exc:
        error = exc
        raise
    finally:
        removal = subprocess.run([engine, 'image', 'rm', '-f', image],
                                 capture_output=True, text=True)
        retained = _image_exists(engine, image)
        metadata['cleanup'] = {
            'attempted': True,
            'returncode': removal.returncode,
            'stdout': removal.stdout,
            'stderr': removal.stderr,
            'image_reference_present_after_cleanup': retained,
        }
        metadata['retained_after_run'] = retained
        (work / 'image.json').write_text(json.dumps(metadata, indent=2) + '\n')
        if retained:
            cleanup_error = RuntimeError(
                'Temporary Büchel AttacKG image was not removed: ' + image)
            if error is not None:
                raise cleanup_error from error
            raise cleanup_error


def adapter_for(tool, variant, target, work, manifest, engine, input_dir, verbose=False,
                image=None, prepared_image=False, expected_image_id=None):
    from Framework.adapters.attackg_adapter import AttacKGAdapter
    payload = None if prepared_image else make_payload(target, work, tool, manifest)
    class BuchelToolAdapter(AttacKGAdapter):
        # Buchel's AttacKG implementation is modified from the original so we create a separate version for it.
        workdir = TARGET[tool]
        def prepare_session(self, ds):
            record = _container_record(engine, ds.cid, self.image,
                                       temporary_image=prepared_image)
            if expected_image_id is not None and record['image_id'] != expected_image_id:
                raise RuntimeError('Framework execution did not start the recorded temporary image')
            work.mkdir(parents=True, exist_ok=True)
            (work / 'container.json').write_text(json.dumps(record, indent=2) + '\n')
            if prepared_image:
                ds.exec(BUCHEL_ATTACKG_PYTHON, IMAGE_RUNTIME, 'verify',
                        '--tool', tool, '--target', self.workdir,
                        '--output', IMAGE_MANIFEST, env=self.env)
                _copy_image_evidence(ds, work)
                return
            ds.exec('mkdir', '-p', '/tmp/buchel-experiment/payload')
            ds.cp_to(RUNTIME, '/tmp/buchel-experiment/runtime.py')
            ds.cp_dir_to(payload, '/tmp/buchel-experiment/payload')
            try:
                ds.exec('/opt/venv/bin/python', '/tmp/buchel-experiment/runtime.py', 'prepare',
                        '--tool', tool, '--target', self.workdir, '--payload', '/tmp/buchel-experiment/payload',
                        '--output', '/tmp/buchel-experiment/manifest.json', env=self.env)
                ds.cp_from('/tmp/buchel-experiment/manifest.json', work / 'container_changes.json')
                ds.cp_from('/tmp/buchel-experiment/manifest.diff', work / 'container_changes.diff')
                changes = json.loads((work / 'container_changes.json').read_text())
                for record in changes['files']:
                    if record.get('asset') and record['action'] != 'unchanged':
                        dst = self.workdir + '/' + record['path']
                        ds.exec('mkdir', '-p', str(Path(dst).parent))
                        ds.cp_to(target / record['path'], dst)
                ds.exec('/opt/venv/bin/python', '/tmp/buchel-experiment/runtime.py', 'verify',
                        '--tool', tool, '--target', self.workdir,
                        '--output', '/tmp/buchel-experiment/manifest.json', env=self.env)
                ds.cp_from('/tmp/buchel-experiment/manifest.json', work / 'container_changes.json')
            except Exception:
                (work / 'preparation_failed.txt').write_text('Preparation failed; container was not used for extraction.\n')
                raise

        def build_command(self, in_cn, out_cn):
            python = BUCHEL_ATTACKG_PYTHON if prepared_image else '/opt/venv/bin/python'
            runtime = IMAGE_RUNTIME if prepared_image else '/tmp/buchel-experiment/runtime.py'
            return [python, runtime, 'extract',
                    '--tool', tool, '--target', self.workdir, '--input', in_cn, '--output', out_cn]

    return BuchelToolAdapter(image=image or ('ttp-workbench:' + tool), engine=engine,
                            tmp_root=input_dir, use_gpus=tool == 'ladder', soft_fail=False, verbose=verbose,
                            env=_thread_env())
