from app.ai.providers.mock_provider import MockAIProvider


def create_provider(settings):
    if settings.AI_PROVIDER == "mock":
        return MockAIProvider()
    from app.ai.providers.clova_triage_provider import ClovaTriageProvider

    return ClovaTriageProvider(settings)
