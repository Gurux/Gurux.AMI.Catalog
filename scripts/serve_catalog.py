"""Generate and serve a development catalog using only Python's standard library."""
import argparse
from datetime import datetime, timezone
import fnmatch
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import lzma
import os
from pathlib import Path
import re
import stat
import subprocess
import shutil
import socket
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit
import zipfile
import zlib

from build_catalog import build_catalog, build_rss, generate_catalog, validate_references
from catalog_metadata import release_metadata
from script_catalog import register_script, atomic_json

SERVER_VERSION = '1.2.0'


def localization_validator():
    configured = os.environ.get('GURUX_LOCALIZATION_VALIDATOR')
    if configured:
        path = Path(configured).resolve()
        if not path.is_file():
            raise RuntimeError('GURUX_LOCALIZATION_VALIDATOR must point to Gurux.Updater.Tool.dll')
        return path
    project = Path(__file__).resolve().parents[2] / 'Gurux.Updater.Net/Development/Gurux.Updater.Tool/bin'
    candidates = list(project.glob('*/net10.0/Gurux.Updater.Tool.dll'))
    if not candidates:
        raise RuntimeError('Build Gurux.Updater.Tool or set GURUX_LOCALIZATION_VALIDATOR to its DLL path')
    return max(candidates, key=lambda path: path.stat().st_mtime)


def validate_localization(body):
    descriptor, filename = tempfile.mkstemp(suffix='.json')
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(body)
        try:
            result = subprocess.run([shutil.which('dotnet') or 'dotnet', str(localization_validator()),
                                     'localization', 'validate-package', '--package', filename],
                                    capture_output=True, text=True, timeout=30, shell=False)
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
            raise UploadError(503, f'Localization validator unavailable: {error}') from error
        try:
            result_data = json.loads(result.stdout)
        except (ValueError, UnicodeError):
            result_data = None
        if result.returncode == 2 and isinstance(result_data, dict) and result_data.get('valid') is False:
            message = str(result_data.get('error', 'Invalid package')).replace(filename, '<package.json>')
            raise UploadError(400, f'Invalid localization package: {message}')
        if result.returncode != 0 or not isinstance(result_data, dict) or result_data.get('valid') is not True:
            raise UploadError(503, 'Localization validator unavailable; check the updater build and .NET 10 runtime')
    finally:
        Path(filename).unlink(missing_ok=True)


def validate_script_manifest(manifest):
    """Use the consumer's NuGet parser so an invalid release cannot poison its index."""
    descriptor, filename = tempfile.mkstemp(suffix='.json')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(manifest, stream)
        configured = os.environ.get('GURUX_SCRIPT_VALIDATOR')
        validator = Path(configured).resolve() if configured else localization_validator()
        try:
            result = subprocess.run([shutil.which('dotnet') or 'dotnet', str(validator),
                                     'script', 'validate-manifest', '--manifest', filename],
                                    capture_output=True, text=True, timeout=30, shell=False)
            value = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            raise UploadError(503, 'Script validator unavailable; build the updater CLI') from error
        if result.returncode == 2 and isinstance(value, dict) and value.get('valid') is False:
            raise UploadError(400, str(value.get('error', 'Invalid script manifest')).replace(filename, '<manifest>'))
        if result.returncode != 0 or not isinstance(value, dict) or value.get('valid') is not True:
            raise UploadError(503, 'Script validator unavailable; build the updater CLI')
    except RuntimeError as error:
        raise UploadError(503, str(error)) from error
    finally:
        Path(filename).unlink(missing_ok=True)


class UploadError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def portable_name(value):
    """Reject names unsafe on either Windows or Unix, including alternate streams."""
    return (bool(value) and value not in ('.', '..') and not value.endswith((' ', '.'))
            and not any(ord(char) < 32 or char in '<>:"/\\|?*' for char in value)
            and not re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value))


