"""
Configuración central y constantes por defecto del sistema de asistencia.
Define los horarios diferenciados de Lunes a Viernes (6 registros), Sábado (2 registros),
Domingo de descanso, identificador de faltas completas y valores iniciales.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScheduleSlotDef:
    """Definición inmutable de un bloque horario esperado dentro de una jornada."""

    slot_id: str
    index: int
    label: str
    short_label: str
    target_time: str  # Formato HH:MM
    target_minutes: int  # Minutos desde las 00:00


# Horario Lunes a Viernes (6 checadas esperadas)
OFFICIAL_SLOTS: tuple[ScheduleSlotDef, ...] = (
    ScheduleSlotDef(
        slot_id="entry",
        index=0,
        label="08:15 hrs - Entrada oficial",
        short_label="Entrada (08:15)",
        target_time="08:15",
        target_minutes=8 * 60 + 15,  # 495
    ),
    ScheduleSlotDef(
        slot_id="breakfast_out",
        index=1,
        label="09:40 hrs - Salida a almuerzo",
        short_label="Sal. Almuerzo (09:40)",
        target_time="09:40",
        target_minutes=9 * 60 + 40,  # 580
    ),
    ScheduleSlotDef(
        slot_id="breakfast_in",
        index=2,
        label="10:00 hrs - Regreso de almuerzo",
        short_label="Reg. Almuerzo (10:00)",
        target_time="10:00",
        target_minutes=10 * 60,  # 600
    ),
    ScheduleSlotDef(
        slot_id="lunch_out",
        index=3,
        label="14:00 hrs - Salida a comida",
        short_label="Sal. Comida (14:00)",
        target_time="14:00",
        target_minutes=14 * 60,  # 840
    ),
    ScheduleSlotDef(
        slot_id="lunch_in",
        index=4,
        label="15:00 hrs - Regreso de comida",
        short_label="Reg. Comida (15:00)",
        target_time="15:00",
        target_minutes=15 * 60,  # 900
    ),
    ScheduleSlotDef(
        slot_id="exit",
        index=5,
        label="18:00 hrs - Salida final",
        short_label="Salida (18:00+)",
        target_time="18:00",
        target_minutes=18 * 60,  # 1080
    ),
)

# Horario Sábados (únicamente 2 checadas: Entrada 09:00 y Salida 13:00+)
SATURDAY_SLOTS: tuple[ScheduleSlotDef, ...] = (
    ScheduleSlotDef(
        slot_id="entry",
        index=0,
        label="09:00 hrs - Entrada sábado",
        short_label="Entrada Sábado (09:00)",
        target_time="09:00",
        target_minutes=9 * 60,  # 540
    ),
    ScheduleSlotDef(
        slot_id="exit",
        index=5,
        label="13:00 hrs - Salida sábado",
        short_label="Salida Sábado (13:00+)",
        target_time="13:00",
        target_minutes=13 * 60,  # 780
    ),
)

SLOTS_BY_ID: dict[str, ScheduleSlotDef] = {slot.slot_id: slot for slot in OFFICIAL_SLOTS}
SATURDAY_SLOTS_BY_ID: dict[str, ScheduleSlotDef] = {slot.slot_id: slot for slot in SATURDAY_SLOTS}

# Identificadores especiales de justificación por día
FULL_ABSENCE_SLOT_ID: str = "full_absence"
ALL_MISSING_DAY_SLOT_ID: str = "all_missing_day"
VALID_JUSTIFIABLE_SLOT_IDS: frozenset[str] = frozenset(
    set(SLOTS_BY_ID.keys()) | {FULL_ABSENCE_SLOT_ID, ALL_MISSING_DAY_SLOT_ID}
)

# Nombres de días en español (0 = Lunes ... 6 = Domingo)
WEEKDAY_SHORT_ES: tuple[str, ...] = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
WEEKDAY_FULL_ES: tuple[str, ...] = (
    "Lunes",
    "Martes",
    "Miércoles",
    "Jueves",
    "Viernes",
    "Sábado",
    "Domingo",
)

# Duraciones máximas permitidas cuando se usa el modo "duration" (Lunes a Viernes)
ALLOWED_BREAKFAST_MINUTES: int = 20  # 09:40 a 10:00
ALLOWED_LUNCH_MINUTES: int = 60      # 14:00 a 15:00

# Valores iniciales por defecto
DEFAULT_TARDINESS_MODE: str = "fixed_time"
DEFAULT_DUPLICATE_MARGIN_MINUTES: int = 5
DEFAULT_HOURLY_WAGE: float = 0.0
DEFAULT_ABSENCE_HOURS_WEEKDAY: float = 8.0
DEFAULT_ABSENCE_HOURS_SATURDAY: float = 4.0
MAX_PERIOD_DAYS: int = 120
