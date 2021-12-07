# -*- coding: future_annotations -*-
import ast
import logging
import sys
from contextlib import contextmanager
from importlib.abc import MetaPathFinder
from importlib.machinery import SourceFileLoader
from importlib.util import spec_from_loader, decode_source


logger = logging.getLogger(__name__)


class TraceLoader(SourceFileLoader):
    def __init__(self, tracer, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tracer = tracer
        self._ast_rewriter = tracer.make_ast_rewriter()
        self._syntax_augmenters = tracer.make_syntax_augmenters(self._ast_rewriter)
        self._augmentation_context: bool = False

    @contextmanager
    def syntax_augmentation_context(self):
        orig_aug_context = self._augmentation_context
        try:
            self._augmentation_context = True
            yield
        finally:
            self._augmentation_context = orig_aug_context

    def get_data(self, path) -> bytes:
        if self._augmentation_context or not self._tracer.should_trace_source_path(path):
            return super().get_data(path)
        with self.syntax_augmentation_context():
            source = self.get_augmented_source(path)
            return bytes(source, encoding='utf-8')

    def get_code(self, fullname):
        source_path = self.get_filename(fullname)
        if not self._tracer.should_trace_source_path(source_path):
            return super().get_code(fullname)
        source_bytes = self.get_data(source_path)
        return self.source_to_code(source_bytes, source_path)

    def get_augmented_source(self, source_path) -> str:
        source_bytes = super().get_data(source_path)
        still_needs_decode = True
        try:
            source = decode_source(source_bytes)
            still_needs_decode = False
        except SyntaxError:
            # this allows us to handle esoteric encodings that require parsing, such as future_annotations
            # in this case, just guess that it's utf-8 encoded

            # this is a bit unfortunate in that it involves multiple round-trips of decoding / encoding,
            # but it's the only way I can think of to ensure that source transformations happen in the
            # correct order
            source = str(source_bytes, encoding='utf-8')
        for augmenter in self._syntax_augmenters:
            source = augmenter(source)
        if still_needs_decode:
            source = decode_source(bytes(source, encoding='utf-8'))
        return source

    def source_to_code(self, data, path, *, _optimize=-1):
        ret = None
        try:
            if self._tracer.should_trace_source_path(path):
                ret = compile(
                    self._ast_rewriter.visit(ast.parse(data)), path, 'exec', dont_inherit=True, optimize=_optimize
                )
        except Exception:
            pass
        finally:
            if ret is None:
                ret = super().source_to_code(data, path, _optimize=_optimize)
        return ret


# this is based on the birdseye finder (which uses import hooks based on MacroPy's):
# https://github.com/alexmojaki/birdseye/blob/9974af715b1801f9dd99fef93ff133d0ab5223af/birdseye/import_hook.py
class TraceFinder(MetaPathFinder):
    def __init__(self, tracer) -> None:
        self.tracer = tracer

    def _find_plain_spec(self, fullname, path, target):
        """Try to find the original module using all the
        remaining meta_path finders."""
        spec = None
        for finder in sys.meta_path:
            # when testing with pytest, it installs a finder that for
            # some yet unknown reasons makes birdseye
            # fail. For now it will just avoid using it and pass to
            # the next one
            if finder is self or 'pytest' in finder.__module__:
                continue
            if hasattr(finder, 'find_spec'):
                spec = finder.find_spec(fullname, path, target=target)
            elif hasattr(finder, 'load_module'):
                spec = spec_from_loader(fullname, finder)

            if spec is not None and spec.origin != 'builtin':
                return spec

    def find_spec(self, fullname, path=None, target=None):
        spec = self._find_plain_spec(fullname, path, target)
        if spec is None or not (hasattr(spec.loader, 'get_source') and
                                callable(spec.loader.get_source)):  # noqa: E128
            if fullname != 'org':
                # stdlib pickle.py at line 94 contains a ``from
                # org.python.core for Jython which is always failing,
                # of course
                logger.debug('Failed finding spec for %s', fullname)
            return None

        if not isinstance(spec.loader, SourceFileLoader):
            return None
        source_path = spec.loader.get_filename(fullname)
        if not self.tracer.should_trace_source_path(source_path):
            return None

        spec.loader = TraceLoader(self.tracer, spec.loader.name, spec.loader.path)
        return spec
