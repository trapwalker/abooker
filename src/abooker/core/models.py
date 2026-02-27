import enum
import json
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class FileStatus(str, enum.Enum):
    pending = "pending"
    downloading = "downloading"
    cached = "cached"
    evicted = "evicted"


class AuthRequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    is_authorized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user_books: Mapped[list["UserBook"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    auth_requests: Mapped[list["AuthRequest"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    author: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(Text, nullable=False)
    torrent_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    total_size: Mapped[int] = mapped_column(BigInteger, default=0)
    warmup_count: Mapped[int] = mapped_column(Integer, default=0)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    files: Mapped[list["AudioFile"]] = relationship(back_populates="publication", cascade="all, delete-orphan")
    user_books: Mapped[list["UserBook"]] = relationship(back_populates="publication", cascade="all, delete-orphan")


class UserBook(Base):
    __tablename__ = "user_books"
    __table_args__ = (UniqueConstraint("user_id", "publication_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id"), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="user_books")
    publication: Mapped["Publication"] = relationship(back_populates="user_books")


class AuthRequest(Base):
    __tablename__ = "auth_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    status: Mapped[AuthRequestStatus] = mapped_column(
        Enum(AuthRequestStatus), default=AuthRequestStatus.pending, nullable=False
    )
    resolved_by: Mapped[int | None] = mapped_column(BigInteger)  # telegram_id of admin
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    _bot_message_ids: Mapped[str | None] = mapped_column("bot_message_ids", Text)

    user: Mapped["User"] = relationship(back_populates="auth_requests")

    @property
    def bot_message_ids(self) -> dict[str, int]:
        if self._bot_message_ids:
            return json.loads(self._bot_message_ids)
        return {}

    @bot_message_ids.setter
    def bot_message_ids(self, value: dict[str, int]) -> None:
        self._bot_message_ids = json.dumps(value)


class AudioFile(Base):
    __tablename__ = "audio_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id"), nullable=False)
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    url_path: Mapped[str] = mapped_column(String(64), nullable=False)   # e.g. "00.mp3"
    filename: Mapped[str] = mapped_column(Text, nullable=False)          # original filename on disk
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    qbt_file_index: Mapped[int | None] = mapped_column(Integer)          # index in qBittorrent files list
    status: Mapped[FileStatus] = mapped_column(
        Enum(FileStatus), default=FileStatus.pending, nullable=False
    )

    publication: Mapped["Publication"] = relationship(back_populates="files")
    download_events: Mapped[list["DownloadEvent"]] = relationship(back_populates="file", cascade="all, delete-orphan")


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text)
    first_seen: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    download_events: Mapped[list["DownloadEvent"]] = relationship(back_populates="client", cascade="all, delete-orphan")


class DownloadEvent(Base):
    __tablename__ = "download_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    file_id: Mapped[int] = mapped_column(ForeignKey("audio_files.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    client: Mapped["Client"] = relationship(back_populates="download_events")
    file: Mapped["AudioFile"] = relationship(back_populates="download_events")
