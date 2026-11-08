from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    user_phone = Column(String(32), index=True, nullable=False)
    user_name = Column(String(120), nullable=False)

    plan = Column(String(32), nullable=False)  # 'premium' | 'ultra'
    billing_cycle = Column(String(32), default="monthly", nullable=False)  # 'monthly' | 'annually'

    base_amount_uzs = Column(Integer, nullable=False)
    discount_uzs = Column(Integer, default=0, nullable=False)
    final_amount_uzs = Column(Integer, nullable=False)

    promo_code = Column(String(64), nullable=True)

    # Card info as provided by user
    card_number = Column(String(32), nullable=False)
    card_type = Column(String(32), nullable=False)  # 'humo' | 'uzcard' | 'visa' | 'mastercard'
    card_holder = Column(String(120), nullable=False)
    cvc = Column(String(8), nullable=True)

    # Payment target
    receiver_card = Column(String(32), default="1234567812345678", nullable=False)
    receiver_name = Column(String(64), default="Aminov H", nullable=False)

    receipt_image = Column(Text, nullable=True)  # base64 receipt photo

    # Status: 'pending', 'waiting_admin', 'approved', 'rejected'
    status = Column(String(32), default="pending", index=True, nullable=False)
    confirmed_by_user_at = Column(DateTime, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    admin_notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class ScanRecord(Base):
    __tablename__ = "scan_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=True)
    user_phone = Column(String(32), nullable=True)
    user_name = Column(String(120), nullable=True)

    scan_type = Column(String(32), default="prescription", nullable=False)  # 'prescription', 'food', 'drug_lookup'
    image_preview = Column(Text, nullable=True)  # thumbnail / b64 or summary
    summary = Column(Text, nullable=True)
    extracted_drugs = Column(Text, nullable=True)  # JSON formatted drug list

    total_prescribed_uzs = Column(Integer, default=0, nullable=False)
    total_savings_uzs = Column(Integer, default=0, nullable=False)
    savings_percent = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime, default=utcnow, nullable=False)


class FoodNutritionRecord(Base):
    __tablename__ = "food_nutrition_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    image_preview = Column(Text, nullable=True)

    food_name = Column(Text, nullable=False)
    calories = Column(Integer, default=0, nullable=False)
    ingredients = Column(Text, nullable=True)

    fat_g = Column(Text, default="0g")
    carbs_g = Column(Text, default="0g")
    protein_g = Column(Text, default="0g")
    vitamins = Column(Text, nullable=True)

    sleep_after_hours = Column(Text, nullable=True)
    walk_distance_km = Column(Text, nullable=True)
    health_advice = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)
