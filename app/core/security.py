from datetime import datetime, timedelta
from typing import Any, Union
import urllib.request
import json
import time
from jose import jwt, JWTError
import bcrypt
from app.core.config import settings

# Cache for Google's Firebase public keys
GOOGLE_KEYS_CACHE = {
    "keys": None,
    "expires_at": 0
}

def get_google_public_keys() -> list:
    now = time.time()
    # Cache keys for 1 hour to avoid fetching from Google on every API request
    if GOOGLE_KEYS_CACHE["keys"] and GOOGLE_KEYS_CACHE["expires_at"] > now:
        return GOOGLE_KEYS_CACHE["keys"]
    
    try:
        url = "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
        req = urllib.request.Request(
            url, 
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            keys_data = json.loads(response.read().decode("utf-8"))
            GOOGLE_KEYS_CACHE["keys"] = keys_data.get("keys", [])
            GOOGLE_KEYS_CACHE["expires_at"] = now + 3600  # 1 hour
            return GOOGLE_KEYS_CACHE["keys"]
    except Exception as e:
        # Fallback to expired cache if fetching fails
        if GOOGLE_KEYS_CACHE["keys"]:
            return GOOGLE_KEYS_CACHE["keys"]
        raise ValueError(f"Failed to fetch public keys from Google: {e}")

def verify_firebase_token(id_token: str, firebase_project_id: str) -> dict:
    try:
        unverified_header = jwt.get_unverified_header(id_token)
        kid = unverified_header.get("kid")
        if not kid:
            raise ValueError("No 'kid' claim found in token header")
        
        public_keys = get_google_public_keys()
        
        # Find matching key
        matching_key = None
        for key in public_keys:
            if key.get("kid") == kid:
                matching_key = key
                break
                
        if not matching_key:
            raise ValueError("No matching public key found for the 'kid'")
            
        # Decode and verify the token signature and standard claims
        payload = jwt.decode(
            id_token,
            matching_key,
            algorithms=["RS256"],
            audience=firebase_project_id,
            issuer=f"https://securetoken.google.com/{firebase_project_id}"
        )
        
        # Ensure auth_time is in the past
        auth_time = payload.get("auth_time")
        if auth_time and auth_time > time.time() + 300: # allow 5 min clock skew
            raise ValueError("Token authentication time is in the future")
            
        return payload
    except JWTError as e:
        raise ValueError(f"Invalid token signature or claim verification failed: {e}")
    except Exception as e:
        raise ValueError(f"Token verification failed: {str(e)}")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False

def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def create_access_token(subject: Union[str, Any], expires_delta: timedelta = None) -> str:
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

