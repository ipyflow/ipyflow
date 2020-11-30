import ast


class StatementInserter(ast.NodeTransformer):
    def __init__(self, insert_stmt_template: str, cell_counter: int):
        self.insert_stmt_template = insert_stmt_template
        self.cell_counter = cell_counter
        self.cur_line_id = 0

    def _get_parsed_insert_stmt(self):
        ret = ast.parse(self.insert_stmt_template.format(site_id=(self.cell_counter, self.cur_line_id))).body[0]
        self.cur_line_id += 1
        return ret

    def visit(self, node):
        if not hasattr(node, 'body'):
            return node
        if not all(isinstance(nd, ast.stmt) for nd in node.body):
            return node
        new_stmts = []
        for stmt in node.body:
            insert_stmt = self._get_parsed_insert_stmt()
            ast.copy_location(insert_stmt, stmt)
            new_stmts.append(insert_stmt)
            new_stmts.append(self.visit(stmt))
        node.body = new_stmts
        return node
