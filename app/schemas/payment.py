from typing import Optional
from pydantic import BaseModel, Field


class PlanFeature(BaseModel):
    title: str
    desc: str


class PlanInfo(BaseModel):
    id: str  # 'standard', 'premium', 'ultra'
    name: str
    price_monthly_uzs: int
    price_annual_uzs: int
    badge: Optional[str] = None
    features: list[PlanFeature]


class PromoCodeCheckRequest(BaseModel):
    code: str
    plan: str
    billing_cycle: str = "monthly"


class PromoCodeCheckResponse(BaseModel):
    valid: bool
    discount_uzs: int
    message: str


class PaymentCheckoutRequest(BaseModel):
    plan: str = Field(..., description="'premium' yoki 'ultra'")
    billing_cycle: str = Field("monthly", description="'monthly' yoki 'annually'")
    card_number: str = Field(..., description="Foydalanuvchi karta raqami")
    card_holder: str = Field(..., description="Karta egasining ISM FAMILYASI")
    cvc: Optional[str] = Field(None, description="Visa uchun CVC/CVV kodi")
    promo_code: Optional[str] = None
    receipt_image: Optional[str] = None  # Base64 photo of receipt
    user_phone: Optional[str] = None


class PaymentCheckoutResponse(BaseModel):
    payment_id: int
    status: str
    plan: str
    billing_cycle: str
    base_amount_uzs: int
    discount_uzs: int
    final_amount_uzs: int
    card_type: str
    receiver_card: str
    receiver_name: str
    message: str


class PaymentConfirmRequest(BaseModel):
    receipt_image: Optional[str] = None


class PaymentStatusResponse(BaseModel):
    payment_id: int
    status: str
    plan: str
    billing_cycle: str
    final_amount_uzs: int
    is_approved: bool
    user_plan: str
    message: str
