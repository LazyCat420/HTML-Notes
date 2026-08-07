import re
from bs4 import BeautifulSoup

def simulate_widget_removal(canvas_html: str, user_query: str) -> str:
    soup = BeautifulSoup(canvas_html, "html.parser")
    text_clean = user_query.strip().lower()
    
    target_text = re.sub(
        r'\b(remove|delete|close|hide|clear|dismiss|drop|kill|stop|widget|card|window|the|it|my|a|an|get rid of)\b',
        '', text_clean, flags=re.I).strip()
    target_tokens = [t.lower() for t in re.findall(r'\w+', target_text) if len(t) > 1]
    
    widgets = soup.select('.canvas-widget, .glass-card, .widget-container, [id^="widget-"], [data-widget-type]')
    for w in widgets:
        w_id = (w.get("id") or "").lower()
        w_type = (w.get("data-widget-type") or "").lower()
        w_title = (w.get("data-title") or "").lower()
        w_text = w.get_text().lower()

        combined = f"{w_id} {w_type} {w_title} {w_text}"
        
        if not target_tokens or any(t in combined for t in target_tokens):
            w.decompose()
            
    return str(soup)

def test_removal():
    sample_html = """
    <div id="dashboard-grid">
        <div id="widget-video-1" class="canvas-widget" data-widget-type="video" data-title="CNN Live News Stream">
            <h2>CNN Live News</h2>
            <iframe></iframe>
        </div>
        <div id="widget-clock-1" class="canvas-widget" data-widget-type="clock" data-title="World Clock">
            <h2>Clock</h2>
        </div>
    </div>
    """
    
    # Test 1: Remove CNN Live News
    result1 = simulate_widget_removal(sample_html, "close cnn live news widget")
    assert "widget-video-1" not in result1, "CNN video widget should be removed!"
    assert "widget-clock-1" in result1, "Clock widget should remain!"
    print("✓ Test 1: 'close cnn live news widget' correctly removed CNN video widget!")

    # Test 2: Remove Clock
    result2 = simulate_widget_removal(sample_html, "remove the clock")
    assert "widget-clock-1" not in result2, "Clock widget should be removed!"
    assert "widget-video-1" in result2, "CNN video widget should remain!"
    print("✓ Test 2: 'remove the clock' correctly removed Clock widget!")

if __name__ == "__main__":
    test_removal()
