from typing import Optional
from pydantic import BaseModel, Field


class AIChatMessage(BaseModel):
    role: str  # 'user' | 'assistant'
    text: str


class AIChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    history: list[AIChatMessage] = []
    voice_gender: Optional[str] = "female"  # 'male' | 'female'
    lang: str = "uz"


class AIChatResponse(BaseModel):
    reply: str
    voice_url: Optional[str] = None
    voice_gender: str = "female"


class HealthAnalysisRequest(BaseModel):
    daily_routine_notes: Optional[str] = None
    lang: str = "uz"


class HealthAnalysisResponse(BaseModel):
    daily_plan_summary: str
    target_steps: int
    target_water_ml: int
    target_calories_burn: int
    food_recommendations: list[str]
    activity_routine: list[str]
    sleep_advice: str


class ElderlyAnalysisRequest(BaseModel):
    notes_or_symptoms: Optional[str] = None
    blood_test_image: Optional[str] = None  # Base64
    audio_transcription: Optional[str] = None
    lang: str = "uz"


class ElderlyAnalysisResponse(BaseModel):
    summary: str
    has_diabetes_signs: str
    blood_pressure_advice: str
    dietary_dos_and_donts: list[str]
    walking_recommendation_km: str
    suggested_safe_medicines: list[str]
    disclaimer: str


class FoodScanRequest(BaseModel):
    image_base64: str = Field(..., description="Ovqat rasmi (JPEG/PNG base64)")
    portion_grams: Optional[int] = Field(None, description="Foydalanuvchi ko'rsatgan taxminiy porsiya grammi")
    user_notes: Optional[str] = Field(None, description="Foydalanuvchi izohi yoki tuzatishi")
    lang: str = "uz"


class FoodScanResponse(BaseModel):
    food_name: str
    calories: int
    calorie_range: Optional[str] = None  # e.g. "450 - 520 kcal"
    portion_size_g: Optional[int] = 300
    ingredients: list[str]
    sleep_after_hours: str
    fat_g: str
    carbs_g: str
    protein_g: str
    vitamins: list[str]
    walk_distance_km: str
    health_advice: str


class FoodCorrectionRequest(BaseModel):
    original_food_name: str
    corrected_food_name: Optional[str] = None
    portion_grams: int = Field(300, ge=50, le=2000)
    ingredients: Optional[list[str]] = None


class AlarmMissionVerifyRequest(BaseModel):
    step: int = Field(..., ge=1, le=3, description="1: rakvina/cho'mbirak, 2: tish yuvish, 3: tetik yuz")
    image_base64: str = Field(..., description="Kameradan olingan jonli rasm")
    lang: str = "uz"


class AlarmMissionVerifyResponse(BaseModel):
    step: int
    passed: bool
    next_step: int
    message: str
    mission_completed: bool


# --- Fitness & Yoga AI Coach Schemas ---
class ExerciseItem(BaseModel):
    name: str
    duration_or_reps: str
    rest_sec: int = 20
    instruction: str
    focus_muscle: str
    animation_icon: str = "💪"


class WorkoutPlanRequest(BaseModel):
    workout_type: str = "fitness"  # 'fitness' | 'yoga'
    age: Optional[int] = None
    duration_min: int = 20
    fitness_level: str = "boshlovchi"  # 'boshlovchi' | 'o`rtacha' | 'yuqori'
    health_conditions: Optional[str] = None
    lang: str = "uz"


class WorkoutPlanResponse(BaseModel):
    title: str
    workout_type: str
    total_duration_min: int
    estimated_kcal: int
    target_audience: str
    safety_precautions: list[str]
    exercises: list[ExerciseItem]


# --- Goal Planner (Maqsadlarim) Schemas ---
class DailyRoutineItem(BaseModel):
    time: str
    action: str


class GoalsPlanRequest(BaseModel):
    weight_target_kg: Optional[float] = None
    target_type: str = "vazn_tashlash"  # 'vazn_tashlash' | 'vazn_saqlash' | 'mushak_yigish'
    daily_steps_target: Optional[int] = 10000
    daily_active_minutes: Optional[int] = 45
    activity_preference: str = "yurish"  # 'yurish' | 'yugurish' | 'fitness' | 'yoga'
    health_notes: Optional[str] = None
    lang: str = "uz"


class GoalsPlanResponse(BaseModel):
    summary: str
    recommended_daily_steps: int
    recommended_daily_water_ml: int
    recommended_daily_burn_kcal: int
    weekly_milestones: list[str]
    daily_routine: list[DailyRoutineItem]
    motivation_quote: str
    reminder_frequency_min: int = 45


# --- Food Verification & AI Murabbiy Schemas ---
class FoodVerifyRequest(BaseModel):
    image_base64: str = Field(..., description="Ovqat rasmi (JPEG/PNG base64)")
    meal_time: str = "lunch"  # 'breakfast' | 'lunch' | 'dinner' | 'snack'
    user_goal: Optional[str] = None
    lang: str = "uz"


class FoodVerifyResponse(BaseModel):
    food_name: str
    calories: int
    carbs_g: str
    fat_g: str
    protein_g: str
    is_healthy: bool
    is_plan_compliant: bool
    warning_alert: Optional[str] = None
    swap_recommendation: Optional[str] = None
    health_advice: str
    walk_distance_km: str
    sleep_after_hours: str

