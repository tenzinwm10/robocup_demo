"""Create a portable source archive with generated scenes and reproducible hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
UPSTREAM = '2d56d3622ac6dbfdbda96d27a2042eb543f199ae'

def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): result.update(chunk)
    return result.hexdigest()

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True)
    args = parser.parse_args(); target = Path(args.output); target.mkdir(parents=True, exist_ok=True)
    previous = json.loads((target/'manifest.json').read_text()) if (target/'manifest.json').exists() else {}
    listed = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    paths = {ROOT/path for path in listed if path and (ROOT/path).is_file()}
    paths.add(ROOT/'.dockerignore')
    for path in HERE.rglob('*'):
        if path.is_file() and not {'logs', '__pycache__', 'models', 'handoff'} & set(path.relative_to(HERE).parts):
            if path.suffix != '.pyc': paths.add(path)
    with zipfile.ZipFile(target/'source.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for path in sorted(paths): archive.write(path, path.relative_to(ROOT).as_posix())
    files = ('src/brain/src/robot_client.cpp', 'src/brain/src/brain_communication.cpp',
             'src/brain/behavior_trees/game.xml', 'src/brain/include/team_communication_protocol.h',
             'src/vision/model/T2_0804_digua.engine', 'src/vision/model/best_seg_orin_10.3.engine')
    evidence = {}
    for name in files:
        # Compare Git blob IDs; this proves the critical protocol/model files were not edited.
        original = subprocess.check_output(['git', 'rev-parse', f'{UPSTREAM}:{name}'], cwd=ROOT).decode().strip()
        current = subprocess.check_output(['git', 'hash-object', name], cwd=ROOT).decode().strip()
        if original != current: raise RuntimeError(f'Upstream protocol/model changed: {name}')
        evidence[name] = {'sha256': digest(ROOT/name), 'upstream_blob': original, 'unchanged': True}
    images = json.loads(subprocess.check_output(['docker', 'image', 'inspect',
        'robocup-t2-studio:cpu-ready', 'robocup-t2-studio:gpu-ready', 'robocup-t2-studio:renderer']))
    manifest = {'repository': 'https://github.com/BoosterRobotics/robocup_demo',
                'branch': 'sandbox/support_T2', 'upstream_commit': UPSTREAM,
                'source_zip_sha256': digest(target/'source.zip'), 'unchanged_upstream_files': evidence,
                'images': [{'tags': image['RepoTags'], 'id': image['Id'], 'size_bytes': image['Size']} for image in images]}
    if [image['id'] for image in previous.get('images', [])] == [image['id'] for image in manifest['images']]:
        for key in ('docker_archive_sha256', 'docker_archive_bytes'):
            if key in previous: manifest[key] = previous[key]
    (target/'manifest.json').write_text(json.dumps(manifest, indent=2))
    (target/'START_HERE.md').write_text('''# T2 brain / T1 body workstation handoff

1. Extract `source.zip` into a directory named `source`.
2. Run `docker load -i docker-images.tar` on the Linux workstation.
3. Follow `source/simulation/studio_t1/WORKSTATION.md`.
4. Read `validation.json`: passed isolated checks and workstation-only checks are distinguished.

The bundle uses the exact support_T2 models and protocol code. NVIDIA model-load success,
T2-only motion execution on the T1 controller, and full-match performance are not claimed.
The scene camera renderer only reads Studio snapshots; Studio owns physics and motion.
''')
    print(json.dumps({'directory': str(target), 'source_bytes': (target/'source.zip').stat().st_size,
                      'protocol_and_models_match_upstream': True}, indent=2))

if __name__ == '__main__': main()
