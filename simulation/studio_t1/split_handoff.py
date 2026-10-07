"""Split a Docker archive into GitHub release assets and hash every part."""
import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('directory', type=Path)
args = parser.parse_args()
directory = args.directory
archive = directory/'docker-images.tar'
manifest_path = directory/'manifest.json'
manifest = json.loads(manifest_path.read_text())
part_size = 1_800_000_000  # Below GitHub's per-release-asset size limit.
whole_hash = hashlib.sha256()
parts = []
with archive.open('rb') as source:
    index = 1
    while source.tell() < archive.stat().st_size:
        name = f'docker-images.tar.part{index:02d}'
        path = directory/name
        part_hash = hashlib.sha256()
        written = 0
        with path.open('wb') as output:
            while written < part_size:
                chunk = source.read(min(8*1024*1024, part_size-written))
                if not chunk:
                    break
                output.write(chunk)
                whole_hash.update(chunk)
                part_hash.update(chunk)
                written += len(chunk)
        parts.append({'name': name, 'bytes': written, 'sha256': part_hash.hexdigest()})
        print(f'Created {name}: {written} bytes', flush=True)
        index += 1
if whole_hash.hexdigest() != manifest['docker_archive_sha256']:
    raise RuntimeError('Docker archive differs from the validated handoff manifest')
manifest['docker_archive_parts'] = parts
manifest['handoff_repository'] = 'https://github.com/tenzinwm10/robocup-t2-studio-handoff'
manifest['handoff_release'] = 'workstation-2026-10-07'
manifest_path.write_text(json.dumps(manifest, indent=2))
checksums = []
for name in ('source.zip', 'manifest.json', 'validation.json', 'START_HERE.md'):
    digest = hashlib.sha256((directory/name).read_bytes()).hexdigest()
    checksums.append(f'{digest}  {name}')
checksums.extend(f"{part['sha256']}  {part['name']}" for part in parts)
(directory/'SHA256SUMS').write_text('\n'.join(checksums)+'\n')
print('Archive and part hashes verified.', flush=True)
