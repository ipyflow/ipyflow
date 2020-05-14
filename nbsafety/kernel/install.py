# -*- coding: utf-8 -*-
import json
import os
import platform
import sys
import argparse

from jupyter_client.kernelspec import KernelSpecManager
from IPython.utils.tempdir import TemporaryDirectory

PACKAGE = __package__.split('.')[0]
DISPLAY_NAME = f"Python 3 ({PACKAGE})"
kernel_json = {
 "argv": [
   sys.executable, "-m", "nbsafety.kernel", "-f", "{connection_file}",
 ],
 "display_name": DISPLAY_NAME,
 "language": "python",
 "codemirror_mode": "shell",
}


def install_my_kernel_spec(user=True, prefix=None):
    with TemporaryDirectory() as td:
        os.chmod(td, 0o755)  # Starts off as 700, not user readable
        cp = 'cp'
        if platform.system().lower().startswith('win'):
            cp = 'copy'
        import nbsafety
        resources = os.path.join(os.path.dirname(os.path.abspath(nbsafety.__file__)), 'resources')
        logo32 = os.path.join(resources, 'logo-32x32.png')
        logo64 = os.path.join(resources, 'logo-64x64.png')
        os.system(f'{cp} {logo32} {logo64} {td}')
        with open(os.path.join(td, 'kernel.json'), 'w') as f:
            json.dump(kernel_json, f, sort_keys=True)
        # TODO: Copy resources once they're specified

        print(f'Installing KernelSpec for {PACKAGE} kernel')
        KernelSpecManager().install_kernel_spec(td, kernel_name=f'{PACKAGE}', user=user, prefix=prefix)


def _is_root():
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False  # assume not an admin on non-Unix platforms


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=f'Install KernelSpec for {PACKAGE} kernel.'
    )
    prefix_locations = parser.add_mutually_exclusive_group()

    prefix_locations.add_argument(
        '--user',
        help='Install KernelSpec in user homedirectory',
        action='store_true'
    )
    prefix_locations.add_argument(
        '--sys-prefix',
        help='Install KernelSpec in sys.prefix. Useful in conda / virtualenv',
        action='store_true',
        dest='sys_prefix'
    )
    prefix_locations.add_argument(
        '--prefix',
        help='Install KernelSpec in this prefix',
        default=None
    )

    args = parser.parse_args(argv)

    user = False
    prefix = None
    if args.sys_prefix:
        prefix = sys.prefix
    elif args.prefix:
        prefix = args.prefix
    elif args.user or not _is_root():
        user = True

    install_my_kernel_spec(user=user, prefix=prefix)
    return 0


if __name__ == '__main__':
    sys.exit(main())
