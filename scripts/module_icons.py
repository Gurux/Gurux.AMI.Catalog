"""Fetch release-tagged module icons without credentials or archive extraction."""
import hashlib
from pathlib import PurePosixPath
import re
import urllib.request
from urllib.parse import quote, unquote, urlparse
import xml.etree.ElementTree as ET

MAX_ICON_BYTES = 1024 * 1024
EXTENSIONS = {'.svg', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.ico'}


def fetch_icon(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Gurux-AMI-Catalog'})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(MAX_ICON_BYTES + 1)
    if len(data) > MAX_ICON_BYTES:
        raise ValueError('Module icon exceeds 1 MiB')
    return data


def prepare_icons(sources, catalog, download=None, base_url=None):
    download = download or fetch_icon
    prepared = {}
    items = {item['id']: item for item in catalog['items']}
    for source in sources['items']:
        if 'iconPath' not in source:
            continue
        identifier, path = source['id'], source['iconPath']
        if (source.get('source', 'github') != 'github' or source.get('type') != 'module'
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', identifier)
                or not isinstance(path, str) or not path or '\\' in path or ':' in path
                or path.startswith('/') or any(p in ('', '.', '..') for p in path.split('/'))
                or PurePosixPath(path).suffix.lower() not in EXTENSIONS):
            raise ValueError(f'Invalid module iconPath for {identifier}')
        repository = source.get('repository', '')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('Invalid icon source repository')
        relative = f'assets/{identifier}/{PurePosixPath(path).name}'
        public_url = source.get('iconUrl', '')
        parsed = urlparse(public_url)
        if (parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password
                or parsed.query or parsed.fragment or not unquote(parsed.path).endswith('/' + relative)):
            raise ValueError(f'iconUrl must end with /{relative}')
        item = items[identifier]
        releases = item.get('releases', [])
        release = next((r for r in releases if not r.get('isPrerelease')), releases[0] if releases else None)
        if release is None:
            item.pop('iconUrl', None)
            continue
        tag = release['tagName']
        if not isinstance(tag, str) or not tag or any(p in ('', '.', '..') for p in tag.split('/')):
            raise ValueError('Invalid module icon release tag')
        url = f'https://raw.githubusercontent.com/{repository}/refs/tags/{quote(tag, safe="/")}/{quote(path, safe="/")}'
        data = download(url)
        if not isinstance(data, bytes) or not data or len(data) > MAX_ICON_BYTES:
            raise ValueError(f'Empty or oversized module icon for {identifier}')
        if PurePosixPath(path).suffix.lower() == '.svg':
            if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                raise ValueError('Module SVG must not contain a DTD or entities')
            try:
                root = ET.fromstring(data)
            except ET.ParseError as error:
                raise ValueError('Invalid module SVG') from error
            if root.tag not in ('svg', '{http://www.w3.org/2000/svg}svg'):
                raise ValueError('Expected an SVG root element')
        prepared[relative] = data
        url = base_url.rstrip('/') + '/' + quote(relative, safe='/') if base_url else public_url
        item['iconUrl'] = url + '?v=' + hashlib.sha256(data).hexdigest()[:16]
    return prepared
