import re
import sys

def main():
    try:
        with open("app/main.py", "r", encoding="utf-8") as f:
            content = f.read()

        # Add top-level _page_text
        top_level_page_text = """
async def _fetch_news_page_text(n):
    url = n.get("url", "")
    if not url:
        return ""
    try:
        page = await read_web_page(url, max_chars=4000)
        return "" if page.get("is_error") else (page.get("content") or "")
    except Exception:
        return ""
"""
        if "_fetch_news_page_text" not in content:
            # Insert after read_web_page
            idx = content.find("async def read_web_page")
            if idx != -1:
                end_idx = content.find("async def", idx + 10)
                content = content[:end_idx] + top_level_page_text + "\n\n" + content[end_idx:]

        # Replace inner _page_text declarations
        inner_pt = """    async def _page_text(n):
        url = n.get("url", "")
        if not url:
            return ""
        try:
            # 4000 chars, not 2000: these syndicated finance articles open with a
            # long teaser intro (and embedded ad copy), so a short read hands the
            # editor only the tease and its summaries come out content-free
            # ("a specific Vanguard ETF" — never naming it).
            page = await read_web_page(url, max_chars=4000)
            return "" if page.get("is_error") else (page.get("content") or "")
        except Exception:
            return ""
"""
        inner_pt_alt = """    async def _page_text(r):
        url = r.get("url", "")
        if not url:
            return ""
        try:
            page = await read_web_page(url, max_chars=4000)
            return "" if page.get("is_error") else (page.get("content") or "")
        except Exception:
            return ""
"""
        content = content.replace(inner_pt, "")
        content = content.replace(inner_pt_alt, "")
        
        # Now replace the calls
        content = content.replace("_page_text(n)", "_fetch_news_page_text(n)")
        content = content.replace("_page_text(r)", "_fetch_news_page_text(r)")
        
        with open("app/main.py", "w", encoding="utf-8") as f:
            f.write(content)
            
        print("Refactored _page_text successfully")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
