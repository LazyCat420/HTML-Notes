import sys

def main():
    try:
        with open("app/main.py", "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        # Find where router imports start
        idx = -1
        for i, line in enumerate(lines):
            if line.startswith("from app.routes.message"):
                idx = i
                break
                
        if idx != -1:
            lines.insert(idx, "__all__ = list(globals().keys())\n\n")
            
        with open("app/main.py", "w", encoding="utf-8") as f:
            f.writelines(lines)
            
        print("Added __all__")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
