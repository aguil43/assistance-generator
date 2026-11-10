"""
Módulo 1: Ingesta y Limpieza de Archivos del Reloj Checador (excel_parser.py).

Responsabilidades:
- Leer archivos .xlsx (y .csv como soporte adicional) tal como se descargan del reloj checador.
- Detectar dinámicamente la fila de encabezados (ID, Nombre, Fecha / Hora, Estado, Tipo de Registro).
- Filtrar filas vacías, títulos ('Reporte de Eventos'), paginaciones ('Página: 1 / 1'),
  encabezados repetidos en saltos de página y pies de exportación ('Fecha / Hora:26/10/2026...').
- Ignorar las columnas 'Estado' y 'Tipo de Registro'.
- Agrupar las checadas limpias por empleado y por fecha.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import openpyxl


class ExcelParseError(ValueError):
    """Excepción controlada cuando el archivo subido no tiene el formato esperado o carece de registros."""


@dataclass
class ParsedEmployeeRaw:
    """Estructura interna con los registros crudos de un empleado agrupados por día."""

    employee_id: str
    employee_name: str
    punches_by_date: dict[date, list[datetime]] = field(default_factory=dict)


@dataclass
class ParsedAttendanceFile:
    """Resultado limpio de la ingesta de un archivo de reloj checador."""

    source_filename: str
    employees: list[ParsedEmployeeRaw]
    min_date: date
    max_date: date
    total_raw_events: int


_SUPPORTED_DATETIME_FORMATS: tuple[str, ...] = (
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
)


def _normalize_text(value: Any) -> str:
    """Normaliza cadenas quitando acentos, espacios extremos y pasando a minúsculas."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _clean_employee_id(value: Any) -> str:
    """Convierte el ID del empleado a cadena limpia (ej. 1.0 -> '1')."""
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value)
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".")[0]
    return text


