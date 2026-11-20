from app.schemas.chat import Result, Status


class AnalysisService:
    def __init__(self, provider):
        self.provider = provider

    async def analyze(self, session):
        output = await self.provider.analyze_symptoms(session.symptoms)
        return Result(
            session_id=session.session_id,
            status=Status.COMPLETED,
            assessment_status=output.assessment_status,
            analysis_source=self.provider.source,
            symptom_summary=output.symptom_summary,
            suspected_diseases=output.suspected_diseases,
            recommended_department=output.recommended_department,
        )
