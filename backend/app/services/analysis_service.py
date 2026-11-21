from app.schemas.chat import Disease, Result, Status


class AnalysisService:
    """Map the unified LLM analysis to the unchanged public result DTO."""

    def __init__(self, provider):
        self.provider = provider

    def result(self, session, output):
        return Result(
            session_id=session.session_id,
            status=Status.COMPLETED,
            assessment_status=output.assessment_status,
            analysis_source=self.provider.source,
            symptom_summary=output.symptom_summary,
            suspected_diseases=[
                Disease(**d.model_dump()) for d in output.suspected_diseases
            ],
            recommended_department=output.recommended_department,
        )
