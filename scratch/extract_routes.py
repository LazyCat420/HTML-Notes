import ast
import sys

def main():
    try:
        with open("app/main.py", "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Let's write a robust script that uses ast to find endpoints and extracts them.
        with open("app/main.py", "r", encoding="utf-8") as f:
            content = f.read()
        
        tree = ast.parse(content)
        
        routes = []
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) or isinstance(node, ast.FunctionDef):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                        if decorator.func.value.id == "app" and decorator.func.attr in ("get", "post", "delete"):
                            start = node.lineno - 1 # 0-indexed
                            end = node.end_lineno
                            routes.append((start, end, node.name))

        for r in routes:
            print(f"Route {r[2]} from {r[0]} to {r[1]}")
            
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
