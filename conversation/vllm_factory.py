import os

from openai import OpenAI

from conversation.openai_conversation import OpenAIConversation


class VLLMFactory:
    def __init__(self, model_name: str):
        self.client = OpenAI(
            api_key=os.environ["VLLM_KEY"],
            base_url=os.environ["VLLM_ADDRESS"]
        )

        self.model_name = model_name

    def get_conversation(self):
        return OpenAIConversation(
            self.client,
            model_name=self.model_name,
        )
