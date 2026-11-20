from app.schemas.ai import AnalysisOutput, ChatOutput, SummaryOutput
from app.schemas.chat import Symptoms


class MockAIProvider:
    source = "MOCK"

    async def process_chat(self, request):
        return ChatOutput(
            symptom_updates=Symptoms(),
            answered_fields=[],
            next_question="[MOCK] 증상을 조금 더 설명해 주세요.",
        )

    async def analyze_symptoms(self, symptoms):
        return AnalysisOutput(
            assessment_status="INSUFFICIENT_EVIDENCE",
            symptom_summary="[MOCK] AI 통신을 수행하지 않은 흐름 테스트 결과입니다.",
            suspected_diseases=[],
            recommended_department=None,
        )

    async def summarize_medication(self, medication):
        return SummaryOutput(summary=[medication["efficacy"], medication["usage"]])

    async def close(self):
        pass
