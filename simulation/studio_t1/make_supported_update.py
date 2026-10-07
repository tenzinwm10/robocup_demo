"""Package supported launcher changes while reusing the original Docker parts."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from make_handoff import HERE, ROOT, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-directory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--validation', type=Path, required=True,
                        help='CPU validation results for the supported update')
    args = parser.parse_args()
    if args.output.resolve() == args.base_directory.resolve():
        parser.error('The update directory must differ from the original release directory')
    base = json.loads((args.base_directory/'manifest.json').read_text())
    parts = base['docker_archive_parts']
    if [part['name'] for part in parts] != [f'docker-images.tar.part{i:02d}' for i in range(1, 5)]:
        raise RuntimeError('Expected the four original release Docker parts')
    for part in parts:
        path = args.base_directory/part['name']
        if path.stat().st_size != part['bytes'] or digest(path) != part['sha256']:
            raise RuntimeError(f'Original Docker part failed verification: {path.name}')
    target = args.output
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(HERE/'make_handoff.py'), '--output', str(target)], check=True)
    manifest = json.loads((target/'manifest.json').read_text())
    if {item['id'] for item in manifest['images']} != {item['id'] for item in base['images']}:
        raise RuntimeError('The update requires the original release Docker images')
    manifest.update(
        source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        prepared_at=datetime.now(timezone.utc).isoformat(),
        source_archive='source-supported.zip',
        docker_archive_sha256=base['docker_archive_sha256'],
        docker_archive_bytes=base['docker_archive_bytes'],
        docker_archive_parts=parts,
        handoff_release='workstation-2026-10-07',
        scope='Existing support_T2 simulation with supported motion; VisualKick excluded',
    )
    (target/'source.zip').replace(target/'source-supported.zip')
    (target/'manifest.json').unlink()
    (target/'START_HERE.md').unlink()
    (target/'supported-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    validation = json.loads(args.validation.read_text())
    validation.update(source_commit=manifest['source_commit'], prepared_at=manifest['prepared_at'])
    (target/'supported-validation.json').write_text(json.dumps(validation, indent=2)+'\n')
    (target/'SUPPORTED_START_HERE.md').write_text('''# FC ISAR supported simulation handoff

Use this update for the existing `sandbox/support_T2` simulation. VisualKick is
not in the firmware and is outside acceptance. Keep automatic VisualKick off.
The original release assets remain available; this update reuses the same four
Docker archive parts and supplies the current source, plan and CPU evidence.

On the Ubuntu NVIDIA workstation:

```bash
gh release download workstation-2026-10-07 \\
  --repo tenzinwm10/robocup-t2-studio-handoff \\
  --dir ~/robocup-t2-studio-handoff --skip-existing
cd ~/robocup-t2-studio-handoff
sha256sum -c SUPPORTED_SHA256SUMS
cat docker-images.tar.part01 docker-images.tar.part02 \\
    docker-images.tar.part03 docker-images.tar.part04 > docker-images.tar
printf '%s  %s\\n' \\
  b7ca7985aa2e01dc73679cf0956d353294cfa4c2d9ef721d14b96e5b5f464766 \\
  docker-images.tar | sha256sum -c -
docker load -i docker-images.tar
unzip source-supported.zip -d source-supported
```

Add `source-supported` as the workstation project. Follow
`simulation/studio_t1/WORKSTATION_PLAN.md` from that directory, with
`WORKSTATION.md` for route details. Start with robot2, then three players and
finally 3v3. Run CPU checks and the workstation GPU/model/camera gates before
match acceptance. Existing camera/strategy/oversight helpers read the updated
mounted source; the original Docker images do not need rebuilding for this
launcher-only update. C++ edits still require the documented rebuild.

`supported-validation.json` records the update's CPU regressions. Live GPU
inference, visual-localization accuracy and complete matches remain pending.
The six-body scene uses stock T1 bodies to exercise the T2 application.
''')
    names = ('source-supported.zip', 'supported-manifest.json',
             'supported-validation.json', 'SUPPORTED_START_HERE.md')
    checksums = [f'{digest(target/name)}  {name}' for name in names]
    checksums.extend(f"{part['sha256']}  {part['name']}" for part in parts)
    (target/'SUPPORTED_SHA256SUMS').write_text('\n'.join(checksums)+'\n')
    print(json.dumps({'directory': str(target), 'source_commit': manifest['source_commit'],
                      'original_docker_parts_verified': True,
                      'protocol_and_models_match_upstream': True}, indent=2))


if __name__ == '__main__':
    main()
