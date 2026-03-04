from fastapi import FastAPI
# Import config FIRST (ensures env is loaded)
from autohub.api import catalog, login, news, users
from autohub.database.model import Base
from autohub.database.connection import engine

app = FastAPI(title="AutoHub API")

Base.metadata.create_all(bind=engine)

@app.get("/")
def home():
    return {"message": "Welcome to AutoHub"}

# Router order matters
app.include_router(login.router)
app.include_router(users.router)
app.include_router(catalog.router)
app.include_router(news.router)
 