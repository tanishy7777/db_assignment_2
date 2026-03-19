import uuid

from datetime import datetime, timedelta, timezone



import jwt



from app.config import JWT_SECRET, JWT_EXPIRY_HOURS, ALGORITHM





def create_token(user_id: int, username: str, role: str, member_id) -> str:

    now = datetime.now(timezone.utc)

    payload = {

        "user_id":   user_id,

        "username":  username,

        "role":      role,

        "member_id": member_id,

        "exp":       now + timedelta(hours=JWT_EXPIRY_HOURS),

        "iat":       now,

        "jti":       str(uuid.uuid4()),

    }

    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)





def decode_token(token: str) -> dict:


    return jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])

