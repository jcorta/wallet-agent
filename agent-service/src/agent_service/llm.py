from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from agent_service.settings import Settings


def build_chat_model(settings: Settings) -> BaseChatModel:
    model = init_chat_model(
        settings.llm_model,
        model_provider=settings.llm_provider,
        temperature=0,
        max_retries=settings.llm_max_retries,
    )
    # No configurable fields are requested, so this is always a concrete chat model.
    assert isinstance(model, BaseChatModel)
    return model
