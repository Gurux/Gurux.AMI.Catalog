"""Localization transport and artifact copying. All format/version rules live in C#."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


class LocalizationPublicationError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def run_tool(arguments):
    from serve_catalog import localization_validator
    result = subprocess.run([shutil.which('dotnet') or 'dotnet', str(localization_validator()),
                             'localization', *map(str, arguments)],
                            capture_output=True, text=True, timeout=120, shell=False)
    return result


def publish_request(body, index):
    try:
        # Inspect only the envelope to choose the command; C# parses the original bytes.
        envelope = json.loads(body)
        snapshot = isinstance(envelope, dict) and 'packages' in envelope
    except (ValueError, UnicodeError):
        raise LocalizationPublicationError(400, 'Invalid localization JSON')
    descriptor, filename = tempfile.mkstemp(suffix='.json')
    index = Path(index)
    before = index.read_bytes() if index.exists() else None
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(body)
        try:
            result = run_tool(['publish-snapshot' if snapshot else 'publish-package',
                               '--snapshot' if snapshot else '--package', filename, '--index', index, '--json'])
            data = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as error:
            raise LocalizationPublicationError(503, f'Localization publisher unavailable: {error}') from error
        if result.returncode in (2, 3) and isinstance(data, dict) and data.get('valid') is False:
            raise LocalizationPublicationError(409 if result.returncode == 3 else 400, data.get('error', 'Invalid publication'))
        if result.returncode != 0 or not isinstance(data, dict):
            raise LocalizationPublicationError(503, 'Localization publisher unavailable; build the current updater and .NET 10 runtime')
        changed = before != index.read_bytes()
        return (201 if changed else 200), data
    finally:
        Path(filename).unlink(missing_ok=True)


def copy_history(source, destination, validate=True, index_name='localization-index.json'):
    """Copy immutable packages before atomically installing the index; never delete history."""
    source, destination = Path(source), Path(destination)
    index = source / index_name
    if not index.is_file() or source.resolve() == destination.resolve():
        return
    # Capture before walking packages: a concurrent publisher may only append
    # immutable files, so this snapshot's files are already present throughout.
    index_bytes = index.read_bytes()
    if validate:
        if index_name == 'resource-index.json':
            result = run_content_tool(['validate-resources', '--catalog-root', source.parent])
        else:
            result = run_tool(['validate-store', '--index', index])
        if result.returncode:
            raise ValueError(f'Invalid localization history: {result.stderr or result.stdout}')
    destination.mkdir(parents=True, exist_ok=True)
    packages = source / 'packages'
    if packages.exists():
        shutil.copytree(packages, destination / 'packages', dirs_exist_ok=True,
                        copy_function=copy_immutable, ignore=shutil.ignore_patterns('*.tmp', '*.lock'))
    if (destination / index.name).is_file() and (destination / index.name).read_bytes() == index_bytes:
        return
    descriptor, filename = tempfile.mkstemp(prefix='.localization-index-', dir=destination)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(index_bytes)
        os.replace(filename, destination / index.name)
    finally:
        Path(filename).unlink(missing_ok=True)


def copy_immutable(source, destination):
    """Never truncate a served file. Hard-link a staged complete file without overwriting."""
    destination = Path(destination)
    data = Path(source).read_bytes()
    if destination.exists():
        if destination.read_bytes() != data:
            raise ValueError(f'Conflicting immutable package in publication output: {destination}')
        return str(destination)
    descriptor, filename = tempfile.mkstemp(prefix='.package-', suffix='.tmp', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
        try:
            os.link(filename, destination)
        except FileExistsError:
            if destination.read_bytes() != data:
                raise ValueError(f'Concurrent immutable package conflict: {destination}')
        return str(destination)
    finally:
        Path(filename).unlink(missing_ok=True)


def run_content_tool(arguments):
    from serve_catalog import localization_validator
    return subprocess.run([shutil.which('dotnet') or 'dotnet', str(localization_validator()), 'content', *map(str, arguments)],
                          capture_output=True, text=True, timeout=120, shell=False)
