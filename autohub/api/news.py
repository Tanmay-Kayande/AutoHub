from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from autohub.database import model
from autohub.database.connection import get_db
from autohub.model.schemas import News, NewsUpdate
from sqlalchemy.exc import IntegrityError

router = APIRouter(
    prefix="/news",
    tags=["news"],
)

@router.post("/add_news")
def add_news(request: News, db: Session = Depends(get_db)):
    
    data = request.model_dump()
    image_urls = data.pop("news_images", [])

    new_news = model.News(**data)

    # accept either list[str] or list[dict] with {'image_url': str}
    for img in image_urls:
        if isinstance(img, dict):
            url = img.get("image_url")
        else:
            url = img
        if url:
            image_obj = model.NewsImage(image_url=url)
            new_news.news_images.append(image_obj)

    try:
        db.add(new_news)
        db.commit()
        db.refresh(new_news)

        return {
        "status": "success",
        "message": f"News '{request.title}' added successfully",
        "image_count": len(new_news.news_images)
         }
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="News with this title already exists")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="An unexpected error occurred while adding the news")
    
@router.get("/", response_model=list[News])
def get_news(db: Session = Depends(get_db)):
    news_items = db.query(model.News).all()
    return news_items

@router.put("/update_news/{news_id}")
def update_news(news_id: int, request: NewsUpdate, db: Session = Depends(get_db)):
    news_item = db.query(model.News).filter(model.News.id == news_id).first()
    if not news_item:
        raise HTTPException(status_code=404, detail="News item not found")
    
    data = request.model_dump(exclude_unset=True, exclude_none=True)
    data.pop("news_id", None)  # don't allow changing the primary key
    image_urls = data.pop("news_images", [])
    for key, value in data.items():
        if hasattr(news_item, key):
            setattr(news_item, key, value)
    
    for img in image_urls:
        if isinstance(img, dict):
            url = img.get("image_url")
        else:
            url = img
        if url:
            image_obj = model.NewsImage(image_url=url)
            news_item.news_images.append(image_obj)

    try:
        db.add(news_item)
        db.commit()
        db.refresh(news_item)
        return {
            "status": "success",
            "message": f"News item with id {news_id} updated successfully",
            "image_count": len(news_item.news_images)
        }
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="News with this title already exists")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="An unexpected error occurred while updating the news item")
    
@router.delete("/delete_news/{news_id}")
def delete_news(news_id: int, db: Session = Depends(get_db)):
    news_item = db.query(model.News).filter(model.News.id == news_id).first()
    if not news_item:
        raise HTTPException(status_code=404, detail="News item not found")
    
    try:
        db.delete(news_item)
        db.commit()
        return {"message": f"News item with id {news_id} deleted successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="An unexpected error occurred while deleting the news item")
