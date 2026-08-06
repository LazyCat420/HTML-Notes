import ast
import json

def get_ast_node_text(lines, node):
    start = node.lineno - 1
    if hasattr(node, "decorator_list") and node.decorator_list:
        start = node.decorator_list[0].lineno - 1
    end = node.end_lineno
    return start, end

def main():
    with open("app/main.py", "r", encoding="utf-8") as f:
        content = f.read()
        lines = content.splitlines(True)
    
    tree = ast.parse(content)
    
    routes = []
    internal_fn = None
    
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) or isinstance(node, ast.FunctionDef):
            if node.name == "internal_tool_execute":
                start, end = get_ast_node_text(lines, node)
                internal_fn = {"start": start, "end": end, "name": node.name, "path": "FUNCTION"}
                routes.append(internal_fn)
                continue
            
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                    if decorator.func.value.id == "app" and decorator.func.attr in ("get", "post", "delete"):
                        path = ""
                        if decorator.args and isinstance(decorator.args[0], ast.Constant):
                            path = decorator.args[0].value
                        
                        start, end = get_ast_node_text(lines, node)
                        routes.append({
                            "start": start,
                            "end": end,
                            "name": node.name,
                            "path": path
                        })
                        break

    routes.sort(key=lambda x: x["start"], reverse=True)
    
    message_routes = []
    notes_routes = []
    health_routes = []
    api_routes = []
    internal_routes = []
    
    for r in routes:
        path = r["path"]
        if r["name"] == "internal_tool_execute":
            internal_routes.append(r)
        elif path.startswith("/session") or path.startswith("/models"):
            message_routes.append(r)
        elif path.startswith("/api/notes") or path.startswith("/notes"):
            notes_routes.append(r)
        elif path.startswith("/health"):
            health_routes.append(r)
        elif path.startswith("/api"):
            api_routes.append(r)
        elif path.startswith("/widgets") or path.startswith("/user/memory"):
            internal_routes.append(r)

    def write_router(filename, router_routes):
        if not router_routes: return
        with open(f"app/routes/{filename}", "w", encoding="utf-8") as f:
            f.write("from fastapi import APIRouter, Request, HTTPException, Response\n")
            f.write("import sys\n")
            f.write("import app.main as main\n")
            f.write("sys.modules[__name__].__dict__.update(main.__dict__)\n\n")
            f.write("router = APIRouter()\n\n")
            
            # Write them in original order
            router_routes.sort(key=lambda x: x["start"])
            for r in router_routes:
                chunk = lines[r["start"]:r["end"]]
                # Replace @app. with @router. for routes
                if r["path"] != "FUNCTION":
                    for i in range(len(chunk)):
                        if chunk[i].strip().startswith("@app."):
                            chunk[i] = chunk[i].replace("@app.", "@router.")
                f.writelines(chunk)
                f.write("\n\n")

    import os
    os.makedirs("app/routes", exist_ok=True)
    write_router("message.py", message_routes)
    write_router("notes.py", notes_routes)
    write_router("health.py", health_routes)
    write_router("api.py", api_routes)
    write_router("internal.py", internal_routes)
    
    # Remove from main.py
    for r in routes:
        if r in message_routes or r in notes_routes or r in health_routes or r in api_routes or r in internal_routes:
            del lines[r["start"]:r["end"]]

    # Inject __all__ and include_routers at the end
    lines.append("\n__all__ = list(globals().keys())\n\n")
    lines.append("from app.routes.message import router as message_router\napp.include_router(message_router)\n")
    lines.append("from app.routes.notes import router as notes_router\napp.include_router(notes_router)\n")
    lines.append("from app.routes.health import router as health_router\napp.include_router(health_router)\n")
    lines.append("from app.routes.api import router as api_router\napp.include_router(api_router)\n")
    lines.append("from app.routes.internal import router as internal_router\napp.include_router(internal_router)\n")

    with open("app/main.py", "w", encoding="utf-8") as f:
        f.writelines(lines)
    
    print("Successfully extracted all routes!")

if __name__ == "__main__":
    main()
