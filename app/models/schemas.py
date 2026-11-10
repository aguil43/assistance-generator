"""
Esquemas Pydantic para validación estricta de entradas, configuración de horarios
(Lunes-Viernes, Sábados, Domingos, Festivos, horas por falta), justificaciones y reporte.
"""

import re
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator


TardinessMode = Literal["fixed_time", "duration"]
SlotStatus = Literal[
    "ok",
    "late",
    "early_exit",
    "missing",
    "justified",
    "not_applicable",
    "absence",
    "justified_absence",
    "holiday",
    "rest",
]
DayStatus = Literal[
    "complete",
    "incomplete",
    "justified_complete",
    "absence",
    "justified_absence",
    "holiday",
    "rest_day",
]
DayType = Literal["weekday", "saturday", "sunday", "holiday"]


class HolidayItem(BaseModel):
    """Representa un día festivo oficial configurado dentro del periodo."""

    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="Fecha ISO YYYY-MM-DD.")
    label: str = Field(default="Día festivo oficial", max_length=120, description="Nombre o motivo del festivo.")

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        cleaned = value.strip()
        return cleaned if cleaned else "Día festivo oficial"


class AttendanceConfig(BaseModel):
    """Parámetros dinámicos configurables por el usuario desde la interfaz."""

    tardiness_mode: TardinessMode = Field(
        default="fixed_time",
        description=(
            "Modo de cálculo de retardo: 'fixed_time' evalúa contra las horas límite "
            "(08:15, 10:00, 15:00 en L-V y 09:00 en Sáb); 'duration' evalúa exceso sobre "
            "20 min en almuerzo y 60 min en comida."
        ),
    )
    duplicate_margin_minutes: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Margen en minutos entre checadas consecutivas para descartar registros dobles.",
    )
    hourly_wage: float = Field(
        default=0.0,
        ge=0.0,
        description="Salario por hora del personal para calcular descuentos por retardos y faltas.",
    )
    absence_hours_weekday: float = Field(
        default=8.0,
        ge=0.0,
        le=24.0,
        description="Horas de sueldo a descontar por cada falta injustificada de Lunes a Viernes.",
    )
    absence_hours_saturday: float = Field(
        default=4.0,
        ge=0.0,
        le=24.0,
        description="Horas de sueldo a descontar por cada falta injustificada en día Sábado (09:00-13:00).",
    )
    period_start: Optional[str] = Field(
        default=None,
        description="Fecha inicial del periodo a evaluar (YYYY-MM-DD). Si es None, usa la mínima del Excel.",
    )
    period_end: Optional[str] = Field(
        default=None,
        description="Fecha final del periodo a evaluar (YYYY-MM-DD). Si es None, usa la máxima del Excel.",
    )
    holidays: list[HolidayItem] = Field(
        default_factory=list,
        description="Lista de días festivos exentos de asistencia dentro del periodo.",
    )

    @field_validator("period_start", "period_end", mode="before")
    @classmethod
    def normalize_optional_date(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise ValueError("El formato de fecha debe ser YYYY-MM-DD.")
        return text

    @field_validator("holidays", mode="before")
    @classmethod
    def normalize_holidays(cls, value: Any) -> list[Any]:
        if not value:
            return []
        normalized: dict[str, dict[str, str]] = {}
        for item in value:
            if isinstance(item, str):
                dt_str = item.strip()
                if dt_str:
                    normalized[dt_str] = {"date": dt_str, "label": "Día festivo oficial"}
            elif isinstance(item, HolidayItem):
                normalized[item.date] = {"date": item.date, "label": item.label}
            elif isinstance(item, dict) and "date" in item:
                dt_str = str(item["date"]).strip()
                lbl = str(item.get("label") or "Día festivo oficial").strip()
                normalized[dt_str] = {"date": dt_str, "label": lbl or "Día festivo oficial"}
        return [normalized[k] for k in sorted(normalized.keys())]


class JustificationInput(BaseModel):
    """Datos enviados desde el menú de incidencias para justificar un registro faltante o una falta."""

    employee_id: str = Field(..., min_length=1, description="ID del empleado.")
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="Fecha ISO YYYY-MM-DD.")
    slot_id: str = Field(
        ...,
        min_length=1,
        description="Identificador del bloque horario faltante o 'full_absence' para falta completa.",
    )
    justification: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Explicación de la ausencia del registro o falta que se incluirá en el PDF.",
    )
    penalty_minutes: int = Field(
        default=0,
        ge=0,
        le=1440,
        description="Minutos de penalización a sumar al retardo (0 = sin descuento, >0 = penalización en min).",
    )

    @field_validator("justification")
    @classmethod
    def strip_and_validate_justification(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("La justificación no puede estar vacía ni contener solo espacios.")
        return cleaned


class JustificationRecord(JustificationInput):
    """Registro almacenado de una justificación aplicada con su número de nota asignado."""

    note_number: int = Field(..., ge=1, description="Número secuencial de nota en el reporte.")
    employee_name: str = Field(..., description="Nombre del empleado.")
    display_date: str = Field(..., description="Fecha formateada con día (ej. Mié 05/08/2026).")
    slot_label: str = Field(..., description="Etiqueta legible del bloque horario o falta completa.")


class SlotEvaluation(BaseModel):
    """Resultado del análisis de una de las 6 columnas horarias de un empleado en un día."""

    slot_id: str
    slot_label: str
    short_label: str
    target_time: str
    recorded_time: Optional[str] = None  # HH:MM si existió checada
    status: SlotStatus
    tardiness_minutes: int = 0
    penalty_minutes: int = 0
    note_number: Optional[int] = None
    justification: Optional[str] = None


class DayEvaluation(BaseModel):
    """Resultado consolidado de un día calendario para un empleado."""

    date: str  # YYYY-MM-DD
    display_date: str  # DD/MM/YYYY
    weekday_index: int  # 0=Lun .. 6=Dom
    weekday_name: str  # Lun, Mar, ..., Sáb, Dom
    day_type: DayType  # weekday, saturday, sunday, holiday
    holiday_label: Optional[str] = None
    expected_punches_count: int  # 6 (L-V), 2 (Sáb), 0 (Dom/Festivo)
    raw_punches_count: int
    valid_punches_count: int
    discarded_duplicates: list[str] = Field(default_factory=list)
    has_missing_punches: bool
    is_unjustified_absence: bool = False
    absence_hours_applied: float = 0.0
    unjustified_missing_count: int
    justified_count: int
    has_notes: bool
    row_status: DayStatus
    slots: list[SlotEvaluation]
    schedule_tardiness_minutes: int
    penalty_tardiness_minutes: int
    total_tardiness_minutes: int
    tardiness_discount: float = 0.0
    absence_discount: float = 0.0
    daily_discount: float


class MissingSlotOption(BaseModel):
    """Opción individual para el desplegable de registros faltantes o faltas del día."""

    date: str  # YYYY-MM-DD
    display_date: str  # Ej: "Mié 05/08/2026"
    slot_id: str  # 'entry'..'exit' o 'full_absence'
    slot_label: str
    dropdown_label: str
    is_full_absence: bool = False
    is_justified: bool = False
    current_justification: Optional[str] = None
    current_penalty_minutes: int = 0
    note_number: Optional[int] = None


class EmployeeWithMissing(BaseModel):
    """Empleado listado en el menú desplegable de usuarios con registros faltantes o faltas."""

    employee_id: str
    employee_name: str
    pending_missing_count: int
    justified_missing_count: int
    total_missing_count: int
    missing_slots: list[MissingSlotOption]


class EmployeeReport(BaseModel):
    """Resumen completo y detalle diario de un empleado dentro del periodo analizado."""

    employee_id: str
    employee_name: str
    days_analyzed: int
    incomplete_days_count: int
    unjustified_absences_count: int = 0
    justified_absences_count: int = 0
    total_absence_hours: float = 0.0
    justified_notes_count: int
    schedule_tardiness_minutes: int
    penalty_tardiness_minutes: int
    total_tardiness_minutes: int
    tardiness_discount: float = 0.0
    absence_discount: float = 0.0
    total_discount: float
    days: list[DayEvaluation]


class ReportSummary(BaseModel):
    """Totales globales del reporte actual."""

    source_filename: str
    detected_min_date: Optional[str] = None  # YYYY-MM-DD del archivo Excel
    detected_max_date: Optional[str] = None  # YYYY-MM-DD del archivo Excel
    active_period_start_iso: Optional[str] = None  # YYYY-MM-DD activo
    active_period_end_iso: Optional[str] = None  # YYYY-MM-DD activo
    period_start: Optional[str] = None  # DD/MM/YYYY activo
    period_end: Optional[str] = None  # DD/MM/YYYY activo
    holidays_count: int = 0
    total_employees: int = 0
    employees_with_missing_count: int = 0
    pending_missing_slots_count: int = 0
    unjustified_absences_count: int = 0
    justified_slots_count: int = 0
    total_tardiness_minutes: int = 0
    total_tardiness_discount: float = 0.0
    total_absence_discount: float = 0.0
    total_discount: float = 0.0


class ReportStateResponse(BaseModel):
    """Respuesta unificada de la API con configuración, menú de faltantes y vista previa."""

    has_data: bool
    config: AttendanceConfig
    summary: Optional[ReportSummary] = None
    employees_with_missing: list[EmployeeWithMissing] = Field(default_factory=list)
    employees: list[EmployeeReport] = Field(default_factory=list)
    notes: list[JustificationRecord] = Field(default_factory=list)
