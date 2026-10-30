"""Import only published, verified content Releases from an explicit repository allowlist."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import quote


def run(arguments):
    return subprocess.check_output(list(map(str, arguments)), text=True, encoding='utf-8')


def import_releases(configuration, root, validator, repository=None, tag=None, runner=run):
    repositories = json.loads(Path(configuration).read_text(encoding='utf-8'))['repositories']
    if not isinstance(repositories, list) or any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value) for value in repositories):
        raise ValueError('Content sources require an explicit GitHub repository allowlist')
    if repository and repository not in repositories:
        raise ValueError('Dispatch repository is not registered for content publication')
    if tag and not repository:
        raise ValueError('A tag requires its registered repository')
    for source in [repository] if repository else repositories:
        if tag:
            releases = [json.loads(runner(['gh', 'api', f'repos/{source}/releases/tags/{quote(tag, safe="")}']))]
        else:
            pages = json.loads(runner(['gh', 'api', f'repos/{source}/releases', '--paginate', '--slurp']))
            releases = [release for page in pages for release in page]
        for release in releases:
            release_tag = release['tag_name']
            if not release_tag.startswith(('localization/', 'resources/')):
                continue
            if tag and release_tag != tag:
                raise ValueError('Release does not match requested tag')
            if release.get('draft') or release.get('prerelease'):
                if tag:
                    raise ValueError('Catalog accepts only successfully published stable content Releases')
                continue
            with tempfile.TemporaryDirectory(prefix='gurux-content-import-') as temporary:
                temporary = Path(temporary)
                names = [asset['name'] for asset in release.get('assets', []) if asset.get('state') == 'uploaded']
                if names.count('release-manifest.json') != 1:
                    raise ValueError('Published content Release is missing its manifest')
                runner(['gh', 'release', 'download', release_tag, '--repo', source, '--pattern', 'release-manifest.json', '--dir', temporary])
                manifest_path = temporary / 'release-manifest.json'
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                if manifest['tag'] != release_tag or manifest['kind'] != release_tag.split('/')[0]:
                    raise ValueError('Release and manifest tag/kind differ')
                # The annotated tag, rather than an editable Release asset, reserves the immutable identity/hash.
                reference = json.loads(runner(['gh', 'api', f'repos/{source}/git/ref/tags/{quote(release_tag, safe="")}']))
                if reference['object']['type'] != 'tag':
                    raise ValueError('Content releases require annotated tags')
                annotation = json.loads(runner(['gh', 'api', f'repos/{source}/git/tags/{reference["object"]["sha"]}']))
                if json.loads(annotation['message']) != manifest:
                    raise ValueError('Release manifest differs from immutable annotated tag')
                runner(['dotnet', validator, 'content', 'verify-manifest', '--manifest', manifest_path])
                name = manifest['package']
                if not isinstance(name, str) or '/' in name or '\\' in name or name in ('.', '..') or name.startswith('-') or names.count(name) != 1:
                    raise ValueError('Invalid or missing Release package asset')
                runner(['gh', 'release', 'download', release_tag, '--repo', source, '--pattern', name, '--dir', temporary])
                # C# validates exact bytes, manifest schema, tag, owner/culture, resource paths and content hashes.
                runner(['dotnet', validator, 'content', 'import', '--manifest', manifest_path, '--package', temporary / name, '--catalog-root', Path(root).resolve()])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', default='content-release-sources.json')
    parser.add_argument('--catalog-root', default='.')
    parser.add_argument('--validator', default=os.environ.get('GURUX_LOCALIZATION_VALIDATOR'), required=not bool(os.environ.get('GURUX_LOCALIZATION_VALIDATOR')))
    parser.add_argument('--repository')
    parser.add_argument('--tag')
    args = parser.parse_args()
    import_releases(args.sources, args.catalog_root, args.validator, args.repository, args.tag)
