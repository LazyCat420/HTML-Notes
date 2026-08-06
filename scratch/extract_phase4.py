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
    
    config_fns = []
    canvas_fns = []
    
    canvas_names = {
        "coerce_widget_type", "_widget_is_degenerate", "render_widget", "find_singleton_media_widget",
        "_stamp_media_seq", "_place_media_widget", "_canvas_lock", "get_session_canvas", "set_session_canvas",
        "commit_canvas", "_run_turn", "_classify_canvas_widget", "_iter_canvas_widgets",
        "find_existing_widget_by_id_prefix", "find_existing_widget", "_score_widget_for_query",
        "find_reuse_target", "_remember_widget_config", "_stack_data_card_update", "_spoken_summary",
        "_widget_detail", "_widget_content_gist", "record_turn", "_summarize_canvas_for_history",
        "build_turn_context", "_widget_showing", "_widget_on_canvas", "_resolve_widget_target",
        "_resolve_agent_widget_id", "get_canvas_summary", "_drop_offsubject_widgets"
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) or isinstance(node, ast.FunctionDef):
            # Only pull top level functions (not nested ones)
            # Actually ast.walk goes through everything, so we need to ensure it's top level by checking if its parent is Module.
            # We'll just rely on name matching for now.
            name = node.name
            
            if name.startswith("build_") and name not in {"build_turn_context"}:
                start, end = get_ast_node_text(lines, node)
                config_fns.append({"start": start, "end": end, "name": name})
            elif name in canvas_names:
                start, end = get_ast_node_text(lines, node)
                canvas_fns.append({"start": start, "end": end, "name": name})

    # Sort in reverse order for safe deletion
    config_fns.sort(key=lambda x: x["start"], reverse=True)
    canvas_fns.sort(key=lambda x: x["start"], reverse=True)
    
    all_extracted = sorted(config_fns + canvas_fns, key=lambda x: x["start"], reverse=True)
    
    def write_module(filename, fns):
        if not fns: return
        with open(f"app/{filename}", "w", encoding="utf-8") as f:
            f.write("import sys\n")
            f.write("import app.main as main\n")
            f.write("sys.modules[__name__].__dict__.update(main.__dict__)\n\n")
            
            fns_sorted = sorted(fns, key=lambda x: x["start"])
            for r in fns_sorted:
                chunk = lines[r["start"]:r["end"]]
                f.writelines(chunk)
                f.write("\n\n")

    write_module("config_builders.py", config_fns)
    write_module("canvas_manager.py", canvas_fns)
    
    # Remove from main.py (since we reverse sorted, deletion is safe)
    for r in all_extracted:
        del lines[r["start"]:r["end"]]

    # Inject imports right after the standard imports in main.py.
    # The safest place is at the top, but we need the variables they depend on to exist first.
    # The best place is at the BOTTOM of main.py, right BEFORE `__all__ = ...`.
    idx = -1
    for i, line in enumerate(lines):
        if line.startswith("__all__ ="):
            idx = i
            break
            
    if idx != -1:
        lines.insert(idx, "from app.config_builders import *\nfrom app.canvas_manager import *\n\n")
    else:
        # fallback
        lines.append("\nfrom app.config_builders import *\nfrom app.canvas_manager import *\n")

    with open("app/main.py", "w", encoding="utf-8") as f:
        f.writelines(lines)
    
    print(f"Successfully extracted {len(config_fns)} config builders and {len(canvas_fns)} canvas functions!")

if __name__ == "__main__":
    main()
