from app.schemas.ai import AnalysisOutput, ChatOutput, SymptomUpdates


class MockAIProvider:
    source = "MOCK"

    async def process_chat(self, request):
        if request.turn_count >= request.max_turns:
            return ChatOutput(
                action="COMPLETE",
                symptom_updates=SymptomUpdates(),
                answered_fields=[],
                next_question=None,
                analysis=AnalysisOutput(
                    assessment_status="INSUFFICIENT_EVIDENCE",
                    symptom_summary="[MOCK] AI 통신을 수행하지 않은 흐름 테스트 결과입니다.",
                    suspected_diseases=[],
                    recommended_department=None,
                ),
            )
        return ChatOutput(
            action="ASK_FOLLOW_UP",
            symptom_updates=SymptomUpdates(),
            answered_fields=[],
            next_question="[MOCK] 증상을 조금 더 설명해 주세요.",
            analysis=None,
        )

    async def close(self):
        pass
