from fastapi import FastAPI
from contextlib import asynccontextmanager
from autohub.api import catalog, login, news
from autohub.api.users import router as users_router
from autohub.api.routes import router
from autohub.database.model import Base
from autohub.database.connection import engine
from autohub.automation.scheduler import start_scheduler, stop_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):

    start_scheduler()
    yield

    stop_scheduler()

app = FastAPI(title="AutoHub API", lifespan=lifespan)

Base.metadata.create_all(bind=engine)

@app.get("/")
def home():
    return {"message": "Welcome to AutoHub"}

app.include_router(login.router)
app.include_router(router)
app.include_router(users_router)
app.include_router(catalog.router)
app.include_router(news.router)