from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from autohub.database import model
from autohub.database.connection import get_db
from autohub.model.schemas import User
from autohub.api.login import get_current_user
from passlib.context import CryptContext

router = APIRouter(tags=["Users"])


pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")

@router.get("/status")
def get_status():
    return {"status": "API is running"}

# Signup
@router.post("/signup", status_code=status.HTTP_201_CREATED)
def sign_up(request: User, db: Session = Depends(get_db)):

    existing_user = db.query(model.User).filter(
        model.User.email == request.email
    ).first()

    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_password = pwd_context.hash(request.password)

    new_user = model.User(
        name=request.name,
        email=request.email,
        gender=request.gender,
        location=request.location,
        hashed_password=hashed_password,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {"message": f"User {request.name} signed up successfully"}

# Get All User
@router.get("/users")
def get_users(
    db: Session = Depends(get_db),
    _: model.User = Depends(get_current_user),
):
    return db.query(model.User).all()

# Delete User
@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    _: model.User = Depends(get_current_user),
):
    user = db.query(model.User).filter(
        model.User.id == user_id
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    db.delete(user)
    db.commit()

    return {"message": f"User with id {user_id} deleted successfully"}


# Update User

@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    request: User,
    db: Session = Depends(get_db),
    _: model.User = Depends(get_current_user),
):
    user = db.query(model.User).filter(
        model.User.id == user_id
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    data = request.model_dump()
    data.pop("id", None)
    password = data.pop("password", None)

    for key, value in data.items():
        if hasattr(user, key):
            setattr(user, key, value)

    if password is not None:
        user.hashed_password = pwd_context.hash(password)

    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email already in use")

    return {"message": f"User with id {user_id} updated successfully"}