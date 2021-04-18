# -*- coding: future_annotations -*-
from nbsafety._version import get_versions
__version__ = get_versions()['version']
del get_versions


def make_version_tuple(vstr=None):
    if vstr is None:
        vstr = __version__
    if vstr[0] == 'v':
        vstr = vstr[1:]
    components = []
    for component in vstr.split('+')[0].split('.'):
        try:
            components.append(int(component))
        except ValueError:
            break
    return tuple(components)


version = '.'.join(str(d) for d in make_version_tuple())
