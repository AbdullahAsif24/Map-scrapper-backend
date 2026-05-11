import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import jwt, os, uuid, tempfile

from database import engine, Base, ScrapeJob, Listing
from scrapper import scrape_google_maps, save_to_excel, save_to_json

app = FastAPI(title="Map Scraper API")

SECRET = os.getenv("JWT_SECRET", "changeme")

# ── CORS ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic Models ───────────────────────────────────────────
class ScrapeRequest(BaseModel):
    query: str
    max_results: int = 20

class ExportRequest(BaseModel):
    results: List[dict]
    filename: Optional[str] = "exported_results"

# ── Startup: create tables ────────────────────────────────────
@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ── Session token helper ──────────────────────────────────────
def get_session_token(request: Request):
    token = request.headers.get("X-Session-Token")
    if not token:
        token = jwt.encode({"sid": str(uuid.uuid4())}, SECRET, algorithm="HS256")
    return token

# ── Health Check ──────────────────────────────────────────────
@app.get("/health")
def health_check():
    return {"status": "ok"}

# ── Route 1: Scrape ───────────────────────────────────────────
@app.post("/scrape")
async def scrape(request: ScrapeRequest, req: Request):
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    token = get_session_token(req)

    try:
        results = await scrape_google_maps(
            query=request.query,
            max_results=request.max_results
        )

        async with AsyncSession(engine) as session:
            job = ScrapeJob(session_token=token, query=request.query)
            session.add(job)
            await session.flush()
            job_id = job.id  # save before session closes
            for r in results:
                session.add(Listing(job_id=job_id, **r))
            await session.commit()

        return {
            "success": True,
            "session_token": token,
            "job_id": job_id,
            "query": request.query,
            "count": len(results),
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Route 2: Export ───────────────────────────────────────────
@app.post("/export")
def export(request: ExportRequest):
    if not request.results:
        raise HTTPException(status_code=400, detail="No results to export")

    try:
        filename = f"{request.filename or 'results'}_{uuid.uuid4().hex[:8]}.xlsx"
        filepath = os.path.join(tempfile.gettempdir(), filename)
        save_to_excel(request.results, filepath)
        return FileResponse(
            path=filepath,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=filename
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Route 3: History ──────────────────────────────────────────
@app.get("/history")
async def history(req: Request):
    token = req.headers.get("X-Session-Token")
    if not token:
        raise HTTPException(status_code=400, detail="No session token")
    try:
        async with AsyncSession(engine) as session:
            result = await session.execute(
                select(ScrapeJob)
                .where(ScrapeJob.session_token == token)
                .order_by(ScrapeJob.created_at.desc())
            )
            jobs = result.scalars().all()
            return {
                "jobs": [
                    {"id": j.id, "query": j.query, "created_at": j.created_at}
                    for j in jobs
                ]
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ======== Route 4 --------
@app.get("/history/{job_id}")
async def history_detail(job_id: int, req: Request):
    token = req.headers.get("X-Session-Token")
    if not token:
        raise HTTPException(status_code=400, detail="No session token")
    try:
        async with AsyncSession(engine) as session:
            # verify job belongs to this token
            job = await session.execute(
                select(ScrapeJob).where(ScrapeJob.id == job_id, ScrapeJob.session_token == token)
            )
            job = job.scalar_one_or_none()
            if not job:
                raise HTTPException(status_code=404, detail="Job not found")

            listings = await session.execute(
                select(Listing).where(Listing.job_id == job_id)
            )
            results = [
                {
                    "name": l.name,
                    "address": l.address,
                    "phone": l.phone,
                    "website": l.website,
                    "hours_status": l.hours_status,
                    "category": l.category,
                    "rating": l.rating,
                    "review_count": l.review_count,
                    "url": l.url,
                }
                for l in listings.scalars().all()
            ]
            return {"job_id": job_id, "query": job.query, "count": len(results), "results": results}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Run ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True, loop="asyncio")