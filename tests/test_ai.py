import pytest
from services.ai_service import FallbackChatbot, AIService


def test_fallback_returns_dict(app):
    """AIService fallback (no API key) returns a dict with 'message' key via 'content'."""
    with app.app_context():
        result = FallbackChatbot.respond(
            conversation_history=[],
            user_message="I cannot log into the application",
            kb_context=[],
        )
        assert isinstance(result, dict), "FallbackChatbot.respond must return a dict"
        # The key used throughout is 'content' (not 'message') per design
        assert "content" in result, f"Expected 'content' key in result, got: {list(result.keys())}"
        assert len(result["content"]) > 0


def test_login_keywords_detected(app):
    """Message with 'login' keyword → detected_category contains 'login'."""
    with app.app_context():
        result = FallbackChatbot.respond(
            conversation_history=[],
            user_message="I cannot login to my account",
            kb_context=[],
        )
        assert result.get("category") == "login", \
            f"Expected category='login', got: {result.get('category')}"


def test_crash_keywords_detected(app):
    """Message with 'crash' → category is 'crashes'."""
    with app.app_context():
        result = FallbackChatbot.respond(
            conversation_history=[],
            user_message="The application keeps crashing when I open it",
            kb_context=[],
        )
        assert result.get("category") == "crashes"


def test_ask_confirmation_set(app):
    """FallbackChatbot always sets ask_confirmation=True."""
    with app.app_context():
        result = FallbackChatbot.respond(
            conversation_history=[],
            user_message="My password expired",
            kb_context=[],
        )
        assert result.get("ask_confirmation") is True


def test_ai_service_uses_fallback_without_key(app):
    """AIService uses FallbackChatbot when OPENAI_API_KEY is not set."""
    with app.app_context():
        app.config["OPENAI_API_KEY"] = ""
        result = AIService.get_response([], "I have a database connection error", [])
        assert isinstance(result, dict)
        assert "content" in result
