from fastapi import FastAPI
from autohub.api import cars, login, news
from autohub.api.routes import router
from autohub.model import schemas
from autohub.database.model import Base
from autohub.database.connection import engine

app = FastAPI()
Base.metadata.create_all(bind=engine)


@app.get("/")
def home():
    return {"message": "Welcome to AutoHub"}

app.include_router(router)
app.include_router(login.router)
app.include_router(cars.router)
app.include_router(news.router)