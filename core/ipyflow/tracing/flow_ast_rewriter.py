# -*- coding: utf-8 -*-
import ast
import logging
import traceback
from typing import cast

import pyccolo as pyc

from ipyflow.data_model.cell import cells
from ipyflow.singletons import flow

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class DataflowAstRewriter(pyc.AstRewriter):
    # we do our own garbage collection
    gc_bookkeeping = False

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._already_run = False

    def should_instrument_with_tracer(self, _tracer: pyc.BaseTracer) -> bool:
        return True

    def visit(self, node: ast.AST):
        # prevents calling the same transformer multiple times due to e.g. magics like %time
        if self._already_run:
            return node
        self._already_run = True
        try:
            last_tracer = self._tracers[-1]
            old_bookkeeper = last_tracer.ast_bookkeeper_by_fname.get(self._path)
            ret = super().visit(node)
            # after call to super().visit(...), orig_to_copy_mapping should be set
            assert self.orig_to_copy_mapping is not None
            cells().current_cell().to_ast(
                override=cast(ast.Module, self.orig_to_copy_mapping[id(node)])
            )
            if old_bookkeeper is not None and self._module_id is None:
                new_bookkeeper = last_tracer.ast_bookkeeper_by_fname[self._path]
                assert new_bookkeeper is not old_bookkeeper
                last_tracer.remove_bookkeeping(new_bookkeeper, id(node))
                last_tracer.ast_bookkeeper_by_fname[self._path] = old_bookkeeper
            return ret
        except Exception as e:
            flow().get_and_set_exception_raised_during_execution(e)
            traceback.print_exc()
            raise e
