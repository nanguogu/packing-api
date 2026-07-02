"""FastAPI application entry point.

Initializes the database on startup, registers routers, and serves
the application via uvicorn.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.database import Base, engine
from app.routers import pack, products


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
