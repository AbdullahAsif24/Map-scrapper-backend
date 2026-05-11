from dotenv import load_dotenv
load_dotenv()

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Float, Integer, DateTime, ForeignKey
import datetime, os

_url = os.getenv("DATABASE_URL")
if not _url:
    raise ValueError("DATABASE_URL is not set in .env")

DATABASE_URL = _url.replace("postgresql://", "postgresql+asyncpg://")

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"statement_cache_size": 0}
)

class Base(DeclarativeBase):
    pass

class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_token: Mapped[str] = mapped_column(String, index=True)
    query: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

class Listing(Base):
    __tablename__ = "listings"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("scrape_jobs.id"))
    name: Mapped[str | None] = mapped_column(String)
    address: Mapped[str | None] = mapped_column(String)
    phone: Mapped[str | None] = mapped_column(String)
    website: Mapped[str | None] = mapped_column(String)
    hours_status: Mapped[str | None] = mapped_column(String)
    category: Mapped[str | None] = mapped_column(String)
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(String)