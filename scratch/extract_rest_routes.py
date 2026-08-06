import ast
import os
import sys

def main():
    try:
        with open("app/main.py", "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        with open("app/main.py", "r", encoding="utf-8") as f:
            content = f.read()
            
        tree = ast.parse(content)
        
        routes = []
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) or isinstance(node, ast.FunctionDef):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                        if decorator.func.value.id == "app" and decorator.func.attr in ("get", "post", "delete"):
                            start = node.decorator_list[0].lineno - 1
                            end = node.end_lineno
                            routes.append({
                                "start": start,
                                "end": end,
                                "name": node.name,
                                "path": decorator.args[0].value if decorator.args else ""
                            })
                            
        # Sort in reverse order so deleting doesn't mess up indices
        routes.sort(key=lambda x: x["start"], reverse=True)
        
        # Categorize
        notes_routes = []
        health_routes = []
        api_routes = []
        other_routes = []
        
        for r in routes:
            path = r["path"]
            if "/notes" in path:
                notes_routes.append(r)
            elif "/health" in path:
                health_routes.append(r)
            elif "/api/" in path:
                api_routes.append(r)
            else:
                other_routes.append(r)
                
        def write_router(filename, route_list):
            if not route_list: return
            
            with open(f"app/routes/{filename}", "w", encoding="utf-8") as f:
                f.write("from fastapi import APIRouter, Request, HTTPException\n")
                f.write("from app.main import *\n\n")
                f.write("router = APIRouter()\n\n")
                
                # Write in original order (reverse the reversed list)
                for r in reversed(route_list):
                    chunk = lines[r["start"]:r["end"]]
                    chunk[0] = chunk[0].replace("@app.", "@router.")
                    f.writelines(chunk)
                    f.write("\n")
                    
        write_router("notes.py", notes_routes)
        write_router("health.py", health_routes)
        write_router("api.py", api_routes)
        
        # Now remove them from main.py
        for r in routes:
            if r in notes_routes or r in health_routes or r in api_routes:
                del lines[r["start"]:r["end"]]
                
        # Append includes
        router_imports = [
            "\n",
            "from app.routes.notes import router as notes_router\n",
            "app.include_router(notes_router)\n",
            "\n",
            "from app.routes.health import router as health_router\n",
            "app.include_router(health_router)\n",
            "\n",
            "from app.routes.api import router as api_router\n",
            "app.include_router(api_router)\n"
        ]
        
        lines.extend(router_imports)
        
        with open("app/main.py", "w", encoding="utf-8") as f:
            f.writelines(lines)
            
        print("Successfully extracted notes, health, and api routes")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
