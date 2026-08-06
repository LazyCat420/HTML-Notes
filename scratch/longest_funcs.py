import ast

def find_longest_nodes(file_path):
    with open(file_path, "r") as f:
        content = f.read()
    
    tree = ast.parse(content)
    
    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = getattr(node, 'lineno', 0)
            end = getattr(node, 'end_lineno', 0)
            if start and end:
                length = end - start + 1
                nodes.append((length, node.name, type(node).__name__))
                
    nodes.sort(reverse=True)
    print(f"Top 20 longest functions/classes in {file_path}:")
    for length, name, nodetype in nodes[:20]:
        print(f"{length} lines - {nodetype} {name}")

if __name__ == "__main__":
    find_longest_nodes("app/main.py")
