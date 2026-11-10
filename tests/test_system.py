"""
Suite integral de pruebas unitarias y de integración para todos los módulos:
- Módulo 1: excel_parser.py (limpieza de Excel/CSV crudo de reloj checador)
- Módulo 2: attendance_engine.py (Lunes-Viernes 6 reg., Sábados 2 reg. 09:00-13:00,
  Domingos de descanso, Días Festivos, Faltas completas con descuento configurable,
  deduplicación y ambos modos de retardo)
- Módulo 3 y 5A: state_manager.py y endpoints FastAPI
- Módulo 4: pdf_generator.py (cifrado AES-256, bloqueo de permisos y renderizado de todos los estados)
"""

from datetime import datetime
import io

from fastapi.testclient import TestClient
from pypdf import PdfReader
from pypdf.constants import UserAccessPermissions

from app.config import FULL_ABSENCE_SLOT_ID
from app.main import app
from app.models.schemas import AttendanceConfig, HolidayItem, JustificationInput
from app.modules.attendance_engine import (
    assign_punches_to_official_slots,
    calculate_monetary_discount,
    deduplicate_daily_punches,
    evaluate_attendance_report,
)
from app.modules.excel_parser import (
    generate_sample_excel_bytes,
    parse_attendance_file,
)


RAW_CSV_FROM_ATTACHMENT = """,,,,,,,
,Reporte de Eventos,,,,,,
,,,,,,,
,,,,,,,
,,,,,,,
,ID,Nombre ,Fecha / Hora,Estado,,,Tipo de Registro
,1,Lorena 1,03/08/2026 07:50,Entrada,,,Normal
,1,Lorena 1,03/08/2026 09:43,Entrada,,,Normal
,1,Lorena 1,03/08/2026 09:54,Entrada,,,Normal
,1,Lorena 1,03/08/2026 14:03,Entrada,,,Normal
,1,Lorena 1,03/08/2026 15:00,Entrada,,,Normal
,1,Lorena 1,03/08/2026 18:16,Entrada,,,Normal
,1,Lorena 1,04/08/2026 07:54,Entrada,,,Normal
,1,Lorena 1,04/08/2026 09:43,Entrada,,,Normal
,1,Lorena 1,04/08/2026 09:54,Entrada,,,Normal
,1,Lorena 1,04/08/2026 14:04,Entrada,,,Normal
,1,Lorena 1,04/08/2026 15:02,Entrada,,,Normal
,1,Lorena 1,04/08/2026 18:00,Entrada,,,Normal
,1,Lorena 1,05/08/2026 08:06,Entrada,,,Normal
,1,Lorena 1,05/08/2026 09:41,Entrada,,,Normal
,1,Lorena 1,05/08/2026 09:52,Entrada,,,Normal
,1,Lorena 1,05/08/2026 14:03,Entrada,,,Normal
,1,Lorena 1,05/08/2026 14:50,Entrada,,,Normal
,,,,,,Página: 1 / 1,
,,,,,,,
,,,,,,,
Fecha / Hora:26/10/2026 15:27,,,,,,,
"""


def test_parse_exact_attachment_csv_and_xlsx():
    """Verifica que tanto el volcado exacto CSV como el XLSX se limpien sin basura."""
    parsed_csv = parse_attendance_file(RAW_CSV_FROM_ATTACHMENT.encode("utf-8"), "adjunto.csv")
    assert len(parsed_csv.employees) == 1
    emp = parsed_csv.employees[0]
    assert emp.employee_id == "1"
    assert emp.employee_name == "Lorena 1"
    assert parsed_csv.total_raw_events == 17
    assert len(emp.punches_by_date) == 3

    xlsx_bytes = generate_sample_excel_bytes(include_extra_cases=False)
    parsed_xlsx = parse_attendance_file(xlsx_bytes, "Reporte_Eventos.xlsx")
    assert parsed_xlsx.total_raw_events == 17
    assert parsed_xlsx.employees[0].employee_name == "Lorena 1"


