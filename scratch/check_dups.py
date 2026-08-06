import ast
from collections import Counter

def check_duplicates(file_path):
    with open(file_path, "r") as f:
        content = f.read()

    tree = ast.parse(content)
    functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef)]
    
    counts = Counter(functions)
    duplicates = {name: count for name, count in counts.items() if count > 1}
    
    print(f"Total functions: {len(functions)}")
    print(f"Total unique functions: {len(counts)}")
    print(f"Duplicates: {duplicates}")
    
    classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    counts_cls = Counter(classes)
    duplicates_cls = {name: count for name, count in counts_cls.items() if count > 1}
    
    print(f"Total classes: {len(classes)}")
    print(f"Total unique classes: {len(counts_cls)}")
    print(f"Duplicate classes: {duplicates_cls}")

if __name__ == "__main__":
    check_duplicates("app/main.py")
