from fastapi import FastAPI
# Import config FIRST (ensures env is loaded)
from autohub.core import config
from autohub.api import catalog, login, news
from autohub.api.routes import router
from autohub.database.model import Base
from autohub.database.connection import engine

app = FastAPI(title="AutoHub API")

Base.metadata.create_all(bind=engine)

@app.get("/")
def home():
    return {"message": "Welcome to AutoHub"}

# Router order matters
app.include_router(login.router)
app.include_router(router)
app.include_router(catalog.router)
app.include_router(news.router)
