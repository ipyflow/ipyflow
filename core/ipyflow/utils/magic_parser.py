# -*- coding: utf-8 -*-
import argparse


class MagicParser(argparse.ArgumentParser):
    """
    This just prevents argparse calling `sys.exit` when -h or --help are passed.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._help_printed = False

    def exit(self, status=0, message=None):
        if message is not None and not self._help_printed:
            raise ValueError(message)

    def error(self, message):
        if not self._help_printed:
            super().error(message)

    def print_help(self, *args, **kwargs) -> None:
        super().print_help(*args, **kwargs)
        self._help_printed = True

    def parse_args(self, *args, **kwargs) -> argparse.Namespace:  # type: ignore
        ret = super().parse_args(*args, **kwargs)
        ret.help = self._help_printed
        self._help_printed = False
        return ret
