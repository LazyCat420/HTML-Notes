import sys

def main():
    try:
        with open("app/youtube_service.py", "r", encoding="utf-8") as f:
            content = f.read()

        # Add missing standard library imports
        imports_to_add = "import difflib\nimport urllib.parse\nimport xml.etree.ElementTree as ET\n"
        if "import difflib" not in content:
            idx = content.find("import asyncio")
            content = content[:idx] + imports_to_add + content[idx:]

        # Replace dynamic variables
        content = content.replace("MUSIC_PLAYER_URL", "__import__('app.main', fromlist=['MUSIC_PLAYER_URL']).MUSIC_PLAYER_URL")
        content = content.replace("_YAHOO_UA", "__import__('app.main', fromlist=['_YAHOO_UA'])._YAHOO_UA")
        content = content.replace("urllib.parse.", "urllib.parse.") # Already added import urllib.parse

        with open("app/youtube_service.py", "w", encoding="utf-8") as f:
            f.write(content)
            
        print("Fixed missing imports successfully")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
