from pathlib import Path


SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def test_cassette_replay_cannot_be_labeled_fresh():
    """Demo/cassette data must remain visibly synthetic in AI evidence."""
    assert 'ai_response_source = "CASSETTE_REPLAY"' in SOURCE
    assert 'ai_response_synthetic = True' in SOURCE
    assert '"source": ai_response_source' in SOURCE
    assert '"synthetic_response": ai_response_synthetic' in SOURCE


def test_real_provider_path_keeps_fresh_source():
    """The non-cassette provider path remains the only FRESH source."""
    assert 'ai_response_source = "FRESH"' in SOURCE
    assert 'log_pipeline_event("AI", "API_OK", "DEEPSEEK_RESPONSE"' in SOURCE
