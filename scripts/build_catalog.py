"""Generate a public AMI catalog from an explicitly maintained repository registry."""
import argparse
import base64
from datetime import datetime, timezone
from email.utils import format_datetime
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import urllib.request
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlparse
import zipfile
import xml.etree.ElementTree as ET


def validate_references(references):
    """Validate exact assembly dependencies while preserving unknown metadata as null."""
    if not isinstance(references, list):
        raise ValueError('references must be a list')
    for reference in references:
        if (not isinstance(reference, dict) or set(reference) != {'name', 'version'}
                or not isinstance(reference['name'], str)
                or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\.dll', reference['name'], re.IGNORECASE)
                or not isinstance(reference['version'], str)
                or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+', reference['version'])
                or any(int(part) > 65535 for part in reference['version'].split('.'))):
            raise ValueError('references require a DLL basename and four-part assembly version')
    return references


METADATA_MAX_BYTES = 64 * 1024


def fetch_compatibility_metadata(url):
    """Download a small public sidecar without forwarding GitHub credentials."""
    request = urllib.request.Request(url, headers={'User-Agent': 'Gurux-AMI-Catalog',
                                                  'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(METADATA_MAX_BYTES + 1)
    if len(data) > METADATA_MAX_BYTES:
        raise ValueError('Compatibility metadata exceeds size limit')
    return json.loads(data.decode('utf-8-sig'))


def compatibility_references(source, release, version, fetch_metadata):
    """Require the published sidecar belonging to this exact module release."""
    name = f'{source["id"]}-{version}.compatibility.json'
    matches = [asset for asset in release.get('assets', [])
               if asset.get('name') == name and asset.get('state') == 'uploaded']
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one uploaded compatibility sidecar: {name}')
    url = matches[0].get('browser_download_url')
    uri = urlparse(url) if isinstance(url, str) else None
    expected_path = f'/{source["repository"]}/releases/download/{release["tag_name"]}/{name}'
    if (uri is None or uri.scheme != 'https' or uri.netloc != 'github.com'
            or uri.query or uri.fragment or unquote(uri.path) != expected_path):
        raise ValueError(f'Invalid compatibility metadata download URL: {name}')
    metadata = fetch_metadata(url)
    if (not isinstance(metadata, dict) or type(metadata.get('schemaVersion')) is not int
            or metadata['schemaVersion'] != 1 or metadata.get('id') != source['id']
            or metadata.get('version') != version or metadata.get('targetFramework') != 'net10.0'):
        raise ValueError(f'Invalid compatibility metadata identity or framework: {name}')
    references = validate_references(metadata.get('references'))
    if not references:
        raise ValueError(f'Compatibility references must not be empty: {name}')
    seen = set()
    for reference in references:
        key = reference['name'].lower()
        if key in seen:
            raise ValueError(f'Duplicate compatibility reference: {reference["name"]}')
        seen.add(key)
    return references


def github_json(url):
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'Gurux-AMI-Catalog'}
    token = os.environ.get('GH_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


def fetch_release_commits(repository, previous_tag, tag):
    """Read all commit messages in a release range, or tag history for the first release."""
    messages = []
    page = 1
    while True:
        if previous_tag is None:
            url = f'https://api.github.com/repos/{repository}/commits?sha={quote(tag, safe="")}&per_page=100&page={page}'
        else:
            comparison = quote(previous_tag, safe='') + '...' + quote(tag, safe='')
            url = f'https://api.github.com/repos/{repository}/compare/{comparison}?per_page=100&page={page}'
        response = github_json(url)
        batch = response.get('commits') if previous_tag is not None and isinstance(response, dict) else response
        if not isinstance(batch, list):
            raise ValueError(f'Invalid commits response for {repository} {tag}')
        for entry in batch:
            commit = entry.get('commit') if isinstance(entry, dict) else None
            message = commit.get('message') if isinstance(commit, dict) else None
            if not isinstance(message, str):
                raise ValueError(f'Invalid commit message for {repository} {tag}')
            messages.append(message)
        if len(batch) < 100:
            return messages
        page += 1


def fetch_releases(repository):
    releases = []
    page = 1
    while True:
        url = f'https://api.github.com/repos/{repository}/releases?per_page=100&page={page}'
        batch = github_json(url)
        if not isinstance(batch, list):
            raise ValueError(f'Invalid releases response for {repository}')
        releases.extend(batch)
        if len(batch) < 100:
            return releases
        page += 1


def fetch_description(repository, metadata_path='catalog-info.json'):
    """Read product metadata on the default branch, then repository About metadata."""
    if (not isinstance(metadata_path, str) or not metadata_path.strip()
            or metadata_path.startswith('/') or '\\' in metadata_path
            or any(part in ('', '.', '..') for part in metadata_path.split('/'))):
        raise ValueError('metadataPath must be a relative repository file path')
    try:
        response = github_json(f'https://api.github.com/repos/{repository}/contents/{quote(metadata_path, safe="/")}')
    except HTTPError as error:
        if error.code != 404:
            raise
    else:
        if not isinstance(response, dict) or response.get('encoding') != 'base64':
            raise ValueError(f'Invalid metadata file response for {repository}/{metadata_path}')
        metadata = json.loads(base64.b64decode(response['content']).decode('utf-8-sig'))
        if not isinstance(metadata, dict):
            raise ValueError(f'Metadata must be an object: {repository}/{metadata_path}')
        description = metadata.get('description')
        if isinstance(description, str) and description.strip():
            return description
    try:
        description = github_json(f'https://api.github.com/repos/{repository}').get('description')
    except HTTPError as error:
        if error.code != 404:
            raise
        return ''
    return description if isinstance(description, str) and description.strip() else ''


def local_releases(source, source_directory, output_directory, base_url):
    """Adapt local ZIPs to the release format shared with GitHub sources."""
    uri = urlparse(base_url or '')
    if uri.scheme not in ('http', 'https') or not uri.netloc or uri.username or uri.password or uri.query or uri.fragment:
        raise ValueError('Local sources require an absolute HTTP(S) --base-url')
    if output_directory is None:
        raise ValueError('Local sources require an output directory')
    releases = source.get('releases')
    if not isinstance(releases, list) or not releases:
        raise ValueError(f'Local releases are required for {source["id"]}')
    result = []
    versions = set()
    for release in releases:
        version = release.get('version')
        package_name = release.get('package')
        if not isinstance(version, str) or not version.strip() or version in versions:
            raise ValueError('Local release versions must be nonempty and unique')
        versions.add(version)
        if not isinstance(package_name, str) or not package_name.strip():
            raise ValueError(f'Local package is required for {source["id"]} {version}')
        package = (Path(source_directory) / package_name).resolve()
        if not package.is_file():
            raise FileNotFoundError(f'Local package not found: {package}')
        if package.suffix.lower() != '.zip' or not zipfile.is_zipfile(package):
            raise ValueError(f'Local package must be a ZIP: {package}')
        with package.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else None
        if digest is None:  # Python 3.10 compatibility.
            checksum = hashlib.sha256()
            with package.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    checksum.update(chunk)
            digest = checksum.hexdigest()
        relative = Path('packages') / digest / package.name
        destination = Path(output_directory) / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.resolve() != package:
            shutil.copyfile(package, destination)
        published = release.get('publishedAt') or datetime.fromtimestamp(package.stat().st_mtime, timezone.utc).isoformat()
        timestamp = datetime.fromisoformat(published.replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            raise ValueError('Local publishedAt must include a timezone')
        prerelease = release.get('isPrerelease', '-' in version)
        if not isinstance(prerelease, bool):
            raise ValueError('Local isPrerelease must be a boolean')
        result.append({'tag_name': release.get('tagName', 'v' + version),
                       **({'references': validate_references(release['references'])} if 'references' in release else {}),
                       'published_at': published, 'prerelease': prerelease,
                       'html_url': release.get('releaseUrl'), 'body': release.get('releaseNotes'),
                       'assets': [{'name': package.name,
                                   'browser_download_url': base_url.rstrip('/') + '/' + quote(relative.as_posix()),
                                   'size': package.stat().st_size, 'digest': 'sha256:' + digest}]})
    return result


def build_catalog(sources, fetch, count=10, include_prereleases=True, *,
                  source_directory='.', output_directory=None, base_url=None, fetch_commits=None,
                  fetch_metadata=None):
    if not isinstance(count, int) or count < 1:
        raise ValueError('count must be positive')
    entries = sources.get('items')
    if not isinstance(entries, list) or not entries:
        raise ValueError('Configure at least one product in catalog-sources.json')
    seen = set()
    items = []
    cache = {}
    commit_cache = {}
    description_cache = {}
    fetch_commits = fetch_commits or fetch_release_commits
    fetch_metadata = fetch_metadata or fetch_compatibility_metadata
    def description_for(source):
        description = source.get('description')
        if isinstance(description, str) and description.strip():
            return description
        if source.get('source', 'github') == 'local':
            return ''
        key = (source['repository'], source.get('metadataPath', 'catalog-info.json'))
        if key not in description_cache:
            description_cache[key] = fetch_description(*key)
        return description_cache[key]

    for source in entries:
        identifier = source.get('id')
        repository = source.get('repository', '')
        pattern = source.get('assetPattern')
        provider = source.get('source', 'github')
        tag_prefix = source.get('tagPrefix')
        require_compatibility = source.get('compatibilityMetadata', False)
        if (not isinstance(require_compatibility, bool)
                or (require_compatibility and (provider != 'github' or source.get('type') != 'module'))):
            raise ValueError('compatibilityMetadata requires a GitHub module source and boolean value')
        if 'tagPrefix' in source and (not isinstance(tag_prefix, str) or not tag_prefix.strip()):
            raise ValueError(f'tagPrefix must be a nonempty string for {identifier}')
        if provider not in ('github', 'local'):
            raise ValueError(f'Invalid source for {identifier}')
        if not isinstance(identifier, str) or not identifier.strip() or identifier in seen:
            raise ValueError('Item ids must be nonempty and unique')
        if source.get('type') not in ('module', 'application', 'agent', 'configuration'):
            raise ValueError(f'Invalid type for {identifier}')
        if (provider == 'github' or repository) and not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError(f'Invalid owner/name repository for {identifier}')
        if source['type'] == 'configuration':
            if provider != 'github':
                raise ValueError('Local sources support modules, applications and agents only')
            index_url = source.get('indexUrl', '')
            uri = urlparse(index_url)
            loopback = uri.hostname in ('localhost', '127.0.0.1', '::1')
            if (uri.scheme != 'https' and not (uri.scheme == 'http' and loopback)) or not uri.netloc or uri.username or uri.password:
                raise ValueError(f'indexUrl must be an absolute HTTPS or HTTP loopback URL for {identifier}')
            asset_name = Path(uri.path).name
            if not asset_name:
                raise ValueError(f'indexUrl must identify an index file for {identifier}')
            seen.add(identifier)
            items.append({'id': identifier, 'type': 'configuration', 'name': source.get('name', identifier),
                          'description': description_for(source), 'repository': repository,
                          'releases': [{'version': '1.0.0', 'tagName': 'index', 'isPrerelease': False,
                                        'publishedAt': datetime.now(timezone.utc).isoformat(),
                                        'assets': [{'name': asset_name, 'downloadUrl': index_url, 'size': 0}]}]})
            continue
        if provider == 'local':
            pattern = source.get('assetPattern', '*.zip')
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f'assetPattern is required for {identifier}')
        seen.add(identifier)
        if provider == 'local':
            source_releases = local_releases(source, source_directory, output_directory, base_url)
        else:
            if repository not in cache:
                cache[repository] = fetch(repository)
            source_releases = cache[repository]
        published_releases = [r for r in source_releases if not r.get('draft')]
        if tag_prefix is not None:
            published_releases = [r for r in published_releases
                                  if r['tag_name'].startswith(tag_prefix)
                                  and len(r['tag_name']) > len(tag_prefix)]
        # Baselines must include releases outside --count, and stable releases span prereleases.
        ordered = sorted(published_releases, key=lambda r: datetime.fromisoformat(r['published_at'].replace('Z', '+00:00')))
        previous_tags = {}
        previous = previous_stable = None
        for release in ordered:
            previous_tags[release['tag_name']] = previous if release.get('prerelease') else previous_stable
            previous = release['tag_name']
            if not release.get('prerelease'):
                previous_stable = previous
        releases = [r for r in published_releases
                    if include_prereleases or not r.get('prerelease')]
        releases.sort(key=lambda r: datetime.fromisoformat(r['published_at'].replace('Z', '+00:00')),
                      reverse=True)
        versions = []
        for release in releases[:count]:
            tag = release['tag_name']
            security = False
            if tag_prefix is not None and provider == 'github':
                # Prefixed module releases contain a path-filtered commit changelog.
                # Reading the whole monorepo range would flag unrelated modules.
                security = any(line.startswith('- fix(security):')
                               for line in (release.get('body') or '').splitlines())
            elif repository:
                key = (repository, previous_tags[tag], tag)
                if key not in commit_cache:
                    commit_cache[key] = any(message.startswith('fix(security):')
                                            for message in fetch_commits(*key))
                security = commit_cache[key]
            assets = []
            for asset in release.get('assets', []):
                if asset.get('state', None if require_compatibility else 'uploaded') != 'uploaded':
                    continue
                if fnmatch.fnmatchcase(asset['name'].lower(), pattern.lower()):
                    assets.append({'name': asset['name'], 'downloadUrl': asset['browser_download_url'],
                                   'size': asset['size'], 'digest': asset.get('digest')})
            version = tag[len(tag_prefix):] if tag_prefix is not None else (tag[1:] if tag.startswith(('v', 'V')) else tag)
            references = ({'references': validate_references(release['references'])}
                          if 'references' in release else {})
            if require_compatibility and any(asset['name'].lower().endswith('.zip') for asset in assets):
                references = {'references': compatibility_references(source, release, version, fetch_metadata)}
            versions.append({'version': version,
                             'priority': 'Critical' if security else 'Normal', 'isSecurityUpdate': security,
                             **references,
                             'tagName': tag, 'isPrerelease': bool(release.get('prerelease')),
                             'publishedAt': release['published_at'], 'releaseUrl': release.get('html_url'),
                             'releaseNotes': release.get('body'), 'assets': assets})
        items.append({'id': identifier, 'type': source['type'], 'name': source.get('name', identifier),
                      'description': description_for(source), 'repository': repository,
                      'releases': versions})
    return {'schemaVersion': 1, 'generatedAt': datetime.now(timezone.utc).isoformat(), 'items': items}


def build_rss(catalog, base_url=None):
    """Describe the ten latest releases using the catalog's JSON descriptions."""
    site = (base_url or 'https://gurux.github.io/Gurux.AMI.Catalog').rstrip('/') + '/'
    rss = ET.Element('rss', version='2.0')
    channel = ET.SubElement(rss, 'channel')
    ET.SubElement(channel, 'title').text = 'Catalog updates'
    ET.SubElement(channel, 'link').text = site
    ET.SubElement(channel, 'description').text = 'The latest Gurux AMI catalog releases.'

    def published(release):
        value = release.get('publishedAt')
        if not value:
            return datetime.min.replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)

    def text(value):
        # XML 1.0 cannot represent these characters, even as escaped text.
        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]', '', value or '')

    # Configuration index pointers are generated metadata, not versioned changes.
    releases = [(item, release) for item in catalog.get('items', []) for release in item.get('releases', [])
                if not (item.get('type') == 'configuration' and release.get('tagName') == 'index')]
    releases.sort(key=lambda pair: (pair[0]['id'], pair[1]['version']))
    releases.sort(key=lambda pair: published(pair[1]), reverse=True)
    for product, release in releases[:10]:
        item = ET.SubElement(channel, 'item')
        ET.SubElement(item, 'title').text = text(f'{product.get("name") or product["id"]} {release["version"]}')
        descriptions = list(dict.fromkeys(value.strip() for value in
                            (product.get('description'), release.get('releaseNotes'))
                            if isinstance(value, str) and value.strip()))
        ET.SubElement(item, 'description').text = text('\n\n'.join(descriptions))
        ET.SubElement(item, 'guid', isPermaLink='false').text = f'urn:gurux:catalog:{quote(product["id"], safe="")}:{quote(release["version"], safe="")}'
        if release.get('publishedAt'):
            ET.SubElement(item, 'pubDate').text = format_datetime(published(release), usegmt=True)
        link = release.get('releaseUrl')
        parsed = urlparse(link) if isinstance(link, str) else None
        if parsed and parsed.scheme in ('https', 'http') and parsed.netloc and not parsed.username and not parsed.password:
            ET.SubElement(item, 'link').text = link
    return ET.tostring(rss, encoding='utf-8', xml_declaration=True).decode('utf-8') + '\n'


