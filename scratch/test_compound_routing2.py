import re

def check_compound_v2(text: str) -> bool:
    text_clean = text.lower().strip()
    is_video_ask = bool(re.search(r'\b(video|videos|youtube|watch|clip|vids?)\b', text_clean))
    is_stock_ask = bool(re.search(r'\b(chart|charts|stock|stocks|ticker|share|shares|price|nvda|tsla|aapl|msft|googl|amzn)\b', text_clean))
    is_data_ask = bool(re.search(r'\b(article|articles|news|list|boots|trails|guide|summary|info|review|reviews|buying|buy)\b', text_clean))
    is_list_ask = bool(re.search(r'\b(list|checklist|to-?dos?)\b', text_clean))
    is_map_ask = bool(re.search(r'\b(map|location|where|directions)\b', text_clean))
    is_weather_ask = bool(re.search(r'\b(weather|forecast|temperature)\b', text_clean))

    has_conjunction = bool(re.search(r'\b(and|also|plus|along with|both|as well as|with)\b', text_clean))

    intents = [is_video_ask, is_data_ask, is_stock_ask, is_list_ask, is_map_ask, is_weather_ask]
    active_intents = sum(1 for x in intents if x)

    return bool(has_conjunction and active_intents >= 2)

def test_v2():
    q1 = "news articles for NVDA and the chart please"
    q2 = "top hiking trails in oregon i want the articles and a video please"
    q3 = "best hiking trails video and best hiking boots list of best boots to buy"
    q4 = "show me a video of NVDA" # 1 intent (video of NVDA)
    q5 = "weather and news for Seattle"
    
    assert check_compound_v2(q1) == True, f"Failed q1: {q1}"
    assert check_compound_v2(q2) == True, f"Failed q2: {q2}"
    assert check_compound_v2(q3) == True, f"Failed q3: {q3}"
    assert check_compound_v2(q4) == False, f"Failed q4: {q4}"
    assert check_compound_v2(q5) == True, f"Failed q5: {q5}"
    print("✓ All compound v2 test queries passed!")

if __name__ == "__main__":
    test_v2()
