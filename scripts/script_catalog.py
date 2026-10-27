"""Immutable standalone C# source publication; never imports or executes script code."""
import base64
import binascii
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

MAX_SOURCE = 4 * 1024 * 1024
VERSION = r'[0-9]+(?:\.[0-9]+){1,3}(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?(?:\+[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?'

def version_key(value):
    main, _, pre = value.split('+', 1)[0].partition('-')
    numbers = [int(n) for n in main.split('.')]
    return tuple(numbers + [0] * (4 - len(numbers))), tuple(str(int(x)) if x.isdigit() else x.lower() for x in pre.split('.')) if pre else ()

def validate_manifest(m):
    if not isinstance(m, dict) or m.get('schemaVersion') != 1:
        raise ValueError('Unsupported script manifest')
    required = ('id', 'version', 'fileName', 'description', 'author', 'license', 'amiVersionRange', 'sha256')
    if any(not isinstance(m.get(k), str) or not m[k].strip() or len(m[k]) > 4000 for k in required):
        raise ValueError('Manifest requires identity, version, filename, description, author, license, AMI range and SHA-256')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}', m['id']) or not re.fullmatch(VERSION, m['version']):
        raise ValueError('Invalid script ID or version')
    name = m['fileName']
    if len(name) > 200 or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:cs|csx)', name, re.I) or re.match(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\.', name, re.I):
        raise ValueError('fileName must be a portable .cs or .csx basename')
    if not re.fullmatch(r'[a-fA-F0-9]{64}', m['sha256']) or type(m.get('size')) is not int or not 0 < m['size'] <= MAX_SOURCE:
        raise ValueError('Invalid script SHA-256 or size')
    # Standard NuGet numeric ranges plus wildcard; full compatibility is checked by the .NET consumer.
    vr = m['amiVersionRange']
    if not (vr == '*' or re.fullmatch(VERSION, vr) or re.fullmatch(r'\[' + VERSION + r'\]', vr)
            or re.fullmatch(r'[\[(]\s*(?:' + VERSION + r')?\s*,\s*(?:' + VERSION + r')?\s*[\])]', vr)):
        raise ValueError('Invalid AMI version range')
    return {k: m[k] for k in ('schemaVersion', *required, 'size')}

def atomic_json(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.script-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write('\n')
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

def register_script(root, payload):
    if not isinstance(payload, dict) or set(payload) != {'manifest', 'content'}:
        raise ValueError('Expected manifest and base64 content')
    m = validate_manifest(payload['manifest'])
    try:
        content = base64.b64decode(payload['content'], validate=True)
        text = content.decode('utf-8-sig')
    except (ValueError, TypeError, UnicodeError, binascii.Error) as error:
        raise ValueError('Content must be base64 encoded UTF-8 source') from error
    if '\0' in text or len(content) != m['size'] or hashlib.sha256(content).hexdigest().lower() != m['sha256'].lower():
        raise ValueError('Source size, UTF-8 text or SHA-256 mismatch')
    directory = Path(root) / 'scripts'
    directory.mkdir(parents=True, exist_ok=True)
    index_path = directory / 'script-index.json'
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {'schemaVersion': 1, 'items': []}
    previous = next((r for r in index['items'] if r['id'].lower() == m['id'].lower() and version_key(r['version']) == version_key(m['version'])), None)
    if previous:
        comparable = {k: v for k, v in previous.items() if k != 'path'}
        if comparable == m:
            return 200, index
        raise ValueError('Version already exists with different content or metadata; increase version')
    if any(r['id'].lower() == m['id'].lower() and r['id'] != m['id'] for r in index['items']):
        raise ValueError('Script ID casing conflict')
    # Content-addressed folders avoid identifier path traversal and make publication independent of version spelling.
    relative = f"files/{hashlib.sha256(content).hexdigest()}/{m['fileName']}"
    target = directory / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    created = not target.exists()
    if created:
        with target.open('xb') as stream:
            stream.write(content)
    elif target.read_bytes() != content:
        raise ValueError('Existing source has been corrupted')
    m['path'] = relative
    index['items'].append(m)
    try:
        atomic_json(index_path, index)
    except BaseException:
        if created: target.unlink(missing_ok=True)
        raise
    return 201, index
