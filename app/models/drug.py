from sqlalchemy import BigInteger, Boolean, Column, Index, Integer, String, Text

from app.db.database import Base


class TelegramUserPref(Base):
    __tablename__ = "telegram_user_prefs"

    chat_id = Column(BigInteger, primary_key=True, autoincrement=False, index=True)
    lang = Column(String(16), nullable=False, default="uz")
    username = Column(String(120), nullable=True)
    first_name = Column(String(120), nullable=True)


class Drug(Base):
    __tablename__ = "drugs"

    id = Column(Integer, primary_key=True, index=True)
    trade_name = Column(String(150), nullable=False, index=True)
    trade_name_cyrl = Column(String(150), nullable=False)
    search_tokens = Column(String(400), nullable=False, index=True)

    # International Nonproprietary Name (XPN / МНН)
    inn = Column(String(150), nullable=False, index=True)
    inn_uz = Column(String(150), nullable=False)
    inn_ru = Column(String(150), nullable=False)

    dosage = Column(String(80), nullable=False)
    form_uz = Column(String(80), nullable=False)
    form_ru = Column(String(80), nullable=False)

    manufacturer = Column(String(150), nullable=False)
    country_uz = Column(String(80), nullable=False)
    country_ru = Column(String(80), nullable=False)
    is_local_uz = Column(Boolean, default=False, nullable=False, index=True)

    avg_price_uzs = Column(Integer, nullable=False)
    prescription_required = Column(Boolean, default=True, nullable=False)

    category_uz = Column(String(100), nullable=False)
    category_ru = Column(String(100), nullable=False)

    purpose_uz = Column(Text, nullable=False)
    purpose_ru = Column(Text, nullable=False)
    how_to_take_uz = Column(Text, nullable=False)
    how_to_take_ru = Column(Text, nullable=False)
    warnings_uz = Column(Text, nullable=False)
    warnings_ru = Column(Text, nullable=False)

    __table_args__ = (
        Index("ix_drugs_inn_price", "inn", "avg_price_uzs"),
    )
