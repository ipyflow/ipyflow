# -*- coding: future_annotations -*-
import ast
import textwrap

from nbsafety.tracing.ast_eavesdrop import AstEavesdropper
from nbsafety.tracing.trace_events import TraceEvent
from nbsafety.utils import KeyDict


PROGRAM = textwrap.dedent(
    """
    for i in [foo(x) for x in [1, 2, 3]]:
        try:
            logging.info(i)
        except:
            logging.warning("warning!")
        finally:
            sys.exit(0)
            
    if False:
        asdf(qwer(1, 2, a().b[c,d,e](f.g()).h))[a, 7] = foo.bar
    """
)


def test_ast_rewrite():
    """
    No asserts; just make sure we don't throw an error.
    """
    rewriter = AstEavesdropper(KeyDict(), frozenset(TraceEvent))
    assert rewriter.visit(ast.parse(PROGRAM)) is not None
