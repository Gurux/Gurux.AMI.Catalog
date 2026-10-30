"""Validation for optional project metadata and declared AMI compatibility."""
import re

MAINTENANCE_STATUSES = ('active', 'minimal', 'seeking-maintainer', 'unsupported')
VERSION = r'[0-9]+(?:\.[0-9]+){0,3}(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?(?:\+[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?'


def project_metadata(source):
    result = {}
    for field in ('maintenanceStatus', 'category', 'audience'):
        if field not in source:
            continue
        value = source[field]
        if (not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*', value)
                or len(value) > 100 or field == 'maintenanceStatus' and value not in MAINTENANCE_STATUSES):
            raise ValueError(f'Invalid {field}')
        result[field] = value
    return result


def release_metadata(source):
    if 'amiVersionRange' not in source:
        return {}
    value = source['amiVersionRange']
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ValueError('Invalid amiVersionRange')
    if value == '*' or re.fullmatch(VERSION, value) or re.fullmatch(r'\[' + VERSION + r'\]', value):
        return {'amiVersionRange': value}
    match = re.fullmatch(r'([\[(])\s*(' + VERSION + r')?\s*,\s*(' + VERSION + r')?\s*([\])])', value)
    if match:
        left, lower, upper, right = match.groups()
        if lower is None and left != '(' or upper is None and right != ')':
            raise ValueError('Unbounded AMI range endpoints must be exclusive')
        if lower or upper:
            def key(version):
                number, _, pre = version.split('+', 1)[0].partition('-')
                parts = tuple(int(x) for x in number.split('.'))
                labels = tuple((0, int(x)) if x.isdigit() else (1, x.lower()) for x in pre.split('.'))
                return parts + (0,) * (4 - len(parts)), not bool(pre), labels
            if not lower or not upper or key(lower) < key(upper) or key(lower) == key(upper) and left == '[' and right == ']':
                return {'amiVersionRange': value}
    raise ValueError('Invalid amiVersionRange')
