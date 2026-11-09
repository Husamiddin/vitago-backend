from typing import Literal, Optional
from pydantic import BaseModel, Field

SupportedLang = Literal["uz", "uz_cyrl", "ru"]


class AISuggestedAnalog(BaseModel):
    trade_name: str = Field(description="Analog dorining savdo nomi (masalan: Azitromitsin)")
    manufacturer: str = Field(description="Ishlab chiqaruvchi zavod yoki davlat")
    is_local_uz: bool = Field(description="O'zbekistonda ishlab chiqarilganmi?")
    estimated_price_uzs: int = Field(description="O'zbekiston dorixonalaridagi taxminiy narxi (so'mda, butun son)")
    dosage_and_form: str = Field(description="Doza va chiqarilish shakli (masalan: 500 mg, tabletka)")


class AIExtractedDrug(BaseModel):
    raw_written_text: str = Field(description="Retseptda shifokor qanday yozgan bo'lsa shunday asl satr")
    recognized_trade_name: str = Field(description="Aniqlangan dorining toza savdo nomi (masalan: Sumamed)")
    inn: str = Field(description="Xalqaro patentlanmagan faol modda nomi ingliz/lotin standartida (masalan: Azithromycin)")
    inn_localized: str = Field(description="Faol modda nomi foydalanuvchi tilida (masalan: Azitromitsin)")
    dosage: str = Field(description="Yozilgan doza va shakl (masalan: 500 mg kapsula)")
    regimen: str = Field(description="Qabul qilish tartibi va davomiyligi (masalan: Kuniga 1 mahal, 3 kun)")
    confidence: float = Field(description="Qo'lyozmani o'qish ishonchlilik darajasi 0.0 dan 1.0 gacha")
    simple_purpose: str = Field(description="Oddiy xalq tilida bu dori nima uchun yozilgani")
    how_to_take: str = Field(description="Ovqatdan oldinmi yoki keyin, qanday qabul qilish qoidasi")
    warnings: str = Field(description="Muhim ogohlantirishlar (bolalar, homiladorlar, allergiya)")
    estimated_brand_price_uzs: int = Field(description="Ushbu yozilgan dorining O'zbekistondagi taxminiy narxi (so'mda)")
    ai_fallback_analogs: list[AISuggestedAnalog] = Field(
        default_factory=list,
        description="O'zbekistonda uchraydigan 2-4 ta sifatli va arzon analoglar (mahalliy va generik)",
    )


class AIPrescriptionExtract(BaseModel):
    is_valid_prescription: bool = Field(description="Rasmda haqiqatan retsept yoki dori ro'yxati/qutisi bormi?")
    doctor_notes_summary: str = Field(description="Retsept bo'yicha umumiy xulosa yoki tashxis izohi (sodda tilda)")
    items: list[AIExtractedDrug] = Field(default_factory=list, description="Retseptdan o'qilgan barcha dorilar ro'yxati")


# ---- Public API Response Schemas ----

class DrugAnalogOption(BaseModel):
    id: Optional[int] = None
    trade_name: str
    inn: str
    inn_localized: str
    dosage: str
    form: str
    manufacturer: str
    country: str
    is_local_uz: bool
    price_uzs: int
    savings_uzs: int
    savings_percent: Optional[int] = None
    source: Literal["verified_db", "ai_estimate"] = "verified_db"
    clinical_equivalence_status: str = "verified_exact_match"  # 'verified_exact_match' | 'same_inn_formulation_verification_required' | 'related_medicine_not_confirmed' | 'insufficient_information'
    is_verified: bool = True
    verification_source: Optional[str] = "O'zR SSV Davlat Reyestri"


class AnalyzedDrugResult(BaseModel):
    item_id: str
    raw_written_text: str
    prescribed_drug_id: Optional[int] = None
    prescribed_name: str
    inn: str
    inn_localized: str
    dosage: str
    form: str
    manufacturer: str
    country: str
    is_local_uz: bool
    prescribed_price_uzs: int
    regimen: str
    confidence: float
    category: str
    simple_purpose: str
    how_to_take: str
    warnings: str
    price_source: Literal["verified_db", "ai_estimate"]
    analogs: list[DrugAnalogOption]
    recommended_analog: Optional[DrugAnalogOption] = None
    max_savings_uzs: int = 0
    max_savings_percent: Optional[int] = None
    clinical_equivalence_status: str = "verified_exact_match"
    is_verified: bool = True
    verification_source: Optional[str] = "O'zR SSV Davlat Reyestri"


class PrescriptionAnalysisResponse(BaseModel):
    analysis_id: str
    mode: Literal["ai_vision", "demo_sample", "drug_lookup"]
    is_valid_prescription: bool = True
    summary: str
    language: SupportedLang
    total_prescribed_uzs: int
    total_with_analogs_uzs: int
    total_savings_uzs: int
    total_savings_percent: Optional[int] = None
    all_items_verified: bool = True
    items: list[AnalyzedDrugResult]
    disclaimer: str


class DrugSearchItem(BaseModel):
    id: int
    trade_name: str
    inn: str
    inn_localized: str
    dosage: str
    form: str
    manufacturer: str
    country: str
    is_local_uz: bool
    avg_price_uzs: int
    category: str
    analogs_count: int = 0
    cheapest_analog_uzs: Optional[int] = None


class SamplePrescriptionMeta(BaseModel):
    id: str
    title: str
    subtitle: str
    badge: str
    drugs_preview: list[str]
    estimated_savings_uzs: int


class PlatformStatsResponse(BaseModel):
    total_drugs: int
    total_inn_groups: int
    local_uz_drugs: int
    avg_savings_percent: int
    ai_vision_configured: bool
    ai_model: str
