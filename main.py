import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv
import os

from fastapi.responses import FileResponse
import uuid
import tempfile


# Import
from scrapper import scrape_google_maps, save_to_excel, save_to_json

load_dotenv()

app = FastAPI(title="Map Scraper API")

# ── CORS (React frontend pe allow karne ke liye) ──────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic Models ───────────────────────────────────────────
class ScrapeRequest(BaseModel):
    query: str          # e.g. "restaurants in karachi"
    max_results: int = 20

class ExportRequest(BaseModel):
    results: List[dict]
    filename: Optional[str] = "exported_results"

# ── Health Check ──────────────────────────────────────────────
@app.get("/health")
def health_check():
    return {"status": "ok"}

# ── Route 1: Scrape ───────────────────────────────────────────
@app.post("/scrape")
async def scrape(request: ScrapeRequest):
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    
    try:
        results = await scrape_google_maps(
            query=request.query,
            max_results=request.max_results
        )
        return {
            "success": True,
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
    
# ── Run ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True, loop="asyncio")