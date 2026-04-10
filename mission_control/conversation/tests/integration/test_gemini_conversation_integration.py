
import os

import pytest

from mission_control.conversation.abstract_conversation import Role
from mission_control.conversation.gemini.gemini_factory import GeminiFactory

# Mark all tests in this file as integration tests
pytestmark = pytest.mark.integration

class TestGeminiConversationIntegration:

    @pytest.fixture(scope="class")
    def conversation_instance(self):
        """Fixture to provide a conversation instance from the factory."""
        api_key = os.getenv("GEMINI_AI_KEY")
        if not api_key:
            pytest.skip("GEMINI_AI_KEY environment variable not set. Skipping integration test.")
        
        # The factory will internally handle the API key configuration
        factory = GeminiFactory(model_name="gemini-2.5-flash")
        return factory.get_conversation()

    def test_send_text_message_and_get_response(self, conversation_instance):
        """
        Tests a simple text-based conversation with the real Gemini API using the factory.
        """
        # Start a transaction and send a message
        conversation_instance.begin_transaction(Role.USER)
        conversation_instance.add_text_message("Hello, who are you? Respond in one short sentence.")
        conversation_instance.commit_transaction(send_to_vlm=True)

        # Get the latest message (which should be the assistant's response)
        role, response = conversation_instance.get_latest_message()

        # Assert that the response is valid
        assert role == Role.ASSISTANT
        assert response is not None
        assert isinstance(response, str)
        assert len(response) > 10, "The response from the API was unexpectedly short."

        print(f"\n[SUCCESS] Gemini API responded: '{response}'")
