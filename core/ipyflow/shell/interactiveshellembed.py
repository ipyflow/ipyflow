# -*- coding: utf-8 -*-
import sys

from IPython.core.interactiveshell import InteractiveShell
from IPython.terminal.embed import InteractiveShellEmbed
from IPython.terminal.ipapp import load_default_config

from ipyflow import singletons
from ipyflow.shell.interactiveshell import UsesIPyflowShell


class IPyflowInteractiveShellEmbed(
    singletons.IPyflowShell, InteractiveShellEmbed, metaclass=UsesIPyflowShell  # type: ignore
):
    pass


# copied from IPython.terminal.embed
# s/InteractiveShellEmbed/IPyflowInteractiveShellEmbed/g
def embed(*, header="", compile_flags=None, **kwargs):
    """Call this to embed IPyflow at the current point in your program.

    The first invocation of this will create a :class:`terminal.embed.InteractiveShellEmbed`
    instance and then call it.  Consecutive calls just call the already
    created instance.

    If you don't want the kernel to initialize the namespace
    from the scope of the surrounding function,
    and/or you want to load full IPython configuration,
    you probably want `IPython.start_ipython()` instead.

    Here is a simple example::

        from ipyflow.shell import embed
        a = 10
        b = 20
        embed(header='First time')
        c = 30
        d = 40
        embed()

    Parameters
    ----------

    header : str
        Optional header string to print at startup.
    compile_flags
        Passed to the `compile_flags` parameter of :py:meth:`terminal.embed.InteractiveShellEmbed.mainloop()`,
        which is called when the :class:`terminal.embed.InteractiveShellEmbed` instance is called.
    **kwargs : various, optional
        Any other kwargs will be passed to the :class:`terminal.embed.InteractiveShellEmbed` constructor.
        Full customization can be done by passing a traitlets :class:`Config` in as the
        `config` argument (see :ref:`configure_start_ipython` and :ref:`terminal_options`).
    """
    config = kwargs.get("config")
    if config is None:
        config = load_default_config()
        config.InteractiveShellEmbed = config.TerminalInteractiveShell
        kwargs["config"] = config
    using = kwargs.get("using", "sync")
    if using:
        kwargs["config"].update(
            {
                "TerminalInteractiveShell": {
                    "loop_runner": using,
                    "colors": "NoColor",
                    "autoawait": using != "sync",
                }
            }
        )
    # save ps1/ps2 if defined
    ps1 = None
    ps2 = None
    try:
        ps1 = sys.ps1
        ps2 = sys.ps2
    except AttributeError:
        pass
    # save previous instance
    saved_shell_instance = InteractiveShell._instance
    if saved_shell_instance is not None:
        cls = type(saved_shell_instance)
        cls.clear_instance()
    frame = sys._getframe(1)
    shell = IPyflowInteractiveShellEmbed.instance(
        _init_location_id="%s:%s" % (frame.f_code.co_filename, frame.f_lineno), **kwargs
    )
    shell(
        header=header,
        stack_depth=2,
        compile_flags=compile_flags,
        _call_location_id="%s:%s" % (frame.f_code.co_filename, frame.f_lineno),
    )
    IPyflowInteractiveShellEmbed.clear_instance()
    # restore previous instance
    if saved_shell_instance is not None:
        cls = type(saved_shell_instance)
        cls.clear_instance()
        for subclass in cls._walk_mro():
            subclass._instance = saved_shell_instance
    if ps1 is not None:
        sys.ps1 = ps1
        sys.ps2 = ps2
