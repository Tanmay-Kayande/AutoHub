from sqlalchemy import Column, Float, Integer, String, ForeignKey
from autohub.database.connection import Base
from sqlalchemy.orm import relationship


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(50), nullable=False)
    email = Column(String(256), unique=True, index=True, nullable=False)
    gender = Column(String(10))
    location = Column(String(60))
    hashed_password = Column(String(200), nullable=False)
    

class Car(Base):
    __tablename__ = "cars"

     # Primary Key: Auto-increments automatically in SQLite, Postgres, MySQL
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    
    # Core Car Details
    car_name = Column(String(100), nullable=False)
    car_model = Column(String(100), nullable=False, unique=True) # Unique prevents duplicates
    car_brand = Column(String(100), nullable=False)
    car_type = Column(String(50), nullable=False)
    car_color = Column(String(50), nullable=False)
    car_launch_date = Column(String(10), nullable=False)
   
   # Technical Specs
    car_engine = Column(String(50), nullable=False)
    car_engine_capacity = Column(Float, nullable=False)
    car_torque = Column(Float, nullable=False)
    car_power = Column(Float, nullable=False)
    car_fuel = Column(String(50), nullable=False)
    car_mileage = Column(Float, nullable=False)
    car_transmission = Column(String(50), nullable=False)
    car_description = Column(String(500), nullable=True)
    car_price = Column(Float, nullable=False)

    # Relationship: Connects to the CarImage table
    # cascade="all, delete-orphan" means if you delete a car, its images are also deleted.
    car_images = relationship("CarImage", back_populates="car", cascade="all, delete-orphan")
    

class CarImage(Base):
    __tablename__ = "car_images"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    image_url = Column(String(300), nullable=False)
    car_id = Column(Integer, ForeignKey("cars.id"), nullable=False)
    car = relationship("Car", back_populates="car_images")

    # Back-reference to the Car object
    car = relationship("Car", back_populates="car_images")

class News(Base):
    __tablename__ = "news"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    content = Column(String(2000), nullable=False)
    published_at = Column(String(30), nullable=False)

    news_images = relationship("NewsImage", back_populates="news", cascade="all, delete-orphan")

class NewsImage(Base):
    __tablename__ = "news_images"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    image_url = Column(String(300), nullable=False)
    news_id = Column(Integer, ForeignKey("news.id"), nullable=False)

    news = relationship("News", back_populates="news_images") # Back-reference to the News object
    
