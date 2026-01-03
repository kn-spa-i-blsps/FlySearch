import pytest
import numpy as np

from PIL import Image
from conversation.openai_conversation import OpenAIConversation
from conversation.abstract_conversation import Role


class SimpleObject:
    pass


class MockOpenAI:
    def mock_create_function(self, to_return: str) -> callable:
        response_mock = SimpleObject()
        response_mock.__dict__["choices"] = [SimpleObject()]

        choice = response_mock.choices[0]  # type: ignore
        choice.__dict__["message"] = SimpleObject()

        message = choice.message
        message.__dict__["content"] = to_return

        def mocked_fun(*args, **kwargs):
            self.mock_create_args.append(args)
            self.mock_create_kwargs.append(kwargs)

            messages = kwargs["messages"]
            self.mock_create_messages.append(list(messages))
            return response_mock

        return mocked_fun

    def __init__(self, api_key, response: str = "mocked_response"):
        chat = SimpleObject()
        chat.__dict__["completions"] = SimpleObject()

        completions = chat.completions  # type: ignore
        completions.__dict__["create"] = self.mock_create_function(response)

        self.chat = chat

        self.mock_create_args = []
        self.mock_create_kwargs = []
        self.mock_create_messages = []

    def get_mock_create_args(self):
        return self.mock_create_args

    def get_mock_create_kwargs(self):
        return self.mock_create_kwargs

    def get_mock_create_messages(self):
        return self.mock_create_messages


