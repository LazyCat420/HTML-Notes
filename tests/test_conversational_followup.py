import pytest
from unittest.mock import patch, AsyncMock
from app import main as m
from app import canvas_manager

def test_record_turn_and_context_block_preserves_assistant_dialogue():
    session_id = "test-session-dialogue-ledger"
    canvas_manager._session_turn_ledger[session_id] = []

    # Turn 1: user asks for sausage recipe, creates widget
    canvas_manager.record_turn(
        session_id=session_id,
        message="give me a good recipe for italian raw sausages",
        route="local:data_card",
        widgets=[("dc-sausage", "data_card", "Italian Sausage Recipe", "Ingredients and cooking methods")],
    )

    # Turn 2: user opens Bloomberg news (media widget)
    canvas_manager.record_turn(
        session_id=session_id,
        message="bloomberg live news",
        route="local:youtube_player",
        widgets=[("yt-bloomberg", "youtube_player", "Bloomberg News Live", "")],
    )

    # Turn 3: conversational follow-up with assistant reply
    canvas_manager.record_turn(
        session_id=session_id,
        message="so i shouldn't boil the sausages?",
        route="reply",
        widgets=[],
        assistant_reply="No, don't boil them — pan-fry or roast at medium heat so the casing doesn't burst."
    )

    # Check turn context block
    canvas_html = '''<div id="dashboard-grid">
        <div id="dc-sausage" class="widget-container" data-widget-type="data_card">
            <h3 class="glass-card-title">Italian Sausage Recipe</h3>
            <p>Pan-fry raw Italian sausages in olive oil until golden brown.</p>
        </div>
        <div id="yt-bloomberg" class="widget-container" data-widget-type="youtube_player">
            <h3 class="glass-card-title">Bloomberg News Live Stream</h3>
        </div>
    </div>'''

    ctx = canvas_manager.build_turn_context(session_id, canvas_html)
    block = ctx["context_block"]

    # Must preserve the assistant's previous advice in the context block
    assert "pan-fry or roast at medium heat" in block or "don't boil them" in block

    # The focus_id should NOT be the passive media player (youtube_player) when resolving conversational focus
    assert ctx["focus_id"] == "dc-sausage"


def test_followup_target_id_ignores_passive_media_players():
    session_id = "test-session-media-immunity"
    canvas_manager._session_turn_ledger[session_id] = []

    canvas_html = '''<div id="dashboard-grid">
        <div id="dc-sausage" class="widget-container" data-widget-type="data_card">
            <h3 class="glass-card-title">Italian Sausage Recipe</h3>
            <p>Sear raw sausages on medium heat. Do not boil.</p>
        </div>
        <div id="yt-bloomberg" class="widget-container" data-widget-type="youtube_player">
            <h3 class="glass-card-title">Bloomberg News Live Stream</h3>
        </div>
    </div>'''

    # Seed ledger
    canvas_manager.record_turn(
        session_id=session_id,
        message="recipe for sausages",
        route="data_card",
        widgets=[("dc-sausage", "data_card", "Italian Sausage Recipe", "Sear raw sausages")],
    )
    canvas_manager.record_turn(
        session_id=session_id,
        message="bloomberg news",
        route="youtube_player",
        widgets=[("yt-bloomberg", "youtube_player", "Bloomberg News", "")],
    )

    # For an elliptical question like "even raw?", it should anchor to the sausage card, NOT the youtube player
    target = m._followup_target_id(session_id, focus_id="yt-bloomberg", message="even raw?")
    assert target == "dc-sausage"


@pytest.mark.asyncio
async def test_route_with_llm_defers_cooking_followups(patch_server):
    # Router should ONLY emit 'reply' for greetings/thanks/chitchat.
    # Questions about domain topics like cooking/recipes must defer to the full agent.
    async def mock_fast_llm_json(prompt, max_tokens=550):
        # Even if the LLM proposes a reply for a cooking question, the system should catch it or prompt should forbid it
        return {
            "reply": "Yes, Italian raw sausage is meant to be cooked before eating, but the ",
            "reason": "answer from canvas",
            "checks": {"wants": "answer", "subject": "sausages"}
        }

    patch_server("fast_llm_json", mock_fast_llm_json)
    plan = await m.route_with_llm("even raw?", "CURRENT CANVAS:\n- #dc-sausage · data_card \"Italian Sausage Recipe\"")
    # For a substantive follow-up question with wants='answer', it should NOT return a hasty 1-sentence reply
    assert plan.get("defer") is True or plan.get("widgets") or plan.get("reply") is None


def test_is_conversational_question():
    # Conversational follow-ups seeking explanation or advice:
    assert m._is_conversational_question("so i shouldn't boil the sausages?") is True
    assert m._is_conversational_question("even raw?") is True
    assert m._is_conversational_question("why shouldn't i boil them?") is True
    assert m._is_conversational_question("can i bake them instead?") is True
    assert m._is_conversational_question("is it safe to eat them pink?") is True

    # Widget mutation/refinement commands must NOT be classified as conversational questions:
    assert m._is_conversational_question("what about the cheaper ones?") is False
    assert m._is_conversational_question("only show waterproof ones") is False
    assert m._is_conversational_question("just the cheap ones") is False
    assert m._is_conversational_question("without the expensive ones") is False

    # Genuinely new requests:
    assert m._is_conversational_question("weather in tokyo") is False
    assert m._is_conversational_question("set a 5 minute timer") is False


def test_conversational_followup_resolution_does_not_force_widget_mutation():
    session_id = "test-session-conv-resolution"
    canvas_manager._session_turn_ledger[session_id] = []

    canvas_html = '''<div id="dashboard-grid">
        <div id="dc-sausage" class="widget-container" data-widget-type="data_card">
            <h3 class="glass-card-title">Italian Sausage Recipe</h3>
            <p>Sear raw sausages on medium heat. Do not boil.</p>
        </div>
        <div id="yt-bloomberg" class="widget-container" data-widget-type="youtube_player">
            <h3 class="glass-card-title">Bloomberg News Live Stream</h3>
        </div>
    </div>'''
    canvas_manager.set_session_canvas(session_id, canvas_html)

    canvas_manager.record_turn(
        session_id=session_id,
        message="recipe for sausages",
        route="data_card",
        widgets=[("dc-sausage", "data_card", "Italian Sausage Recipe", "Sear raw sausages")],
    )
    canvas_manager.record_turn(
        session_id=session_id,
        message="bloomberg news",
        route="youtube_player",
        widgets=[("yt-bloomberg", "youtube_player", "Bloomberg News", "")],
    )

    turn_ctx = canvas_manager.build_turn_context(session_id, canvas_html)
    message = "so i shouldn't boil the sausages?"

    is_conv_q = m._is_conversational_question(message)
    assert is_conv_q is True

    # followup_target must be None for conversational questions so it doesn't order "do not answer in prose"
    followup_target = (
        m._followup_target_id(session_id, turn_ctx.get("focus_id"), message)
        if m._is_refining_followup(message) and not is_conv_q else None)
    assert followup_target is None

    # But conversational_card_id must resolve to the sausage recipe card (skipping youtube_player!)
    conversational_card_id = (
        m._followup_target_id(session_id, turn_ctx.get("focus_id"), message)
        if is_conv_q else None)
    assert conversational_card_id == "dc-sausage"

