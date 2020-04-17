# -*- coding: utf-8 -*-


# Program will raise this error instead of breaking
class UNEXPECTED_STATES(Exception):
    """
    There are three stages: Precheck, Run, Update.  Visit_node represents this
    error happened in which method(e.g. visit_Assign) of the ast.NodeVisitor
    error_node is the ast node argument that caused this error msg is the extra
    msg to explain the error.
    """

    def __init__(self, state, visit_node, error_node, msg=""):
        self.stage = state
        self.visit_node = visit_node
        self.error_node = error_node
        self.msg = msg