def generate_catalog(sources_path, output, count=10, include_prereleases=True, base_url=None):
    """Generate packages and atomically replace the catalog after a successful build."""
    sources_path = Path(sources_path).resolve()
    output = Path(output)
    sources = json.loads(sources_path.read_text(encoding='utf-8'))
    catalog = build_catalog(sources, fetch_releases, count, include_prereleases,
                            source_directory=sources_path.parent,
                            output_directory=output.parent, base_url=base_url)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Pages deploys only the output directory. Include the index and every
    # package it references, rather than publishing a dangling catalog URL.
    localization = sources_path.parent / 'localization'
    if localization.is_dir() and localization.resolve() != (output.parent / 'localization').resolve():
        shutil.copytree(localization, output.parent / 'localization', dirs_exist_ok=True)
    scripts = sources_path.parent / 'scripts'
    published_scripts = output.parent / 'scripts'
    if (scripts / 'script-index.json').is_file() and scripts.resolve() != published_scripts.resolve():
        published_scripts.mkdir(parents=True, exist_ok=True)
        if (scripts / 'files').is_dir():
            shutil.copytree(scripts / 'files', published_scripts / 'files', dirs_exist_ok=True)
        shutil.copy2(scripts / 'script-index.json', published_scripts / 'script-index.json')
    temporary = output.with_suffix('.tmp')
    feed = output.with_name('rss.xml')
    temporary_feed = feed.with_suffix('.tmp')
    original_feed = feed.read_bytes() if feed.exists() else None
    # Build both representations before publishing either one.
    rss = build_rss(catalog, base_url)
    temporary.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary_feed.write_text(rss, encoding='utf-8')
    feed_replaced = False
    try:
        temporary_feed.replace(feed)
        feed_replaced = True
        temporary.replace(output)
    except BaseException:
        if feed_replaced:
            if original_feed is None:
                feed.unlink(missing_ok=True)
            else:
                temporary_feed.write_bytes(original_feed)
                temporary_feed.replace(feed)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        temporary_feed.unlink(missing_ok=True)
    return catalog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sources', default='catalog-sources.json')
    parser.add_argument('--output', default='_site/catalog.json')
    parser.add_argument('--count', type=int, default=10)
    parser.add_argument('--stable-only', action='store_true')
    parser.add_argument('--base-url', help='Public HTTP(S) URL of the output directory for local packages')
    args = parser.parse_args()
    output = Path(args.output)
    catalog = generate_catalog(args.sources, output, args.count, not args.stable_only, args.base_url)
    print(f'Generated {output}: {len(catalog["items"])} products')


if __name__ == '__main__':
    main()
