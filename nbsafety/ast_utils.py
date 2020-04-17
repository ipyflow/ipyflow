import ast


# Helper function to remove the subscript and return the name node in front
# of the subscript For example: pass in ast.Subscript node "a[3][b][5]"
# will return ast.Name node "a".
def remove_subscript(node: ast.AST):
    while isinstance(node, ast.Subscript):
        node = node.value
    return node