def test_deduplication_keeps_first_punch_weekday_and_saturday():
    """Verifica que en checadas dobles L-V (6 slots) y Sábado (2 slots) se conserve la primera hora."""
    punches = [
        datetime(2026, 8, 3, 8, 15),
        datetime(2026, 8, 3, 8, 17),  # Duplicado a los 2 min
        datetime(2026, 8, 3, 9, 40),
        datetime(2026, 8, 3, 10, 0),
        datetime(2026, 8, 3, 14, 0),
        datetime(2026, 8, 3, 15, 0),
        datetime(2026, 8, 3, 18, 5),
    ]
    kept, discarded = deduplicate_daily_punches(punches, margin_minutes=5, max_slots=6)
    assert len(kept) == 6
    assert kept[0].strftime("%H:%M") == "08:15"
    assert discarded == ["08:17"]

    # En Sábado (max_slots=2), si hay 3 checadas conserva la primera del par más cercano
    sat_punches = [
        datetime(2026, 8, 8, 9, 0),
        datetime(2026, 8, 8, 9, 12),
        datetime(2026, 8, 8, 13, 5),
    ]
    kept_sat, disc_sat = deduplicate_daily_punches(sat_punches, margin_minutes=5, max_slots=2)
    assert len(kept_sat) == 2
    assert kept_sat[0].strftime("%H:%M") == "09:00"
    assert kept_sat[1].strftime("%H:%M") == "13:05"
    assert disc_sat == ["09:12"]


def test_missing_middle_slot_detection():
    """Verifica que si falta el regreso de comida (15:00), se identifique exactamente ese bloque."""
    punches = [
        datetime(2026, 8, 4, 8, 10),
        datetime(2026, 8, 4, 9, 40),
        datetime(2026, 8, 4, 9, 58),
        datetime(2026, 8, 4, 14, 2),
        datetime(2026, 8, 4, 18, 5),
    ]
    assigned = assign_punches_to_official_slots(punches)
    assert assigned["lunch_in"] is None
    assert assigned["exit"] is not None and assigned["exit"].strftime("%H:%M") == "18:05"


def test_tardiness_modes_and_salary_discount():
    """Prueba el switch entre Modo Hora Límite y Modo Duración sobre los datos de Lorena 1."""
    xlsx_bytes = generate_sample_excel_bytes(include_extra_cases=False)
    parsed = parse_attendance_file(xlsx_bytes, "Lorena.xlsx")

    cfg_fixed = AttendanceConfig(tardiness_mode="fixed_time", duplicate_margin_minutes=5, hourly_wage=120.0)
    res_fixed = evaluate_attendance_report(parsed, cfg_fixed, {})
    emp_fixed = res_fixed.employees[0]
    assert emp_fixed.total_tardiness_minutes == 2
    assert emp_fixed.incomplete_days_count == 1

    cfg_dur = AttendanceConfig(tardiness_mode="duration", duplicate_margin_minutes=5, hourly_wage=120.0)
    res_dur = evaluate_attendance_report(parsed, cfg_dur, {})
    assert res_dur.employees[0].total_tardiness_minutes == 0

    assert calculate_monetary_discount(30, 120.0) == 60.0

    just_key = ("1", "2026-08-05", "exit")
    just_item = JustificationInput(
        employee_id="1",
        date="2026-08-05",
        slot_id="exit",
        justification="Olvido de checada al salir; reportó salida con retardo acumulado.",
        penalty_minutes=28,
    )
    res_just = evaluate_attendance_report(parsed, cfg_fixed, {just_key: just_item})
    emp_just = res_just.employees[0]
    assert emp_just.incomplete_days_count == 0
    assert emp_just.total_tardiness_minutes == 30
    assert emp_just.total_discount == 60.0


