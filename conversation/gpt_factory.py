import os

from openai import OpenAI, _types

from conversation.base_conversation_factory import BaseConversationFactory
from conversation.openai_conversation import OpenAIConversation


class GPTFactory(BaseConversationFactory):
    def __init__(self, model_name: str):
        self.client = OpenAI(api_key=os.environ["OPEN_AI_KEY"])
        self.model_name = model_name.removeprefix("oai-")

    def get_conversation(self):
        return OpenAIConversation(
            self.client,
            model_name=self.model_name,
            max_tokens=_types.NotGiven(),
            # We have to do this because otherwise GPT-5 would stop working. 4o works with default arguments for this class, but while making this compatible with GPT-5 I've decided to stop passing these arguments altogether as they don't break the 4o.
            temperature=_types.NotGiven()
        )
