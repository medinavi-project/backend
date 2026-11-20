from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.exceptions import BusinessException
from app.core.response_codes import Code
from app.schemas.ai import ChatInput
from app.schemas.chat import (
    CreateResponse,
    Message,
    MessageResponse,
    Receipt,
    Result,
    Session,
    Status,
)


class ChatService:
    def __init__(self, repository, provider, analysis, settings):
        self.repository, self.provider, self.analysis, self.settings = (
            repository,
            provider,
            analysis,
            settings,
        )

    async def apply_turn(self, session, message):
        messages = session.messages + [Message(role="user", content=message)]
        output = await self.provider.process_chat(
            ChatInput(messages=messages, symptoms=session.symptoms)
        )
        updates = output.symptom_updates.model_dump()
        for field in output.answered_fields:
            value = updates[field]
            if value is not None:
                setattr(session.symptoms, field, value)
                session.answered_fields.add(field)
        session.messages = messages + [
            Message(role="assistant", content=output.next_question)
        ]
        session.turn_count += 1
        session.updated_at = datetime.now(UTC)
        required = {
            "main_symptom",
            "body_location",
            "onset",
            "severity",
            "additional_symptoms",
        }
        # AI triage and diagnostic algorithms are deliberately outside this prototype.
        finish = (
            required <= session.answered_fields
            or session.turn_count >= self.settings.MAX_CHAT_TURNS
        )
        return output.next_question, finish

    def response(self, session, question):
        return MessageResponse(
            session_id=session.session_id,
            status=session.status,
            next_question=question if session.status == Status.IN_PROGRESS else None,
            turn_count=session.turn_count,
            collected_symptoms=session.symptoms,
            result_available=session.result is not None,
        )

    async def finish(self, session, token=None):
        session.status = Status.ANALYZING
        if token:
            await self.repository.save(session, token)
        try:
            session.result = await self.analysis.analyze(session)
            session.status = Status.COMPLETED
        except BusinessException as exc:
            session.status, session.failure_code = Status.FAILED, exc.code
            if token:
                await self.repository.save(session, token)
            raise
        except Exception:  # noqa: BLE001 - persist terminal failure before returning safe error
            session.status, session.failure_code = (
                Status.FAILED,
                Code.AI_ANALYSIS_FAILED,
            )
            if token:
                await self.repository.save(session, token)
            raise BusinessException(Code.AI_ANALYSIS_FAILED) from None

    async def create(self, request):
        now = datetime.now(UTC)
        session = Session(session_id=uuid4(), created_at=now, updated_at=now)
        question, finish = await self.apply_turn(session, request.initial_message)
        if finish:
            await self.finish(session)
        session.updated_at = datetime.now(UTC)
        await self.repository.create(session)
        return CreateResponse(
            session_id=session.session_id,
            status=session.status,
            next_question=question if session.status == Status.IN_PROGRESS else None,
            turn_count=session.turn_count,
            expires_at=session.updated_at
            + timedelta(seconds=self.settings.SESSION_TTL_SECONDS),
        )

    async def message(self, session_id, request):
        async with self.repository.locked(session_id) as token:
            session = await self.repository.get(session_id)
            message_id = str(request.client_message_id)
            if receipt := session.receipts.get(message_id):
                if receipt.message != request.message:
                    raise BusinessException(Code.RESOURCE_CONFLICT)
                return receipt.response
            if session.status != Status.IN_PROGRESS:
                code = {
                    Status.COMPLETED: Code.SESSION_ALREADY_COMPLETED,
                    Status.ANALYZING: Code.SESSION_ANALYSIS_IN_PROGRESS,
                }.get(session.status, Code.SESSION_NOT_ACTIVE)
                raise BusinessException(code)
            question, finish = await self.apply_turn(session, request.message)
            if finish:
                await self.finish(session, token)
            response = self.response(session, question)
            session.receipts[message_id] = Receipt(
                message=request.message, response=response
            )
            session.updated_at = datetime.now(UTC)
            await self.repository.save(session, token)
            return response

    async def result(self, session_id):
        session = await self.repository.get(session_id)
        return session.result or Result(
            session_id=session.session_id,
            status=session.status,
            failure_code=session.failure_code,
        )
