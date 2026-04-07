
import pytest
from PIL import Image
from mission_control.conversation.gemini.gemini_conversation import GeminiConversation
from mission_control.conversation.abstract_conversation import Role

class SimpleObject:
    pass

class MockGemini:
    def mock_send_message_function(self, to_return: str) -> callable:
        response_mock = SimpleObject()
        response_mock.__dict__["text"] = to_return

        def mocked_fun(*args, **kwargs):
            self.mock_send_message_args.append(args)
            self.mock_send_message_kwargs.append(kwargs)
            
            message = kwargs["message"]
            self.mock_send_message_messages.append(list(message))
            return response_mock

        return mocked_fun

    def __init__(self, api_key, response: str = "mocked_response"):
        self.response_text = response
        self.chats = SimpleObject()
        self.chats.__dict__["create"] = self.mock_create_function()

        self.mock_send_message_args = []
        self.mock_send_message_kwargs = []
        self.mock_send_message_messages = []

    def mock_create_function(self) -> callable:
        chat_mock = SimpleObject()
        chat_mock.__dict__["send_message"] = self.mock_send_message_function(self.response_text)
        
        def mocked_fun(*args, **kwargs):
            return chat_mock
        return mocked_fun

    def get_mock_send_message_args(self):
        return self.mock_send_message_args

    def get_mock_send_message_kwargs(self):
        return self.mock_send_message_kwargs

    def get_mock_send_message_messages(self):
        return self.mock_send_message_messages


class TestGeminiConversation:
    def test_begin_transaction_throws_if_already_started(self):
        gemini_mock = MockGemini("mock_key")
        conversation = GeminiConversation(
            gemini_mock,
            model_name="mock_model",
        )

        conversation.begin_transaction(Role.USER)

        with pytest.raises(Exception):
            conversation.begin_transaction(Role.USER)

    def test_commit_transaction_sends_to_vlm(self):
        gemini_mock = MockGemini("mock_key")
        conversation = GeminiConversation(
            gemini_mock,
            model_name="mock_model",
        )

        img = Image.new("RGB", (100, 100))

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message")
        conversation.add_image_message(img)
        conversation.commit_transaction(send_to_vlm=True)

        assert len(gemini_mock.get_mock_send_message_args()) == 1
        assert len(gemini_mock.get_mock_send_message_kwargs()) == 1
        
        sent_messages = gemini_mock.get_mock_send_message_messages()[0]
        assert len(sent_messages) == 2
        assert sent_messages[0].text == "mock_message"
        assert sent_messages[1].inline_data.mime_type == "image/jpeg"

    def test_rollback_transaction_clears_current_transaction(self):
        gemini_mock = MockGemini("mock_key")
        conversation = GeminiConversation(
            gemini_mock,
            model_name="mock_model",
        )

        img = Image.new("RGB", (100, 100))

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message")
        conversation.add_image_message(img)
        conversation.rollback_transaction()

        assert len(gemini_mock.get_mock_send_message_args()) == 0
        assert len(gemini_mock.get_mock_send_message_kwargs()) == 0

        with pytest.raises(Exception):
            conversation.commit_transaction(send_to_vlm=True)

    def test_get_latest_message_returns_last_message(self):
        gemini_mock = MockGemini("mock_key")
        conversation = GeminiConversation(
            gemini_mock,
            model_name="mock_model",
        )

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message")
        conversation.commit_transaction(send_to_vlm=True)

        latest_message = conversation.get_latest_message()
        assert latest_message == (Role.ASSISTANT, "mocked_response")

    def test_get_latest_message_throws_if_no_messages(self):
        gemini_mock = MockGemini("mock_key")
        conversation = GeminiConversation(
            gemini_mock,
            model_name="mock_model",
        )

        with pytest.raises(Exception):
            conversation.get_latest_message()
