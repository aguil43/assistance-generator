"""
Módulo 3: Gestor de Estado de Sesión, Configuración Dinámica, Festivos y Justificaciones.

Responsabilidades:
- Mantener en memoria el archivo procesado por sesión de trabajo sin necesidad de resubir el Excel.
- Auto-ajustar las fechas de inicio y fin del periodo al cargar un nuevo Excel, permitiendo
  modificarlas después desde el panel de configuración.
- Permitir cambiar en caliente modo de retardo, margen de registros dobles, salario por hora,
  horas descontadas por falta (L-V y Sábado) y lista de días festivos.
- Registrar, actualizar o eliminar justificaciones tanto para checadas faltantes individuales
  como para faltas de día completo ('full_absence').
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from app.config import ALL_MISSING_DAY_SLOT_ID, VALID_JUSTIFIABLE_SLOT_IDS
from app.models.schemas import (
    AttendanceConfig,
    JustificationInput,
    ReportStateResponse,
)
from app.modules.attendance_engine import evaluate_attendance_report
from app.modules.excel_parser import ParsedAttendanceFile, parse_attendance_file


class StateError(ValueError):
    """Error de validación o estado inválido en operaciones de sesión."""


@dataclass
class SessionWorkspace:
    """Contenedor aislado del estado de trabajo de una sesión."""

    config: AttendanceConfig = field(default_factory=AttendanceConfig)
    parsed_file: ParsedAttendanceFile | None = None
    justifications: dict[tuple[str, str, str], JustificationInput] = field(default_factory=dict)


class AttendanceStateManager:
    """Administrador thread-safe de sesiones de análisis de asistencia."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionWorkspace] = {}
        self._lock = Lock()

    def _get_or_create(self, session_id: str) -> SessionWorkspace:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionWorkspace()
        return self._sessions[session_id]

    def get_state(self, session_id: str) -> ReportStateResponse:
        """Obtiene el estado actual evaluado de la sesión."""
        with self._lock:
            ws = self._get_or_create(session_id)
            if ws.parsed_file is None:
                return ReportStateResponse(has_data=False, config=ws.config)
            return evaluate_attendance_report(ws.parsed_file, ws.config, ws.justifications)

    def load_excel(
        self,
        session_id: str,
        file_bytes: bytes,
        filename: str,
    ) -> ReportStateResponse:
        """
        Procesa un nuevo archivo de reloj checador, sincroniza automáticamente el rango
        de fechas del periodo con las fechas mínima y máxima del Excel y limpia justificaciones previas.
        """
        parsed = parse_attendance_file(file_bytes=file_bytes, filename=filename)
        with self._lock:
            ws = self._get_or_create(session_id)
            ws.parsed_file = parsed
            ws.justifications.clear()
            ws.config = ws.config.model_copy(
                update={
                    "period_start": parsed.min_date.isoformat(),
                    "period_end": parsed.max_date.isoformat(),
                    "holidays": [],
                }
            )
            return evaluate_attendance_report(ws.parsed_file, ws.config, ws.justifications)

    def update_config(
        self,
        session_id: str,
        new_config: AttendanceConfig,
    ) -> ReportStateResponse:
        """Actualiza la configuración dinámica (incluyendo periodo y festivos) y recalcula."""
        with self._lock:
            ws = self._get_or_create(session_id)
            previous_config = ws.config
            ws.config = new_config
            if ws.parsed_file is None:
                return ReportStateResponse(has_data=False, config=ws.config)
            try:
                return evaluate_attendance_report(ws.parsed_file, ws.config, ws.justifications)
            except ValueError as exc:
                ws.config = previous_config
                raise StateError(str(exc)) from exc

    def upsert_justification(
        self,
        session_id: str,
        item: JustificationInput,
    ) -> ReportStateResponse:
        """Agrega o modifica la justificación y penalización de una checada faltante o falta completa."""
        if item.slot_id not in VALID_JUSTIFIABLE_SLOT_IDS:
            raise StateError(f"El bloque o tipo de incidencia '{item.slot_id}' no es válido.")

        with self._lock:
            ws = self._get_or_create(session_id)
            if ws.parsed_file is None:
                raise StateError("Primero debe cargar un archivo Excel antes de registrar justificaciones.")

            current_eval = evaluate_attendance_report(ws.parsed_file, ws.config, ws.justifications)

            if item.slot_id == ALL_MISSING_DAY_SLOT_ID:
                emp_match = next(
                    (e for e in current_eval.employees_with_missing if e.employee_id == item.employee_id),
                    None,
                )
                day_slots = [
                    opt
                    for opt in (emp_match.missing_slots if emp_match else [])
                    if opt.date == item.date
                    and opt.slot_id != ALL_MISSING_DAY_SLOT_ID
                    and not opt.is_justified
                ]
                if not day_slots:
                    raise StateError(
                        "No existen registros pendientes por justificar en ese día para este empleado."
                    )
                for idx, opt in enumerate(day_slots):
                    ws.justifications[(item.employee_id, item.date, opt.slot_id)] = JustificationInput(
                        employee_id=item.employee_id,
                        date=item.date,
                        slot_id=opt.slot_id,
                        justification=item.justification,
                        penalty_minutes=item.penalty_minutes if idx == 0 else 0,
                    )
                return evaluate_attendance_report(ws.parsed_file, ws.config, ws.justifications)

            valid_targets = {
                (emp.employee_id, opt.date, opt.slot_id)
                for emp in current_eval.employees_with_missing
                for opt in emp.missing_slots
                if opt.slot_id != ALL_MISSING_DAY_SLOT_ID
            }
            target_key = (item.employee_id, item.date, item.slot_id)
            if target_key not in valid_targets:
                raise StateError(
                    "El registro seleccionado no corresponde a una checada faltante o falta válida para este empleado."
                )

            ws.justifications[target_key] = item
            return evaluate_attendance_report(ws.parsed_file, ws.config, ws.justifications)

    def remove_justification(
        self,
        session_id: str,
        employee_id: str,
        date_iso: str,
        slot_id: str,
    ) -> ReportStateResponse:
        """Elimina una justificación previamente aplicada."""
        with self._lock:
            ws = self._get_or_create(session_id)
            if ws.parsed_file is None:
                raise StateError("No hay ningún reporte cargado en la sesión actual.")

            target_key = (employee_id, date_iso, slot_id)
            if target_key not in ws.justifications:
                raise StateError("No se encontró la justificación que intenta eliminar.")

            del ws.justifications[target_key]
            return evaluate_attendance_report(ws.parsed_file, ws.config, ws.justifications)


# Instancia global compartida por los endpoints de la aplicación
state_manager = AttendanceStateManager()