def test_saturday_sunday_holidays_and_full_absences():
    """
    Verifica:
    - Horario Sábado (09:00 a 13:00 con 2 checadas y N/A en alimentos).
    - Domingo como día de descanso sin falta ni descuento.
    - Falta completa L-V y Sábado (con horas configurables) y su exención al marcar Día Festivo
      o al justificarla desde la interfaz.
    """
    xlsx_bytes = generate_sample_excel_bytes(include_extra_cases=False)
    parsed = parse_attendance_file(xlsx_bytes, "Lorena_Semana.xlsx")

    # Lorena solo tiene checadas Lun 03/08 a Mié 05/08.
    # Extendemos el periodo hasta el Domingo 09/08/2026:
    # - Jue 06/08 -> Falta L-V (8.5 hrs configuradas)
    # - Vie 07/08 -> Lo configuraremos como FESTIVO (0 hrs falta)
    # - Sáb 08/08 -> Falta Sábado (4.0 hrs configuradas)
    # - Dom 09/08 -> Descanso dominical (0 hrs falta)
    cfg = AttendanceConfig(
        tardiness_mode="fixed_time",
        duplicate_margin_minutes=5,
        hourly_wage=100.0,
        absence_hours_weekday=8.5,
        absence_hours_saturday=4.0,
        period_start="2026-08-03",
        period_end="2026-08-09",
        holidays=[HolidayItem(date="2026-08-07", label="Aniversario Empresa")],
    )
    res = evaluate_attendance_report(parsed, cfg, {})
    emp = res.employees[0]

    days_by_iso = {d.date: d for d in emp.days}
    assert days_by_iso["2026-08-06"].row_status == "absence"
    assert days_by_iso["2026-08-06"].absence_hours_applied == 8.5
    assert days_by_iso["2026-08-06"].daily_discount == 850.0

    assert days_by_iso["2026-08-07"].row_status == "holiday"
    assert days_by_iso["2026-08-07"].daily_discount == 0.0

    assert days_by_iso["2026-08-08"].day_type == "saturday"
    assert days_by_iso["2026-08-08"].row_status == "absence"
    assert days_by_iso["2026-08-08"].absence_hours_applied == 4.0
    assert days_by_iso["2026-08-08"].daily_discount == 400.0

    assert days_by_iso["2026-08-09"].day_type == "sunday"
    assert days_by_iso["2026-08-09"].row_status == "rest_day"
    assert days_by_iso["2026-08-09"].daily_discount == 0.0

    assert emp.unjustified_absences_count == 2
    assert emp.total_absence_hours == 12.5
    assert emp.absence_discount == 1250.0

    # Ahora justificamos la falta del Jueves 06/08 con 0 min de penalización (incapacidad con goce)
    just_abs = {
        ("1", "2026-08-06", FULL_ABSENCE_SLOT_ID): JustificationInput(
            employee_id="1",
            date="2026-08-06",
            slot_id=FULL_ABSENCE_SLOT_ID,
            justification="Incapacidad médica IMSS",
            penalty_minutes=0,
        )
    }
    res_after = evaluate_attendance_report(parsed, cfg, just_abs)
    emp_after = res_after.employees[0]
    assert emp_after.unjustified_absences_count == 1  # Solo queda el sábado 08/08
    assert emp_after.justified_absences_count == 1
    assert emp_after.absence_discount == 400.0

    # Y probamos el archivo extendido donde Sábado 08/08 sí tiene checadas (Lorena 08:56-13:04, Carlos 09:08-13:05)
    ext_bytes = generate_sample_excel_bytes(include_extra_cases=True)
    parsed_ext = parse_attendance_file(ext_bytes, "Extendida.xlsx")
    res_ext = evaluate_attendance_report(parsed_ext, AttendanceConfig(hourly_wage=120.0), {})
    lorena_sat = next(d for d in res_ext.employees[0].days if d.date == "2026-08-08")
    assert lorena_sat.row_status == "complete"
    assert lorena_sat.expected_punches_count == 2
    assert lorena_sat.total_tardiness_minutes == 0
    assert sum(1 for s in lorena_sat.slots if s.status == "not_applicable") == 4

    carlos_sat = next(d for d in res_ext.employees[1].days if d.date == "2026-08-08")
    assert carlos_sat.row_status == "complete"
    assert carlos_sat.total_tardiness_minutes == 8  # 09:08 vs 09:00


