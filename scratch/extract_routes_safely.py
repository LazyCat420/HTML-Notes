import os
import sys

def main():
    try:
        os.makedirs("app/routes", exist_ok=True)
        
        with open("app/main.py", "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        # Extract send_message (8098 to 10824)
        # Note: 0-indexed in Python
        send_message_lines = lines[8097:10824]
        
        # Modify the route decorator
        send_message_lines[0] = send_message_lines[0].replace("@app.", "@router.")
        
        with open("app/routes/message.py", "w", encoding="utf-8") as f:
            f.write("from fastapi import APIRouter\n")
            f.write("from app.main import *\n\n")
            f.write("router = APIRouter()\n\n")
            f.writelines(send_message_lines)
            
        # Extract internal_tool_execute (11326 to 11781)
        internal_lines = lines[11325:11781]
        internal_lines[0] = internal_lines[0].replace("@app.", "@router.")
        
        with open("app/routes/internal.py", "w", encoding="utf-8") as f:
            f.write("from fastapi import APIRouter\n")
            f.write("from app.main import *\n\n")
            f.write("router = APIRouter()\n\n")
            f.writelines(internal_lines)
            
        # Update main.py
        # We need to remove these lines and add the router inclusion at the bottom
        new_main_lines = lines[:8097] + lines[10824:11325] + lines[11781:]
        
        router_imports = [
            "\n",
            "from app.routes.message import router as message_router\n",
            "app.include_router(message_router)\n",
            "\n",
            "from app.routes.internal import router as internal_router\n",
            "app.include_router(internal_router)\n"
        ]
        
        new_main_lines.extend(router_imports)
        
        with open("app/main.py", "w", encoding="utf-8") as f:
            f.writelines(new_main_lines)
            
        # Create an __init__.py for routes
        open("app/routes/__init__.py", "w").close()
            
        print("Safely extracted routes")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
