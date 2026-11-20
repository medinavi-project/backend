from contextlib import asynccontextmanager
from uuid import uuid4

from app.core.exceptions import BusinessException
from app.core.response_codes import Code
from app.schemas.chat import Session

COMMIT = """
if redis.call('get', KEYS[2]) ~= ARGV[1] then return -1 end
if redis.call('exists', KEYS[1]) == 0 then return 0 end
redis.call('set', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""
RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end
return 0
"""


class SessionRepository:
    def __init__(self, redis, ttl=1800, lock_seconds=150):
        self.redis, self.ttl, self.lock_seconds = redis, ttl, lock_seconds

    def key(self, session_id):
        return f"medinavi:chat:session:{session_id}"

    async def create(self, session):
        if not await self.redis.set(
            self.key(session.session_id),
            session.model_dump_json(),
            ex=self.ttl,
            nx=True,
        ):
            raise BusinessException(Code.RESOURCE_CONFLICT)

    async def get(self, session_id):
        raw = await self.redis.get(self.key(session_id))
        if raw is None:
            raise BusinessException(Code.SESSION_NOT_FOUND)
        return Session.model_validate_json(raw)

    async def save(self, session, token):
        key = self.key(session.session_id)
        result = await self.redis.eval(
            COMMIT, 2, key, key + ":lock", token, session.model_dump_json(), self.ttl
        )
        if result == 0:
            raise BusinessException(Code.SESSION_NOT_FOUND)
        if result == -1:
            raise BusinessException(
                Code.RESOURCE_CONFLICT,
                "동시 처리 잠금이 만료되었습니다. 다시 시도하세요.",
            )

    @asynccontextmanager
    async def locked(self, session_id):
        key = self.key(session_id) + ":lock"
        token = str(uuid4())
        if not await self.redis.set(key, token, ex=self.lock_seconds, nx=True):
            session = await self.get(session_id)
            code = (
                Code.SESSION_ANALYSIS_IN_PROGRESS
                if session.status == "ANALYZING"
                else Code.RESOURCE_CONFLICT
            )
            raise BusinessException(code)
        try:
            yield token
        finally:
            await self.redis.eval(RELEASE, 1, key, token)
