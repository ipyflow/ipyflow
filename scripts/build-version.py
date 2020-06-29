#!/usr/bin/env python
import argparse
import json
import os
import subprocess
import sys

from nbsafety.version import make_version_tuple


def main(args):
    components = list(make_version_tuple())
    if args.bump:
        components[-1] += 1
    version = '.'.join(str(c) for c in components)
    if args.tag:
        subprocess.check_output(['git', 'tag', version])
    for package_dot_json_loc in ['./frontend/labextension', './frontend/nbextension']:
        package_dot_json = os.path.join(package_dot_json_loc, 'package.json')
        with open(package_dot_json, 'r') as f:
            package_json = json.loads(f.read())
        if package_json.get('version', None) != version:
            package_json['version'] = version
            with open(package_dot_json, 'w') as f:
                f.write(json.dumps(package_json, indent=2))
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Create and synchronize version tags across packages.'
    )
    parser.add_argument('--bump', action='store_true', help='Whether to increment the version.')
    parser.add_argument('--tag', action='store_true', help='Whether to increment the version.')
    args = parser.parse_args()
    sys.exit(main(args))
