import sys

def main():
    try:
        with open("app/youtube_service.py", "r", encoding="utf-8") as f:
            content = f.read()

        # Fix fast_llm_json
        content = content.replace("from app.main import fast_llm_json\n    data = await fast_llm_json(", "    from app.main import fast_llm_json\n    data = await fast_llm_json(")

        # Fix _scrape
        bad_scrape1 = """        html = from app.main import _scrape
            return await _scrape("""
        good_scrape1 = """        from app.main import _scrape
        html = await _scrape("""
        content = content.replace(bad_scrape1, good_scrape1)
        
        bad_scrape2 = """        html = from app.main import _scrape
    html = await _scrape("""
        good_scrape2 = """        from app.main import _scrape
        html = await _scrape("""
        content = content.replace(bad_scrape2, good_scrape2)

        bad_scrape3 = """    from app.main import _scrape
            return await _scrape("""
        good_scrape3 = """    from app.main import _scrape
    return await _scrape("""
        content = content.replace(bad_scrape3, good_scrape3)

        with open("app/youtube_service.py", "w", encoding="utf-8") as f:
            f.write(content)
            
        print("Fixed imports successfully")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
