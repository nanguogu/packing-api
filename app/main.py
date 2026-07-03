"""FastAPI application entry point.

Initializes the database on startup, registers routers, and serves
the application via uvicorn.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import Base, engine
from app.routers import pack, products, shipping


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: create database tables on startup."""
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Packing Optimization & Logistics Recommendation API",
    version="0.1.0",
    description="MVP API for bin-packing optimization and shipping rate comparison",
    lifespan=lifespan,
)

app.include_router(pack.router)
app.include_router(products.router)
app.include_router(shipping.router)

frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/ui", StaticFiles(directory=frontend_dist, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