class TestOpenAIConversation:
    def test_begin_transaction_throws_if_already_started(self):
        openai_mock = MockOpenAI("mock_key")
        conversation = OpenAIConversation(
            openai_mock,  # type: ignore
            model_name="mock_model",
            seed=3,
            max_tokens=15
        )

        conversation.begin_transaction(Role.USER)

        with pytest.raises(Exception):
            conversation.begin_transaction(Role.USER)

        with pytest.raises(Exception):
            conversation.begin_transaction(Role.ASSISTANT)

        conversation.commit_transaction()

        conversation.begin_transaction(Role.ASSISTANT)

        with pytest.raises(Exception):
            conversation.begin_transaction(Role.USER)

        with pytest.raises(Exception):
            conversation.begin_transaction(Role.ASSISTANT)

    def test_commit_transaction_throws_if_not_started(self):
        openai_mock = MockOpenAI("mock_key")

        conversation = OpenAIConversation(
            openai_mock,  # type: ignore
            model_name="mock_model",
            seed=3,
            max_tokens=15
        )

        with pytest.raises(Exception):
            conversation.commit_transaction()

    def test_commit_transaction_does_not_send_to_vlm_by_default(self):
        openai_mock = MockOpenAI("mock_key")

        conversation = OpenAIConversation(
            openai_mock,  # type: ignore
            model_name="mock_model",
            seed=3,
            max_tokens=15
        )

        img = Image.new("RGB", (100, 100))

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message")
        conversation.add_image_message(img)
        conversation.commit_transaction()

        conversation.begin_transaction(Role.ASSISTANT)
        conversation.add_text_message("mock_response")
        conversation.commit_transaction()

        assert len(openai_mock.get_mock_create_args()) == 0
        assert len(openai_mock.get_mock_create_kwargs()) == 0

    def test_arguments_are_only_passed_via_kwargs(self):
        # To make testing simpler.
        openai_mock = MockOpenAI("mock_key")

        conversation = OpenAIConversation(
            openai_mock,  # type: ignore
            model_name="mock_model",
            seed=3,
            max_tokens=15
        )

        img = Image.new("RGB", (100, 100))

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message")
        conversation.add_image_message(img)
        conversation.commit_transaction(send_to_vlm=True)

        args = openai_mock.get_mock_create_args()[0]
        kwargs = openai_mock.get_mock_create_kwargs()[0]

        assert len(args) == 0

    def test_commit_transaction_throws_if_assistant_message_sent_to_vlm(self):
        openai_mock = MockOpenAI("mock_key")

        conversation = OpenAIConversation(
            openai_mock,  # type: ignore
            model_name="mock_model",
            seed=3,
            max_tokens=15
        )

        conversation.begin_transaction(Role.ASSISTANT)

        with pytest.raises(Exception):
            conversation.commit_transaction(send_to_vlm=True)

    def test_commit_transaction_sends_to_vlm_if_specified(self):
        openai_mock = MockOpenAI("mock_key")

        conversation = OpenAIConversation(
            openai_mock,  # type: ignore
            model_name="mock_model",
            seed=3,
            max_tokens=15
        )

        img = Image.new("RGB", (100, 100))

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message")
        conversation.add_image_message(img)
        conversation.commit_transaction(send_to_vlm=True)

        user_args = openai_mock.get_mock_create_args()[0]
        user_kwargs = openai_mock.get_mock_create_kwargs()[0]

        # Assuming all arguments are passed as kwargs
        user_message = user_kwargs["messages"][0]
        user_content = user_message["content"]

        assert len(openai_mock.get_mock_create_args()) == 1
        assert len(openai_mock.get_mock_create_kwargs()) == 1

        assert len(user_args) == 0

        assert user_kwargs["model"] == "mock_model"
        assert user_kwargs["max_tokens"] == 15
        assert user_message["role"] == "user"
        assert user_content[0]["type"] == "text"
        assert user_content[0]["text"] == "mock_message"
        assert user_content[1]["type"] == "image_url"

    def test_commit_transaction_keeps_history_of_conversation(self):
        openai_mock = MockOpenAI("mock_key")

        conversation = OpenAIConversation(
            openai_mock,  # type: ignore
            model_name="mock_model",
            seed=3,
            max_tokens=15
        )

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message")
        conversation.commit_transaction(send_to_vlm=True)

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message2")
        conversation.commit_transaction(send_to_vlm=True)

        messages = openai_mock.get_mock_create_messages()

        assert len(messages) == 2

        messages_first = messages[0]
        messages_second = messages[1]

        assert messages_first[0] == messages_second[0]
        assert messages_second[0]["role"] == "user"
        assert messages_second[1]["role"] == "assistant"
        assert messages_second[2]["role"] == "user"

        assert messages_second[0]["content"][0]["text"] == "mock_message"
        assert messages_second[1]["content"] == "mocked_response"
        assert messages_second[2]["content"][0]["text"] == "mock_message2"

    def test_rollback_transaction_clears_current_transaction(self):
        openai_mock = MockOpenAI("mock_key")

        conversation = OpenAIConversation(
            openai_mock,  # type: ignore
            model_name="mock_model",
            seed=3,
            max_tokens=15
        )

        img = Image.new("RGB", (100, 100))

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message")
        conversation.add_image_message(img)
        conversation.rollback_transaction()

        assert len(openai_mock.get_mock_create_args()) == 0
        assert len(openai_mock.get_mock_create_kwargs()) == 0

        with pytest.raises(Exception):
            conversation.commit_transaction()

    def test_get_conversation_returns_simplified_conversation_history(self):
        openai_mock = MockOpenAI("mock_key")

        conversation = OpenAIConversation(
            openai_mock,  # type: ignore
            model_name="mock_model",
            seed=3,
            max_tokens=15
        )

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message")
        conversation.commit_transaction(send_to_vlm=True)

        conversation.begin_transaction(Role.ASSISTANT)
        conversation.add_text_message("hello there")
        conversation.commit_transaction(send_to_vlm=False)

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("whooopsie")
        conversation.rollback_transaction()

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message2")
        conversation.commit_transaction(send_to_vlm=False)

        history = conversation.get_conversation()

        assert history == [
            (Role.USER, "mock_message"),
            (Role.ASSISTANT, "mocked_response"),
            (Role.ASSISTANT, "hello there"),
            (Role.USER, "mock_message2")
        ]

    def test_get_latest_message_returns_last_message(self):
        openai_mock = MockOpenAI("mock_key")

        conversation = OpenAIConversation(
            openai_mock,  # type: ignore
            model_name="mock_model",
            seed=3,
            max_tokens=15
        )

        latest_messages = []

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message")
        conversation.commit_transaction(send_to_vlm=True)

        latest_messages.append(conversation.get_latest_message())  # mocked_response from OpenAI

        conversation.begin_transaction(Role.ASSISTANT)
        conversation.add_text_message("hello there")
        conversation.commit_transaction(send_to_vlm=False)

        latest_messages.append(conversation.get_latest_message())  # hello there

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("whooopsie")
        conversation.rollback_transaction()

        latest_messages.append(conversation.get_latest_message())  # still hello there

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message2")
        conversation.commit_transaction(send_to_vlm=False)

        latest_messages.append(conversation.get_latest_message())  # mock_message2

        assert latest_messages == [
            (Role.ASSISTANT, "mocked_response"),
            (Role.ASSISTANT, "hello there"),
            (Role.ASSISTANT, "hello there"),
            (Role.USER, "mock_message2")
        ]

    def test_get_latest_message_throws_if_no_messages(self):
        openai_mock = MockOpenAI("mock_key")

        conversation = OpenAIConversation(
            openai_mock,  # type: ignore
            model_name="mock_model",
            seed=3,
            max_tokens=15
        )

        with pytest.raises(Exception):
            conversation.get_latest_message()

    def test_get_latest_message_throws_if_no_messages_after_rollback(self):
        openai_mock = MockOpenAI("mock_key")

        conversation = OpenAIConversation(
            openai_mock,  # type: ignore
            model_name="mock_model",
            seed=3,
            max_tokens=15
        )

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message")
        conversation.rollback_transaction()

        with pytest.raises(Exception):
            conversation.get_latest_message()

    def test_get_entire_conversation_returns_all_messages_in_openai_format(self):
        openai_mock = MockOpenAI("mock_key")

        conversation = OpenAIConversation(
            openai_mock,  # type: ignore
            model_name="mock_model",
            seed=3,
            max_tokens=15
        )

        img = Image.new("RGB", (100, 100))

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message")
        conversation.add_image_message(img)
        conversation.commit_transaction(send_to_vlm=True)

        conversation.begin_transaction(Role.ASSISTANT)
        conversation.add_text_message("hello there")
        conversation.commit_transaction(send_to_vlm=False)

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message2")
        conversation.commit_transaction(send_to_vlm=False)

        conversation.begin_transaction(Role.USER)
        conversation.add_text_message("mock_message3")
        conversation.add_image_message(img)
        conversation.commit_transaction(send_to_vlm=True)

        entire_conversation = conversation.get_entire_conversation()

        assert entire_conversation[-1]["role"] == "assistant"
        entire_conversation_without_assistant = entire_conversation[:-1]

        assert entire_conversation_without_assistant == openai_mock.get_mock_create_messages()[-1]

    def test_images_are_sent_as_base64_jpeg(self):
        openai_mock = MockOpenAI("mock_key")

        conversation = OpenAIConversation(
            openai_mock,  # type: ignore
            model_name="mock_model",
            seed=3,
            max_tokens=15
        )

        # Create a simple 20x20 test image with a deterministic pattern
        # Create a checkerboard pattern for predictable testing
        pattern = np.zeros((20, 20, 3), dtype=np.uint8)
        for i in range(20):
            for j in range(20):
                if (i + j) % 2 == 0:
                    pattern[i, j] = [255, 0, 0]  # Red squares
                else:
                    pattern[i, j] = [0, 255, 0]  # Green squares
        
        image_pil = Image.fromarray(pattern, mode='RGB')

        conversation.begin_transaction(Role.USER)
        conversation.add_image_message(image_pil)
        conversation.commit_transaction(send_to_vlm=True)

        messages = openai_mock.get_mock_create_messages()
        message = messages[0]
        content = message[0]["content"]

        b64_sent = content[0]["image_url"]["url"]

        # Check that the base64 string starts with the correct JPEG data URI prefix
        assert b64_sent.startswith("data:image/jpeg;base64,")
        
        # Check that the base64 portion is not empty
        base64_part = b64_sent.split(",")[1]
        assert len(base64_part) > 0
        
        # Verify it's valid base64 by attempting to decode it
        import base64
        try:
            decoded_bytes = base64.b64decode(base64_part)
            # Check that it starts with JPEG magic bytes
            assert decoded_bytes.startswith(b'\xff\xd8\xff')
        except Exception as e:
            pytest.fail(f"Invalid base64 or JPEG format: {e}")
