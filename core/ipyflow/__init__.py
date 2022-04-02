# -*- coding: utf-8 -*-

# Jupyter Extension points
def _jupyter_nbextension_paths():
    return [
        dict(
            section="notebook",
            # the path is relative to the `my_fancy_module` directory
            src="resources/nbextension",
            # directory in the `nbextension/` namespace
            dest="ipyflow",
            # _also_ in the `nbextension/` namespace
            require="ipyflow/index",
        )
    ]


def load_jupyter_server_extension(nbapp):
    pass


from . import _version

__version__ = _version.get_versions()["version"]

from . import _version

__version__ = _version.get_versions()["version"]
