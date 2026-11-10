"""
Módulo 5A: Controlador de Endpoints REST en FastAPI (routes.py).

Expone las rutas de subida de Excel, carga de ejemplo, actualización de configuración
en caliente, gestión de justificaciones/penalizaciones y descarga del PDF blindado.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app.models.schemas import (
    AttendanceConfig,
    JustificationInput,
    ReportStateResponse,
)
from app.modules.excel_parser import ExcelParseError, generate_sample_excel_bytes
from app.modules.pdf_generator import generate_protected_pdf
from app.modules.state_manager import StateError, state_manager

router = APIRouter(prefix="/api", tags=["Asistencia"])


def _resolve_session_id(x_session_id: Optional[str]) -> str:
    """Obtiene el identificador de sesión del cliente o usa la sesión local por defecto."""
    if x_session_id and x_session_id.strip():
        return x_session_id.strip()[:64]
    return "default-local-session"


@router.get("/state", response_model=ReportStateResponse)
def get_current_state(
    x_session_id: Optional[str] = Header(default=None),
) -> ReportStateResponse:
    """Devuelve la configuración activa y la vista previa actual si hay un archivo cargado."""
    session_id = _resolve_session_id(x_session_id)
    return state_manager.get_state(session_id)


@router.post("/upload", response_model=ReportStateResponse)
async def upload_attendance_excel(
    file: UploadFile = File(...),
    x_session_id: Optional[str] = Header(default=None),
) -> ReportStateResponse:
    """Recibe el archivo Excel/CSV del reloj checador, lo limpia y evalúa los horarios."""
    session_id = _resolve_session_id(x_session_id)
    filename = file.filename or "reporte_checador.xlsx"

    try:
        content = await file.read()
        return state_manager.load_excel(session_id=session_id, file_bytes=content, filename=filename)
    except ExcelParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error inesperado al procesar el archivo: {exc}",
        ) from exc


@router.post("/load-sample", response_model=ReportStateResponse)
def load_sample_attendance(
    extra_cases: bool = Query(default=False, description="Incluir un segundo empleado con casos adicionales."),
    x_session_id: Optional[str] = Header(default=None),
) -> ReportStateResponse:
    """Carga directamente el archivo Excel de demostración con el formato oficial del reloj checador."""
    session_id = _resolve_session_id(x_session_id)
    sample_bytes = generate_sample_excel_bytes(include_extra_cases=extra_cases)
    sample_name = "Reporte_Eventos_Demo_Completo.xlsx" if extra_cases else "Reporte_Eventos_Reloj.xlsx"
    return state_manager.load_excel(session_id=session_id, file_bytes=sample_bytes, filename=sample_name)


@router.get("/sample-excel")
def download_sample_excel(
    extra_cases: bool = Query(default=False),
) -> Response:
    """Permite descargar un archivo .xlsx con la estructura exacta del reloj checador."""
    sample_bytes = generate_sample_excel_bytes(include_extra_cases=extra_cases)
    filename = "Reporte_Eventos_Reloj_Ejemplo.xlsx"
    return Response(
        content=sample_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/config", response_model=ReportStateResponse)
def update_attendance_config(
    config: AttendanceConfig,
    x_session_id: Optional[str] = Header(default=None),
) -> ReportStateResponse:
    """Actualiza el modo de retardo, margen, salario, horas por falta, periodo y días festivos."""
    session_id = _resolve_session_id(x_session_id)
    try:
        return state_manager.update_config(session_id=session_id, new_config=config)
    except StateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/justify", response_model=ReportStateResponse)
def add_or_update_justification(
    payload: JustificationInput,
    x_session_id: Optional[str] = Header(default=None),
) -> ReportStateResponse:
    """Registra o edita la justificación y minutos de penalización de un registro faltante."""
    session_id = _resolve_session_id(x_session_id)
    try:
        return state_manager.upsert_justification(session_id=session_id, item=payload)
    except StateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/justify", response_model=ReportStateResponse)
def delete_justification(
    employee_id: str = Query(..., min_length=1),
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    slot_id: str = Query(..., min_length=1),
    x_session_id: Optional[str] = Header(default=None),
) -> ReportStateResponse:
    """Elimina una justificación aplicada previamente."""
    session_id = _resolve_session_id(x_session_id)
    try:
        return state_manager.remove_justification(
            session_id=session_id,
            employee_id=employee_id,
            date_iso=date,
            slot_id=slot_id,
        )
    except StateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/export-pdf")
def export_protected_pdf(
    session_id: Optional[str] = Query(default=None),
    x_session_id: Optional[str] = Header(default=None),
) -> Response:
    """Genera y descarga el reporte final en PDF protegido contra modificaciones."""
    resolved_id = _resolve_session_id(x_session_id or session_id)
    current_state = state_manager.get_state(resolved_id)

    if not current_state.has_data:
        raise HTTPException(
            status_code=400,
            detail="No hay información cargada para exportar a PDF. Suba primero un archivo Excel.",
        )

    try:
        pdf_bytes = generate_protected_pdf(current_state)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error al generar el PDF protegido: {exc}",
        ) from exc

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_filename = f"Reporte_Asistencia_Protegido_{stamp}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{out_filename}"',
            "Cache-Control": "no-store",
        },
    )
