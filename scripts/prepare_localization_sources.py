"""Resolve explicitly registered source directories; optionally checkout their GitHub repositories.

Writes only a temporary resolved configuration, never discovers arbitrary JSON files.
The C# publisher evaluates project metadata and validates translations afterwards.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile


def prepare(configuration, output, checkout_root):
    configuration, output, checkout_root = map(lambda p: Path(p).resolve(), (configuration, output, checkout_root))
    data = json.loads(configuration.read_text(encoding='utf-8'))
    repositories = {}
    for source in data['sources']:
        directory = configuration.parent
        repository = source.pop('repository', None)
        revision = source.pop('ref', 'main')
        if repository:
            if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or not revision or revision.startswith('-'):
                raise ValueError('Expected an explicit GitHub owner/repository and ref')
            identity = repository, revision
            if identity not in repositories:
                checkout_root.mkdir(parents=True, exist_ok=True)
                directory = Path(tempfile.mkdtemp(prefix='localization-source-', dir=checkout_root))
                subprocess.run(['git', 'clone', '--no-checkout', '--filter=blob:none',
                                f'https://github.com/{repository}.git', str(directory)], check=True)
                subprocess.run(['git', '-C', str(directory), 'checkout', '--detach', revision], check=True)
                repositories[identity] = directory
            directory = repositories[identity]
        for field in ('directory', 'project', 'metadataPackage'):
            if field in source and source[field] is not None:
                resolved = (directory / source[field]).resolve()
                if repository and not resolved.is_relative_to(directory):
                    raise ValueError('Registered remote source paths must stay inside their checkout')
                source[field] = str(resolved)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', default='localization-sources.json')
    parser.add_argument('--output', required=True)
    parser.add_argument('--checkout-root', required=True)
    args = parser.parse_args()
    prepare(args.sources, args.output, args.checkout_root)