def upload_metadata(query):
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise UploadError(400, 'Invalid upload query') from error
    allowed = {'product', 'type', 'version', 'filename', 'name', 'prerelease', 'references', 'amiVersionRange'}
    if set(values) - allowed or any(len(value) != 1 for value in values.values()):
        raise UploadError(400, 'Unknown or repeated upload parameter')
    metadata = {key: value[0] for key, value in values.items()}
    try:
        release_metadata(metadata)
    except ValueError as error:
        raise UploadError(400, str(error)) from error
    if 'references' in metadata:
        try:
            metadata['references'] = validate_references(json.loads(metadata['references']))
        except (ValueError, TypeError) as error:
            raise UploadError(400, str(error)) from error
    product = metadata.get('product', '')
    version = metadata.get('version', '')
    filename = metadata.get('filename', '')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}', product):
        raise UploadError(400, 'product must be a nonempty product ID')
    if metadata.get('type') not in ('module', 'application', 'agent'):
        raise UploadError(400, 'type must be module, application or agent')
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?(?:\+[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?', version):
        raise UploadError(400, 'version must have 2 to 4 numeric components and optional prerelease/build metadata')
    if len(filename) > 240 or not portable_name(filename) or not filename.lower().endswith('.zip'):
        raise UploadError(400, 'filename must be a portable ZIP basename')
    if 'name' in metadata and (not metadata['name'].strip() or len(metadata['name']) > 500
                              or any(ord(char) < 32 for char in metadata['name'])):
        raise UploadError(400, 'name must be nonempty display text')
    if 'prerelease' in metadata and metadata['prerelease'] not in ('true', 'false'):
        raise UploadError(400, 'prerelease must be true or false')
    metadata['isPrerelease'] = (metadata['prerelease'] == 'true' if 'prerelease' in metadata
                                else '-' in version.split('+', 1)[0])
    return metadata


def validate_zip(path):
    try:
        with zipfile.ZipFile(path) as archive:
            if not archive.infolist():
                raise UploadError(400, 'ZIP must contain at least one entry')
            for entry in archive.infolist():
                # Treat backslashes as separators because installers may run on Windows.
                name = entry.orig_filename.replace('\\', '/')
                parts = name.rstrip('/').split('/')
                if not all(portable_name(part) for part in parts):
                    raise UploadError(400, 'ZIP contains an unsafe entry path')
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise UploadError(400, 'ZIP symbolic links are not supported')
            if archive.testzip() is not None:
                raise UploadError(400, 'ZIP contains a damaged entry')
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError,
            ValueError, OSError, zlib.error, lzma.LZMAError) as error:
        raise UploadError(400, 'Body must be a valid, unencrypted ZIP archive') from error


def staged_json(path, value):
    descriptor, filename = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def staged_text(path, value):
    descriptor, filename = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(value)
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def restore_bytes(path, content):
    descriptor, filename = tempfile.mkstemp(prefix='.rollback-', dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class CatalogRegistry:
    def __init__(self, sources, directory, count, include_prereleases, base_url, catalog):
        self.sources = Path(sources).resolve()
        self.directory = directory
        self.count = count
        self.include_prereleases = include_prereleases
        self.base_url = base_url
        self.catalog = catalog
        self.lock = threading.Lock()

    def register_localization(self, body):
        # Python routes requests only. C# owns JSON validation, hashes, versions,
        # locking and index-last publication for both wire formats.
        from localization_catalog import publish_request, copy_history, LocalizationPublicationError
        persistent = self.sources.parent / 'localization'
        with self.lock:
            try:
                status, result = publish_request(body, persistent / 'localization-index.json')
            except LocalizationPublicationError as error:
                raise UploadError(error.status, str(error)) from error
            copy_history(persistent, self.directory / 'localization', validate=False)
            return status, result

    def register(self, metadata, temporary, digest):
        with self.lock:
            original_sources = self.sources.read_bytes()
            sources = json.loads(original_sources.decode('utf-8'))
            item = next((item for item in sources['items'] if item['id'] == metadata['product']), None)
            if item is not None:
                if item.get('source', 'github') != 'local' or item.get('type') != metadata['type']:
                    raise UploadError(409, 'Product already exists with a different source or type')
                release = next((release for release in item['releases']
                                if release['version'] == metadata['version']), None)
                if release is not None:
                    package = (self.sources.parent / release['package']).resolve()
                    checksum = hashlib.sha256()
                    with package.open('rb') as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                            checksum.update(chunk)
                    if checksum.hexdigest() == digest:
                        return 200, self.catalog
                    raise UploadError(409, 'Different package bytes already exist for this version; bump the version')
            else:
                item = {'id': metadata['product'], 'type': metadata['type'], 'source': 'local',
                        'name': metadata.get('name', metadata['product']), 'releases': []}
                sources['items'].append(item)
            if not fnmatch.fnmatchcase(metadata['filename'].lower(), item.get('assetPattern', '*.zip').lower()):
                raise UploadError(400, 'filename does not match the existing product assetPattern')
            destination = self.directory / 'packages' / digest / metadata['filename']
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                os.replace(temporary, destination)
            item['releases'].append({'version': metadata['version'], 'package': str(destination),
                                     **release_metadata(metadata),
                                     **({'references': metadata['references']} if 'references' in metadata else {}),
                                     'publishedAt': datetime.now(timezone.utc).isoformat(),
                                     'isPrerelease': metadata['isPrerelease']})
            # Only local entries are rebuilt here. Remote entries retain the startup catalog.
            local_sources = {**sources, 'items': [item for item in sources['items']
                                                 if item.get('source', 'github') == 'local']}
            local_catalog = build_catalog(local_sources, lambda repository: None, self.count,
                                          self.include_prereleases, source_directory=self.sources.parent,
                                          output_directory=self.directory, base_url=self.base_url)
            local_items = {item['id']: item for item in local_catalog['items']}
            cached = {item['id']: item for item in self.catalog['items']}
            catalog = {**self.catalog, 'generatedAt': local_catalog['generatedAt'],
                       'items': [local_items[item['id']] if item.get('source', 'github') == 'local'
                                 else cached[item['id']] for item in sources['items']]}
            output = self.directory / 'catalog.json'
            feed = self.directory / 'rss.xml'
            original_feed = feed.read_bytes() if feed.exists() else None
            staged_sources = staged_json(self.sources, sources)
            staged_catalog = None
            staged_feed = None
            sources_replaced = False
            feed_replaced = False
            try:
                staged_catalog = staged_json(output, catalog)
                staged_feed = staged_text(feed, build_rss(catalog, self.base_url))
                os.replace(staged_sources, self.sources)
                sources_replaced = True
                os.replace(staged_feed, feed)
                feed_replaced = True
                os.replace(staged_catalog, output)
            except BaseException:
                if sources_replaced:
                    restore_bytes(self.sources, original_sources)
                if feed_replaced:
                    if original_feed is None:
                        feed.unlink(missing_ok=True)
                    else:
                        restore_bytes(feed, original_feed)
                raise
            finally:
                staged_sources.unlink(missing_ok=True)
                if staged_catalog is not None:
                    staged_catalog.unlink(missing_ok=True)
                if staged_feed is not None:
                    staged_feed.unlink(missing_ok=True)
            self.catalog = catalog
            return 201, catalog


class CatalogServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR can allow another server to bind an occupied port.
    allow_reuse_address = os.name != 'nt'

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class CatalogHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).path == '/health':
            self.send_json(200, self.server.health)
            return
        super().do_GET()

    def send_json(self, status, value):
        body = (json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        temporary = None
        try:
            parsed = urlsplit(self.path)
            if parsed.path == '/scripts/script-index.json':
                if parsed.query or self.headers.get('Transfer-Encoding'):
                    raise UploadError(400, 'Script publication requires a plain URL and Content-Length')
                lengths = self.headers.get_all('Content-Length', [])
                if len(lengths) != 1 or not re.fullmatch(r'[0-9]+', lengths[0]):
                    raise UploadError(411, 'Content-Length is required')
                size = int(lengths[0])
                if not 0 < size <= 6 * 1024 * 1024:
                    raise UploadError(413, 'Script publication must be 1 byte to 6 MiB')
                if self.headers.get_content_type() != 'application/json':
                    raise UploadError(415, 'Content-Type must be application/json')
                self.connection.settimeout(30)
                body = self.rfile.read(size)
                if len(body) != size:
                    raise UploadError(400, 'Body is shorter than Content-Length')
                try:
                    payload = json.loads(body)
                    if not isinstance(payload, dict):
                        raise ValueError('Script publication must be an object')
                    validate_script_manifest(payload.get('manifest'))
                    with self.server.registry.lock:
                        status, catalog = register_script(self.server.registry.directory, payload)
                except (ValueError, UnicodeError) as error:
                    raise UploadError(409 if 'conflict' in str(error) or 'already exists' in str(error) else 400, str(error)) from error
                self.send_json(status, catalog)
                return
            if parsed.path == '/localization/localization-index.json':
                if parsed.query or self.headers.get('Transfer-Encoding'):
                    raise UploadError(400, 'Localization registration requires a plain URL and Content-Length')
                lengths = self.headers.get_all('Content-Length', [])
                if len(lengths) != 1 or not re.fullmatch(r'[0-9]+', lengths[0]):
                    raise UploadError(411, 'Content-Length is required')
                size = int(lengths[0])
                if not 0 < size <= 16 * 1024 * 1024:
                    raise UploadError(413, 'Localization JSON must be between 1 byte and 16 MiB')
                if self.headers.get_content_type() != 'application/json':
                    raise UploadError(415, 'Content-Type must be application/json')
                self.connection.settimeout(30)
                body = self.rfile.read(size)
                if len(body) != size:
                    raise UploadError(400, 'Body is shorter than Content-Length')
                status, catalog = self.server.registry.register_localization(body)
                self.send_json(status, catalog)
                return
            if parsed.path != '/catalog.json':
                raise UploadError(404, 'Upload endpoint is /catalog.json')
            metadata = upload_metadata(parsed.query)
            if self.headers.get('Transfer-Encoding'):
                raise UploadError(400, 'Chunked uploads are not supported; send Content-Length')
            lengths = self.headers.get_all('Content-Length', [])
            if not lengths:
                raise UploadError(411, 'Content-Length is required')
            if len(lengths) != 1 or not re.fullmatch(r'[0-9]+', lengths[0]):
                raise UploadError(400, 'Invalid Content-Length')
            size = int(lengths[0])
            if size == 0:
                raise UploadError(400, 'ZIP body must not be empty')
            if self.headers.get_content_type() != 'application/zip':
                raise UploadError(415, 'Content-Type must be application/zip')
            registry = self.server.registry
            descriptor, filename = tempfile.mkstemp(prefix='.upload-', suffix='.zip', dir=registry.directory)
            temporary = Path(filename)
            checksum = hashlib.sha256()
            remaining = size
            self.connection.settimeout(30)
            with os.fdopen(descriptor, 'wb') as stream:
                while remaining:
                    chunk = self.rfile.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise UploadError(400, 'Body is shorter than Content-Length')
                    stream.write(chunk)
                    checksum.update(chunk)
                    remaining -= len(chunk)
            validate_zip(temporary)
            status, catalog = registry.register(metadata, temporary, checksum.hexdigest())
            self.send_json(status, catalog)
        except UploadError as error:
            self.send_json(error.status, {'error': str(error)})
        except TimeoutError:
            self.send_json(408, {'error': 'Upload body timed out'})
        except Exception as error:
            self.log_error('Catalog registration failed: %s', error)
            self.send_json(500, {'error': 'Catalog registration failed; existing catalog and sources were preserved'})
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def create_server(sources, directory, port=8000, count=10, include_prereleases=True):
    directory = Path(directory).resolve()
    handler = partial(CatalogHandler, directory=str(directory))
    server = CatalogServer(('127.0.0.1', port), handler)
    try:
        server.health = {'status': 'ok', 'version': SERVER_VERSION,
                         'scriptPath': str(Path(__file__).resolve()),
                         'startedAt': datetime.now(timezone.utc).isoformat(), 'processId': os.getpid()}
        base_url = f'http://localhost:{server.server_port}'
        catalog = generate_catalog(sources, directory / 'catalog.json', count, include_prereleases, base_url)
        server.registry = CatalogRegistry(sources, directory, count, include_prereleases, base_url, catalog)
        scripts = directory / 'scripts'
        scripts.mkdir(parents=True, exist_ok=True)
        if not (scripts / 'script-index.json').exists():
            atomic_json(scripts / 'script-index.json', {'schemaVersion': 1, 'items': []})
    except BaseException:
        server.server_close()
        raise
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', default='catalog-sources.json')
    parser.add_argument('--directory', default='_site')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--count', type=int, default=10)
    parser.add_argument('--stable-only', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535')
    with create_server(args.sources, args.directory, args.port, args.count,
                       not args.stable_only) as server:
        print(f"Catalog server {SERVER_VERSION}: {Path(__file__).resolve()} (PID {os.getpid()})", flush=True)
        print(f'Catalog: http://localhost:{server.server_port}/catalog.json', flush=True)
        print(f'Health: http://localhost:{server.server_port}/health', flush=True)
        print('POST ZIPs to /catalog.json to register releases immediately. Press Ctrl+C to stop.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
