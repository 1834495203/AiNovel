"""Optimistic JSON aggregate storage, independent of old writing command payloads."""

from sqlalchemy import Integer, String, delete, select, update
from sqlalchemy.orm import Mapped, mapped_column

from ainovel.core.errors import ConflictError
from ainovel.core.studio import StudioNovel
from ainovel.core.studio_templates import StudioFieldTemplate
from ainovel.mapper.models import Base
from ainovel.mapper.types import ModelJSON


class StudioRow(Base):
    __tablename__ = "studio_novels"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[StudioNovel] = mapped_column(ModelJSON(StudioNovel))


class StudioTemplateRow(Base):
    __tablename__ = "studio_field_templates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[StudioFieldTemplate] = mapped_column(ModelJSON(StudioFieldTemplate))


class StudioRepository:
    def __init__(self, store):
        self.store = store

    async def initialize(self):
        async with self.store.engine.begin() as connection:
            await connection.run_sync(lambda conn: StudioRow.__table__.create(conn, checkfirst=True))
            await connection.run_sync(lambda conn: StudioTemplateRow.__table__.create(conn, checkfirst=True))

    async def templates(self):
        async with self.store.sessions() as session:
            return list((await session.scalars(select(StudioTemplateRow.payload))).all())

    async def template(self, key):
        async with self.store.sessions() as session:
            value = await session.scalar(select(StudioTemplateRow.payload).where(StudioTemplateRow.id == key))
            if value is None:
                raise ValueError("字段模板不存在")
            return value

    async def save_template(self, value, *, create=False):
        candidate = value.model_copy(update={"version": value.version + 1}, deep=True)
        async with self.store.sessions.begin() as session:
            if create:
                session.add(StudioTemplateRow(id=candidate.id, version=candidate.version, payload=candidate))
            else:
                result = await session.execute(
                    update(StudioTemplateRow)
                    .where(StudioTemplateRow.id == value.id, StudioTemplateRow.version == value.version)
                    .values(version=candidate.version, payload=candidate)
                )
                if result.rowcount != 1:
                    raise ConflictError("模板已被修改，请刷新后再保存")
        return candidate

    async def delete_template(self, key, version):
        async with self.store.sessions.begin() as session:
            result = await session.execute(
                delete(StudioTemplateRow).where(
                    StudioTemplateRow.id == key, StudioTemplateRow.version == version
                )
            )
            if result.rowcount != 1:
                raise ConflictError("模板已变化，请刷新")

    async def list(self):
        async with self.store.sessions() as session:
            return list((await session.scalars(select(StudioRow.payload))).all())

    async def get(self, key):
        async with self.store.sessions() as session:
            value = await session.scalar(select(StudioRow.payload).where(StudioRow.id == key))
            if value is None:
                raise ValueError("小说不存在")
            return value

    async def create(self):
        value = StudioNovel()
        async with self.store.sessions.begin() as session:
            session.add(StudioRow(id=value.id, version=0, payload=value))
        return value

    async def save(self, value):
        expected = value.version
        candidate = value.model_copy(update={"version": expected + 1}, deep=True)
        async with self.store.sessions.begin() as session:
            result = await session.execute(
                update(StudioRow)
                .where(StudioRow.id == value.id, StudioRow.version == expected)
                .values(version=expected + 1, payload=candidate)
            )
            if result.rowcount != 1:
                raise ConflictError("资料已被其他请求更新，请刷新")
        value.version = expected + 1

    async def purge(self, key, version):
        async with self.store.sessions.begin() as session:
            row = await session.scalar(select(StudioRow).where(StudioRow.id == key).with_for_update())
            if row is None or row.version != version or row.payload.busy or not row.payload.deleted:
                raise ConflictError("请先停止任务并移入回收站，再永久删除")
            await session.execute(delete(StudioRow).where(StudioRow.id == key))
