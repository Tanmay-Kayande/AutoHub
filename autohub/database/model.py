from sqlalchemy import Column, Float, Integer, String, ForeignKey, UniqueConstraint
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


class CarBrand(Base):
    __tablename__ = "car_brands"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)

    models = relationship(
        "CarModel",
        back_populates="brand",
        cascade="all, delete-orphan"
    )


class CarModel(Base):
    __tablename__ = "car_models"

    id = Column(Integer, primary_key=True, autoincrement=True)

    name = Column(String(100), nullable=False)
    brand_id = Column(Integer, ForeignKey("car_brands.id"), nullable=False)

    body_type = Column(String(50))
    launch_date = Column(String(20))
    description = Column(String(500))

    brand = relationship("CarBrand", back_populates="models")

    variants = relationship(
        "CarVariant",
        back_populates="model",
        cascade="all, delete-orphan"
    )

    images = relationship(
        "CarImage",
        back_populates="model",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("name", "brand_id", name="uq_model_brand"),
    )


class CarVariant(Base):
    __tablename__ = "car_variants"

    id = Column(Integer, primary_key=True, autoincrement=True)

    model_id = Column(Integer, ForeignKey("car_models.id"), nullable=False)

    variant_name = Column(String(120), nullable=False)

    # RAW, LOSSLESS VALUES
    fuel_type = Column(String(50))
    transmission = Column(String(120))
    price = Column(Float)

    # Future lookup tables (not used yet)
    fuel_type_id = Column(Integer, nullable=True)
    transmission_id = Column(Integer, nullable=True)

    model = relationship("CarModel", back_populates="variants")

    specs = relationship(
        "CarSpec",
        back_populates="variant",
        uselist=False,
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("model_id", "variant_name", name="uq_model_variant"),
    )


class CarSpec(Base):
    __tablename__ = "car_specs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    variant_id = Column(
        Integer,
        ForeignKey("car_variants.id"),
        unique=True,
        nullable=False
    )

    engine = Column(String(100))
    engine_capacity = Column(Float)
    power = Column(String(50))
    torque = Column(String(50))
    mileage = Column(Float)

    variant = relationship("CarVariant", back_populates="specs")


class CarImage(Base):
    __tablename__ = "car_images"

    id = Column(Integer, primary_key=True, autoincrement=True)

    image_url = Column(String(300), nullable=False)
    model_id = Column(Integer, ForeignKey("car_models.id"), nullable=False)

    model = relationship("CarModel", back_populates="images")


class News(Base):
    __tablename__ = "news"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    content = Column(String(2000), nullable=False)
    published_at = Column(String(30), nullable=False)

    news_images = relationship(
        "NewsImage",
        back_populates="news",
        cascade="all, delete-orphan"
    )


class NewsImage(Base):
    __tablename__ = "news_images"

    id = Column(Integer, primary_key=True, autoincrement=True)
    image_url = Column(String(300), nullable=False)
    news_id = Column(Integer, ForeignKey("news.id"), nullable=False)

    news = relationship("News", back_populates="news_images")