def test_tuesday_power_outage_two_punches_and_batch_justify():
    """
    Verifica el caso crítico donde un Martes (jornada de 6 checadas) por falla eléctrica
    únicamente existen 2 checadas (08:15 entrada y 18:00 salida):
    - No se confunde con Sábado.
    - Asigna 08:15 a Entrada y 18:00 a Salida.
    - Marca exactamente los 4 bloques intermedios como faltantes (2/6).
    - Permite justificar los 4 faltantes del día en un solo paso ('all_missing_day').
    """
    csv_outage = """,ID,Nombre ,Fecha / Hora,Estado,Tipo de Registro
,1,Lorena 1,04/08/2026 08:15,Entrada,Normal
,1,Lorena 1,04/08/2026 18:00,Entrada,Normal
"""
    client = TestClient(app)
    headers = {"X-Session-Id": "pytest-power-outage"}

    # Verificar también encabezados anti-caché en estáticos
    static_resp = client.get("/static/js/app.js")
    assert static_resp.status_code == 200
    assert "no-store" in static_resp.headers.get("cache-control", "")

    up_resp = client.post(
        "/api/upload",
        headers=headers,
        files={"file": ("Martes_Apagon.csv", csv_outage.encode("utf-8"), "text/csv")},
    )
    assert up_resp.status_code == 200
    state = up_resp.json()
    day = state["employees"][0]["days"][0]

    assert day["weekday_name"] == "Mar"
    assert day["day_type"] == "weekday"
    assert day["expected_punches_count"] == 6
    assert day["row_status"] == "incomplete"
    assert day["unjustified_missing_count"] == 4
    assert day["total_tardiness_minutes"] == 0

    slots_by_id = {s["slot_id"]: s for s in day["slots"]}
    assert slots_by_id["entry"]["recorded_time"] == "08:15"
    assert slots_by_id["entry"]["status"] == "ok"
    assert slots_by_id["breakfast_out"]["status"] == "missing"
    assert slots_by_id["breakfast_in"]["status"] == "missing"
    assert slots_by_id["lunch_out"]["status"] == "missing"
    assert slots_by_id["lunch_in"]["status"] == "missing"
    assert slots_by_id["exit"]["recorded_time"] == "18:00"
    assert slots_by_id["exit"]["status"] == "ok"

    # Justificar los 4 faltantes del día en un solo paso con 'all_missing_day' y 10 min penalización única
    batch_resp = client.post(
        "/api/justify",
        headers=headers,
        json={
            "employee_id": "1",
            "date": "2026-08-04",
            "slot_id": "all_missing_day",
            "justification": "Falla en el suministro eléctrico de 09:00 a 17:30 hrs",
            "penalty_minutes": 10,
        },
    )
    assert batch_resp.status_code == 200
    after = batch_resp.json()
    day_after = after["employees"][0]["days"][0]
    assert day_after["row_status"] == "justified_complete"
    assert day_after["unjustified_missing_count"] == 0
    assert day_after["justified_count"] == 4
    # La penalización de 10 min se aplica una sola vez al día, no multiplicada por 4
    assert day_after["total_tardiness_minutes"] == 10


def test_api_flow_holidays_absences_and_protected_pdf():
    """Prueba de extremo a extremo de los endpoints FastAPI (festivos, faltas, PDF AES-256)."""
    client = TestClient(app)
    headers = {"X-Session-Id": "pytest-session-002"}

    home_resp = client.get("/")
    assert home_resp.status_code == 200

    xlsx_bytes = generate_sample_excel_bytes(include_extra_cases=True)
    up_resp = client.post(
        "/api/upload",
        headers=headers,
        files={"file": ("Reporte_Semana.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert up_resp.status_code == 200
    data = up_resp.json()
    assert data["summary"]["unjustified_absences_count"] == 1  # Carlos el Jueves 06/08

    # Justificar la falta completa de Carlos el 06/08 vía API
    just_resp = client.post(
        "/api/justify",
        headers=headers,
        json={
            "employee_id": "2",
            "date": "2026-08-06",
            "slot_id": "full_absence",
            "justification": "Comisión foránea autorizada en planta & almacén",
            "penalty_minutes": 0,
        },
    )
    assert just_resp.status_code == 200
    assert just_resp.json()["summary"]["unjustified_absences_count"] == 0

    # Ampliar el periodo hasta Domingo 09/08 y declarar un festivo + sueldo/hr
    cfg_resp = client.post(
        "/api/config",
        headers=headers,
        json={
            "tardiness_mode": "fixed_time",
            "duplicate_margin_minutes": 5,
            "hourly_wage": 150.0,
            "absence_hours_weekday": 8.0,
            "absence_hours_saturday": 4.0,
            "period_start": "2026-08-03",
            "period_end": "2026-08-09",
            "holidays": [{"date": "2026-08-07", "label": "Día inhábil local"}],
        },
    )
    assert cfg_resp.status_code == 200
    assert cfg_resp.json()["summary"]["holidays_count"] == 1

    # Exportar PDF protegido con todos los tipos de fila presentes (L-V, Sáb, Dom, Festivo, Falta Justificada)
    pdf_resp = client.get("/api/export-pdf", headers=headers)
    assert pdf_resp.status_code == 200
    assert pdf_resp.content.startswith(b"%PDF-")

    reader = PdfReader(io.BytesIO(pdf_resp.content))
    assert reader.is_encrypted is True
    assert reader.decrypt("") != 0
    encrypt_obj = reader.trailer["/Encrypt"].get_object()
    perms = int(encrypt_obj["/P"])
    assert (perms & UserAccessPermissions.PRINT) != 0
    assert (perms & UserAccessPermissions.MODIFY) == 0
    assert (perms & UserAccessPermissions.EXTRACT) == 0
    assert (perms & UserAccessPermissions.ADD_OR_MODIFY) == 0
    assert (perms & UserAccessPermissions.ASSEMBLE_DOC) == 0
