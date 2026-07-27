from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from app.core.security import get_password_hash, verify_password, create_access_token, verify_firebase_token
from app.core.dependencies import get_current_user
from app.core.config import settings
from app.models.user import User
from app.schemas.user import UserCreate, UserOut, Token, UserBase
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/auth", tags=["Authentication"])

class UserLogin(BaseModel):
    email: str
    password: str

@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register_user(user_in: UserCreate):
    # Check if email exists
    existing_user = await User.find_one(User.email == user_in.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The user with this email already exists in the system.",
        )
    
    hashed_password = get_password_hash(user_in.password)
    user = User(
        email=user_in.email,
        hashed_password=hashed_password,
        full_name=user_in.full_name
    )
    await user.insert()
    
    # In Beanie, user.id is PydanticObjectId, we convert it to str
    return UserOut(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        created_at=user.created_at
    )

@router.post("/login", response_model=Token)
async def login_user(user_in: UserLogin):
    user = await User.find_one(User.email == user_in.email)
    if not user or not verify_password(user_in.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect email or password",
        )
    
    return Token(
        access_token=create_access_token(user.id),
        token_type="bearer"
    )

@router.post("/login-swagger", response_model=Token)
async def login_swagger(form_data: OAuth2PasswordRequestForm = Depends()):
    user = await User.find_one(User.email == form_data.username)
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect email or password",
        )
    
    return Token(
        access_token=create_access_token(user.id),
        token_type="bearer"
    )

@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return UserOut(
        id=str(current_user.id),
        email=current_user.email,
        full_name=current_user.full_name,
        is_active=current_user.is_active,
        created_at=current_user.created_at
    )

class FirebaseLoginRequest(BaseModel):
    id_token: str
    full_name: Optional[str] = None

@router.post("/firebase", response_model=Token)
async def login_firebase(login_data: FirebaseLoginRequest):
    if not settings.FIREBASE_PROJECT_ID:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Firebase Project ID is not configured on the server."
        )
        
    try:
        payload = verify_firebase_token(login_data.id_token, settings.FIREBASE_PROJECT_ID)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )
        
    email = payload.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Firebase ID token does not contain a verified email address."
        )
        
    # Find or create user
    user = await User.find_one(User.email == email)
    if not user:
        # Generate user's full name from Firebase claims or prompt details
        full_name = payload.get("name") or login_data.full_name or email.split("@")[0]
        
        # User created via Firebase doesn't need a local password hash
        user = User(
            email=email,
            hashed_password="",
            full_name=full_name,
            is_active=True
        )
        await user.insert()
        
    # Generate and return custom session access token
    return Token(
        access_token=create_access_token(user.id),
        token_type="bearer"
    )

# --- Personal Access Token (PAT) Schemas & Endpoints ---
from datetime import datetime
from typing import List
from bson import ObjectId
from app.models.pat import PersonalAccessToken

class PATCreateRequest(BaseModel):
    name: str

class PATCreateResponse(BaseModel):
    id: str
    name: str
    token: str  # Only returned once on creation
    created_at: datetime

class PATOut(BaseModel):
    id: str
    name: str
    created_at: datetime

@router.post("/pat", response_model=PATCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_pat(data: PATCreateRequest, current_user: User = Depends(get_current_user)):
    import secrets
    import hashlib
    
    # 1. Generate secure raw token string
    raw_token = f"flint_pat_{secrets.token_urlsafe(32)}"
    
    # 2. Hash token for database lookup
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    
    # 3. Create database entry
    pat = PersonalAccessToken(
        user_id=current_user.id,
        name=data.name,
        token_hash=token_hash,
        is_active=True
    )
    await pat.insert()
    
    return PATCreateResponse(
        id=str(pat.id),
        name=pat.name,
        token=raw_token,
        created_at=pat.created_at
    )

@router.get("/pat", response_model=List[PATOut])
async def list_pats(current_user: User = Depends(get_current_user)):
    pats = await PersonalAccessToken.find(
        PersonalAccessToken.user_id == current_user.id,
        PersonalAccessToken.is_active == True
    ).sort(-PersonalAccessToken.created_at).to_list()
    
    return [
        PATOut(
            id=str(p.id),
            name=p.name,
            created_at=p.created_at
        ) for p in pats
    ]

@router.delete("/pat/{pat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pat(pat_id: str, current_user: User = Depends(get_current_user)):
    if not ObjectId.is_valid(pat_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PAT ID format."
        )
        
    pat = await PersonalAccessToken.get(pat_id)
    if not pat or pat.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Personal Access Token not found."
        )
        
    await pat.delete()
    return None

