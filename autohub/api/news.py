from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from autohub.database import model
from autohub.database.connection import get_db
from autohub.model.schemas import NewsCreate, NewsRead, NewsUpdate

router = APIRouter(
    prefix="/news",
    tags=["news"],
)

# CREATE NEWS
@router.post("/", response_model=NewsRead, status_code=status.HTTP_201_CREATED)
def create_news(request: NewsCreate, db: Session = Depends(get_db)):

    data = request.model_dump()
    image_data = data.pop("news_images", [])

    new_news = model.News(**data)

    # Add images
    for img in image_data:
        image_obj = model.NewsImage(image_url=img.image_url)
        new_news.news_images.append(image_obj)

    try:
        db.add(new_news)
        db.commit()
        db.refresh(new_news)
        return new_news

    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="News with this title already exists")

    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Error while creating news")
    
# GET ALL NEWS
@router.get("/", response_model=list[NewsRead])
def get_news(db: Session = Depends(get_db)):
    return db.query(model.News).all()

# UPDATE NEWS
@router.put("/{news_id}", response_model=NewsRead)
def update_news(news_id: int, request: NewsUpdate, db: Session = Depends(get_db)):

    news_item = db.query(model.News).filter(model.News.id == news_id).first()

    if not news_item:
        raise HTTPException(status_code=404, detail="News not found")

    data = request.model_dump(exclude_unset=True, exclude_none=True)
    image_data = data.pop("news_images", None)

    # Update basic fields
    for key, value in data.items():
        setattr(news_item, key, value)

    # If new images provided, append them
    if image_data is not None:
        for img in image_data:
            image_obj = model.NewsImage(image_url=img.image_url)
            news_item.news_images.append(image_obj)

    try:
        db.commit()
        db.refresh(news_item)
        return news_item

    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Duplicate title detected")

    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Error while updating news")
    
# DELETE NEWS
@router.delete("/{news_id}", status_code=status.HTTP_200_OK)
def delete_news(news_id: int, db: Session = Depends(get_db)):

    news_item = db.query(model.News).filter(model.News.id == news_id).first()

    if not news_item:
        raise HTTPException(status_code=404, detail="News not found")

    try:
        db.delete(news_item)
        db.commit()
        return {"message": f"News with id {news_id} deleted successfully"}

    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Error while deleting news")