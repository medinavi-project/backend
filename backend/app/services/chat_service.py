from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import ValidationError

from app.core.exceptions import BusinessException
from app.core.response_codes import Code
from app.schemas.ai import ChatInput, ChatOutput
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
        request = ChatInput(
            messages=messages,
            symptoms=session.symptoms,
            answered_fields=sorted(session.answered_fields),
            turn_count=session.turn_count + 1,
            max_turns=self.settings.MAX_CHAT_TURNS,
            engine_state=session.engine_state,
        )
        output = await self.provider.process_chat(request)
        # Enforce the same contract for every provider, including external adapters.
        try:
            output = ChatOutput.model_validate(
                output.model_dump(exclude_unset=True),
                context=request.model_dump(),
            )
        except ValidationError:
            raise BusinessException(Code.AI_RESPONSE_INVALID) from None
        for field, value in output.symptom_updates.model_dump(
            exclude_unset=True
        ).items():
            if value is not None:
                setattr(session.symptoms, field, value)
        session.answered_fields.update(output.answered_fields)
        if output.engine_state is not None:
            session.engine_state = output.engine_state
        session.messages = messages
        session.turn_count = request.turn_count
        session.updated_at = datetime.now(UTC)
        if output.action == "EMERGENCY":
            session.status = Status.EMERGENCY
            session.emergency_message = output.emergency_guidance
        elif output.action == "COMPLETE":
            session.result = self.analysis.result(
                session, output.analysis, output.references
            )
            session.status = Status.COMPLETED
        else:
            session.status = Status.IN_PROGRESS
            session.messages.append(
                Message(role="assistant", content=output.next_question)
            )
        return output.next_question

    def response(self, session, question):
        return MessageResponse(
            session_id=session.session_id,
            status=session.status,
            next_question=question if session.status == Status.IN_PROGRESS else None,
            turn_count=session.turn_count,
            collected_symptoms=session.symptoms,
            result_available=session.result is not None,
            emergency_message=session.emergency_message,
        )

    async def create(self, request):
        now = datetime.now(UTC)
        session = Session(session_id=uuid4(), created_at=now, updated_at=now)
        question = await self.apply_turn(session, request.initial_message)
        session.updated_at = datetime.now(UTC)
        await self.repository.create(session)
        return CreateResponse(
            session_id=session.session_id,
            status=session.status,
            next_question=question if session.status == Status.IN_PROGRESS else None,
            turn_count=session.turn_count,
            expires_at=session.updated_at
            + timedelta(seconds=self.settings.SESSION_TTL_SECONDS),
            emergency_message=session.emergency_message,
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
            session.status = Status.ANALYZING
            await self.repository.save(session, token)
            try:
                question = await self.apply_turn(session, request.message)
            except BusinessException as exc:
                session.status, session.failure_code = Status.FAILED, exc.code
                await self.repository.save(session, token)
                raise
            except Exception:  # noqa: BLE001 - persist safe failure before returning
                session.status, session.failure_code = (
                    Status.FAILED,
                    Code.AI_REQUEST_FAILED,
                )
                await self.repository.save(session, token)
                raise BusinessException(Code.AI_REQUEST_FAILED) from None
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
