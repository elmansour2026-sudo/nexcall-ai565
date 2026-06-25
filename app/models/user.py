"""
NexCall AI — Modele User (authentification)
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)   # format: pbkdf2$<salt_hex>$<hash_hex>
    is_active     = Column(Boolean, default=True, nullable=False)
    is_admin      = Column(Boolean, default=True, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id":         self.id,
            "username":   self.username,
            "is_active":  self.is_active,
            "is_admin":   self.is_admin,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
