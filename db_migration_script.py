from sqlalchemy.orm import Session
from autohub.database.model import (
    CarBrand,
    CarModel,
    CarVariant,
    CarSpec,
    CarImage,
    User,
    News,
    NewsImage,
)

def __get_sqllite_session

def _read_sqllite_dump(file_path: str) -> list[dict]:


def write_car_payload(payload: dict, db: Session) -> None:
    pass
