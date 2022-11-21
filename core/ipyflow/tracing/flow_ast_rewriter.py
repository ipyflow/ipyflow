# -*- coding: utf-8 -*-
import ast
import logging
import traceback
from typing import cast

from pyccolo import AstRewriter

from ipyflow.data_model.code_cell import cells
from ipyflow.singletons import flow

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class DataflowAstRewriter(AstRewriter):
    def visit(self, node: ast.AST):
        try:
            ret = super().visit(node)
            cells().current_cell().to_ast(
                override=cast(ast.Module, self.orig_to_copy_mapping[id(node)])
            )
            return ret
        except Exception as e:
            flow().get_and_set_exception_raised_during_execution(e)
            traceback.print_exc()
            raise e
