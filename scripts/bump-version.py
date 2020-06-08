#!/usr/bin/env python
import json
import subprocess
import sys

import versioneer
from nbsafety.version import make_version_tuple


def main():
    components = list(make_version_tuple())
    components[-1] += 1
    version = '.'.join(str(c) for c in components)
    if len(sys.argv) == 2 and sys.argv[1] == '--tag':
        subprocess.check_output(['git', 'tag', version])
    else:
        print(f"skipping 'git tag {version}'")
    with open('./frontend/labextension/package.in.json', 'r') as f:
        package_json = json.loads(f.read())
    package_json['version'] = version
    with open('./frontend/labextension/package.json', 'w') as f:
        f.write(json.dumps(package_json, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
