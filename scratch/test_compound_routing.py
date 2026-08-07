import asyncio
import re

def check_is_compound(text: str) -> bool:
    text_clean = text.lower().strip()
    is_video_ask = bool(re.search(r'\b(video|videos|youtube|watch|clip|vids?)\b', text_clean))
    is_data_ask = bool(re.search(r'\b(article|articles|news|list|boots|trails|guide|summary|info|review|reviews|buying|buy)\b', text_clean))
    has_conjunction = bool(re.search(r'\b(and|also|plus|along with|both|as well as)\b', text_clean))
    
    return is_compound_ask if 'is_compound_ask' in locals() else (has_conjunction and is_video_ask and is_data_ask)

def test_compound():
    q1 = "top hiking trails in oregon i want the articles and a video please"
    q2 = "best hiking trails video and best hiking boots list of best boots to buy"
    q3 = "show me a video of a cat"
    
    assert check_is_compound(q1) == True, f"q1 should be compound: {q1}"
    assert check_is_compound(q2) == True, f"q2 should be compound: {q2}"
    assert check_is_compound(q3) == False, f"q3 should NOT be compound: {q3}"
    print("✓ All compound query detection assertions passed!")

if __name__ == "__main__":
    test_compound()