def _parse_datetime_cell(value: Any) -> datetime | None:
    """Convierte el valor de la celda 'Fecha / Hora' a objeto datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, 0, 0)
    if isinstance(value, (int, float)):
        # Soporte para fechas seriales de Excel (base 1899-12-30)
        if 20000 < float(value) < 80000:
            base = datetime(1899, 12, 30)
            return base + timedelta(days=float(value))
        return None

    raw_str = str(value).strip()
    if not raw_str:
        return None

    # Limpiar espacios múltiples internos
    raw_str = re.sub(r"\s+", " ", raw_str)

    for fmt in _SUPPORTED_DATETIME_FORMATS:
        try:
            return datetime.strptime(raw_str, fmt)
        except ValueError:
            continue

    return None


def _detect_header_columns(row: list[Any]) -> dict[str, int] | None:
    """
    Detecta si una fila corresponde al encabezado de columnas del reloj checador.
    Retorna el mapeo {'id': idx, 'name': idx, 'datetime': idx} o None.
    """
    normalized = [_normalize_text(cell) for cell in row]
    id_idx: int | None = None
    name_idx: int | None = None
    dt_idx: int | None = None

    for idx, text in enumerate(normalized):
        if not text:
            continue
        if id_idx is None and text in ("id", "id empleado", "no.", "num", "codigo"):
            id_idx = idx
        elif name_idx is None and ("nombre" in text or "empleado" in text):
            name_idx = idx
        elif dt_idx is None and ("fecha" in text or "hora" in text or "tiempo" in text):
            # Evitar falsos positivos con el pie de página "Fecha / Hora:26/10/2026..."
            if ":" not in str(row[idx]):
                dt_idx = idx

    if id_idx is not None and name_idx is not None and dt_idx is not None:
        return {"id": id_idx, "name": name_idx, "datetime": dt_idx}
    return None


def _is_noise_row(row: list[Any]) -> bool:
    """Identifica filas vacías, títulos o pies de página del reporte de reloj checador."""
    non_empty = [str(cell).strip() for cell in row if cell is not None and str(cell).strip() != ""]
    if not non_empty:
        return True

    joined_norm = _normalize_text(" ".join(non_empty))
    if "reporte de eventos" in joined_norm:
        return True
    if joined_norm.startswith("pagina:") or "pagina:" in joined_norm:
        return True
    if joined_norm.startswith("fecha / hora:") or joined_norm.startswith("fecha/hora:"):
        return True
    return False


def _extract_rows_from_xlsx(file_bytes: bytes) -> list[list[Any]]:
    """Extrae todas las filas de todas las hojas de un archivo Excel .xlsx."""
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    except Exception as exc:
        raise ExcelParseError(
            "No fue posible abrir el archivo Excel. Verifique que sea un archivo .xlsx válido."
        ) from exc

    all_rows: list[list[Any]] = []
    try:
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(values_only=True):
                all_rows.append(list(row))
    finally:
        workbook.close()
    return all_rows


def _extract_rows_from_csv(file_bytes: bytes) -> list[list[Any]]:
    """Extrae filas desde un archivo CSV probando codificaciones habituales en español."""
    decoded: str | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            decoded = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    if decoded is None:
        raise ExcelParseError("No se pudo decodificar el archivo CSV.")

    reader = csv.reader(io.StringIO(decoded))
    return [list(row) for row in reader]


def parse_attendance_file(file_bytes: bytes, filename: str = "reporte.xlsx") -> ParsedAttendanceFile:
    """
    Procesa los bytes del archivo del reloj checador y devuelve los eventos limpios
    agrupados por empleado y día.
    """
    if not file_bytes:
        raise ExcelParseError("El archivo subido está vacío.")

    lower_name = filename.lower().strip()
    if lower_name.endswith(".csv"):
        rows = _extract_rows_from_csv(file_bytes)
    elif lower_name.endswith((".xlsx", ".xlsm")):
        rows = _extract_rows_from_xlsx(file_bytes)
    else:
        # Intentar primero como xlsx y luego como csv
        try:
            rows = _extract_rows_from_xlsx(file_bytes)
        except ExcelParseError:
            rows = _extract_rows_from_csv(file_bytes)

    header_map: dict[str, int] | None = None
    employees_map: dict[str, ParsedEmployeeRaw] = {}
    all_dates: list[date] = []
    total_events = 0

    for row in rows:
        if _is_noise_row(row):
            continue

        detected = _detect_header_columns(row)
        if detected is not None:
            header_map = detected
            continue

        if header_map is None:
            continue

        max_needed = max(header_map.values())
        if len(row) <= max_needed:
            continue

        emp_id = _clean_employee_id(row[header_map["id"]])
        emp_name_raw = row[header_map["name"]]
        emp_name = str(emp_name_raw).strip() if emp_name_raw is not None else ""
        dt_val = _parse_datetime_cell(row[header_map["datetime"]])

        if not emp_id or not emp_name or dt_val is None:
            continue

        if emp_id not in employees_map:
            employees_map[emp_id] = ParsedEmployeeRaw(
                employee_id=emp_id,
                employee_name=emp_name,
            )

        day_key = dt_val.date()
        employees_map[emp_id].punches_by_date.setdefault(day_key, []).append(dt_val)
        all_dates.append(day_key)
        total_events += 1

    if header_map is None:
        raise ExcelParseError(
            "No se encontraron las columnas requeridas ('ID', 'Nombre', 'Fecha / Hora') en el archivo."
        )

    if total_events == 0 or not employees_map:
        raise ExcelParseError(
            "El archivo tiene encabezados válidos pero no contiene registros de asistencia."
        )

    # Ordenar empleados por ID numérico (si aplica) o alfabético, y ordenar sus fechas/horas
    def _emp_sort_key(emp: ParsedEmployeeRaw) -> tuple[int, int | str]:
        if emp.employee_id.isdigit():
            return (0, int(emp.employee_id))
        return (1, emp.employee_id)

    sorted_employees = sorted(employees_map.values(), key=_emp_sort_key)
    for emp in sorted_employees:
        sorted_dates = sorted(emp.punches_by_date.keys())
        emp.punches_by_date = {
            d: sorted(emp.punches_by_date[d]) for d in sorted_dates
        }

    return ParsedAttendanceFile(
        source_filename=filename,
        employees=sorted_employees,
        min_date=min(all_dates),
        max_date=max(all_dates),
        total_raw_events=total_events,
    )


def generate_sample_excel_bytes(include_extra_cases: bool = False) -> bytes:
    """
    Genera en memoria un archivo .xlsx con la estructura exacta del reloj checador
    proporcionada en la especificación (celdas desplazadas, título, pie de página).
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Reporte de Eventos"

    # Filas superiores tal como las exporta el reloj checador
    ws.append([None] * 8)
    ws.append([None, "Reporte de Eventos", None, None, None, None, None, None])
    ws.append([None] * 8)
    ws.append([None] * 8)
    ws.append([None] * 8)
    ws.append([None, "ID", "Nombre ", "Fecha / Hora", "Estado", None, None, "Tipo de Registro"])

    # Datos exactos del archivo adjunto por el usuario
    sample_events = [
        ("1", "Lorena 1", "03/08/2026 07:50"),
        ("1", "Lorena 1", "03/08/2026 09:43"),
        ("1", "Lorena 1", "03/08/2026 09:54"),
        ("1", "Lorena 1", "03/08/2026 14:03"),
        ("1", "Lorena 1", "03/08/2026 15:00"),
        ("1", "Lorena 1", "03/08/2026 18:16"),
        ("1", "Lorena 1", "04/08/2026 07:54"),
        ("1", "Lorena 1", "04/08/2026 09:43"),
        ("1", "Lorena 1", "04/08/2026 09:54"),
        ("1", "Lorena 1", "04/08/2026 14:04"),
        ("1", "Lorena 1", "04/08/2026 15:02"),
        ("1", "Lorena 1", "04/08/2026 18:00"),
        ("1", "Lorena 1", "05/08/2026 08:06"),
        ("1", "Lorena 1", "05/08/2026 09:41"),
        ("1", "Lorena 1", "05/08/2026 09:52"),
        ("1", "Lorena 1", "05/08/2026 14:03"),
        ("1", "Lorena 1", "05/08/2026 14:50"),
    ]

    if include_extra_cases:
        sample_events.extend([
            # Lorena 1 continúa Jueves 06/08, Viernes 07/08 y Sábado 08/08 (09:00 a 13:00)
            ("1", "Lorena 1", "06/08/2026 08:10"),
            ("1", "Lorena 1", "06/08/2026 09:42"),
            ("1", "Lorena 1", "06/08/2026 09:58"),
            ("1", "Lorena 1", "06/08/2026 14:01"),
            ("1", "Lorena 1", "06/08/2026 15:00"),
            ("1", "Lorena 1", "06/08/2026 18:05"),
            ("1", "Lorena 1", "07/08/2026 08:14"),
            ("1", "Lorena 1", "07/08/2026 09:40"),
            ("1", "Lorena 1", "07/08/2026 09:59"),
            ("1", "Lorena 1", "07/08/2026 14:00"),
            ("1", "Lorena 1", "07/08/2026 14:58"),
            ("1", "Lorena 1", "07/08/2026 18:12"),
            # Sábado 08/08/2026 (solo 2 checadas: 09:00 y 13:00)
            ("1", "Lorena 1", "08/08/2026 08:56"),
            ("1", "Lorena 1", "08/08/2026 13:04"),

            # Empleado 2 (Carlos 2): checada doble Lun 03/08, falta regreso comida Mar 04/08,
            # FALTA COMPLETA el Jueves 06/08 (0 checadas) y retardo en Sábado 08/08
            ("2", "Carlos 2", "03/08/2026 08:16"),
            ("2", "Carlos 2", "03/08/2026 08:18"),  # Duplicado dentro del margen de 5 min
            ("2", "Carlos 2", "03/08/2026 09:40"),
            ("2", "Carlos 2", "03/08/2026 10:04"),  # 4 min retardo almuerzo
            ("2", "Carlos 2", "03/08/2026 14:01"),
            ("2", "Carlos 2", "03/08/2026 15:05"),  # 5 min retardo comida
            ("2", "Carlos 2", "03/08/2026 18:10"),
            ("2", "Carlos 2", "04/08/2026 08:12"),
            ("2", "Carlos 2", "04/08/2026 09:42"),
            ("2", "Carlos 2", "04/08/2026 09:59"),
            ("2", "Carlos 2", "04/08/2026 14:02"),
            # Falta 15:00 hrs regreso de comida el 04/08
            ("2", "Carlos 2", "04/08/2026 18:05"),
            ("2", "Carlos 2", "05/08/2026 08:11"),
            ("2", "Carlos 2", "05/08/2026 09:41"),
            ("2", "Carlos 2", "05/08/2026 09:57"),
            ("2", "Carlos 2", "05/08/2026 14:03"),
            ("2", "Carlos 2", "05/08/2026 14:59"),
            ("2", "Carlos 2", "05/08/2026 18:03"),
            # Jueves 06/08/2026 -> 0 checadas (FALTA COMPLETA salvo que sea festivo o se justifique)
            ("2", "Carlos 2", "07/08/2026 08:13"),
            ("2", "Carlos 2", "07/08/2026 09:41"),
            ("2", "Carlos 2", "07/08/2026 09:58"),
            ("2", "Carlos 2", "07/08/2026 14:02"),
            ("2", "Carlos 2", "07/08/2026 14:59"),
            ("2", "Carlos 2", "07/08/2026 18:06"),
            # Sábado 08/08/2026 -> Entrada con retardo (09:08 -> +8 min) y salida 13:05
            ("2", "Carlos 2", "08/08/2026 09:08"),
            ("2", "Carlos 2", "08/08/2026 13:05"),
        ])

    for emp_id, emp_name, dt_str in sample_events:
        ws.append([None, int(emp_id), emp_name, dt_str, "Entrada", None, None, "Normal"])

    ws.append([None, None, None, None, None, None, "Página: 1 / 1", None])
    for _ in range(10):
        ws.append([None] * 8)
    ws.append(["Fecha / Hora:26/10/2026 15:27", None, None, None, None, None, None, None])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
