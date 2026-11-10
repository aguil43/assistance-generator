"""
Módulo 4: Generador de Reporte PDF Protegido y Anti-Manipulación (pdf_generator.py).

Responsabilidades:
- Generar el documento PDF formal con diseño claro y sin ambigüedades por empleado:
    1) Tabla de Jornada Lunes a Viernes (6 checadas: 08:15, 09:40, 10:00, 14:00, 15:00, 18:00+)
       con estado 'Completo (6/6)'.
    2) Sub-tabla exclusiva de Jornada Sábados (2 checadas: Entrada 09:00 y Salida 13:00+)
       con estado 'Completo (2/2)', evitando mezclar horarios de 13:00 bajo columnas de 18:00+.
    3) Exclusión limpia de Domingos de descanso (salvo registro extraordinario) y marcado de Festivos.
- Aplicar código de colores estricto (Rojo para faltantes/faltas, Ámbar para columnas/celdas con notas)
  y blindaje AES-256 + malla vectorial Guilloché y sello SHA-256.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import secrets
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

from pypdf import PdfReader, PdfWriter
from pypdf.constants import UserAccessPermissions
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.config import OFFICIAL_SLOTS
from app.models.schemas import DayEvaluation, EmployeeReport, ReportStateResponse


# Paleta institucional y de estados
COLOR_NAVY = colors.HexColor("#0F172A")
COLOR_SLATE_HEADER = colors.HexColor("#1E293B")
COLOR_SLATE_SUB = colors.HexColor("#334155")
COLOR_SAT_HEADER = colors.HexColor("#0F766E")
COLOR_BORDER = colors.HexColor("#CBD5E1")
COLOR_ZEBRA = colors.HexColor("#F8FAFC")

# Festivo
COLOR_HOLIDAY_BG = colors.HexColor("#E0F2FE")
COLOR_HOLIDAY_TEXT = colors.HexColor("#0369A1")
COLOR_HOLIDAY_BORDER = colors.HexColor("#38BDF8")

# Rojo para registros faltantes y Faltas completas injustificadas
COLOR_MISSING_BG = colors.HexColor("#FADBD8")
COLOR_MISSING_ROW_BG = colors.HexColor("#FDEDEC")
COLOR_MISSING_TEXT = colors.HexColor("#922B21")
COLOR_MISSING_BORDER = colors.HexColor("#E74C3C")

# Color distintivo (Ámbar / Dorado intenso) para columnas y celdas con Notas / Justificaciones
COLOR_NOTE_COL_HEADER = colors.HexColor("#B45309")
COLOR_NOTE_COL_TINT = colors.HexColor("#FFFBEB")
COLOR_NOTE_CELL_BG = colors.HexColor("#FDE68A")
COLOR_NOTE_TEXT = colors.HexColor("#78350F")
COLOR_NOTE_BORDER = colors.HexColor("#D97706")

# Naranja / Coral suave para checadas con retardo
COLOR_LATE_BG = colors.HexColor("#FFEDD5")
COLOR_LATE_TEXT = colors.HexColor("#9A3412")


def compute_report_integrity_hash(state: ReportStateResponse) -> str:
    """Calcula un sello SHA-256 determinista sobre todas las cifras y notas del reporte."""
    payload = {
        "config": state.config.model_dump(),
        "summary": state.summary.model_dump() if state.summary else {},
        "employees": [emp.model_dump() for emp in state.employees],
        "notes": [note.model_dump() for note in state.notes],
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _draw_tamper_evident_canvas(
    canvas: Canvas,
    doc: SimpleDocTemplate,
    integrity_hash: str,
    generated_at: str,
) -> None:
    """Dibuja malla Guilloché sinusoidal continua y microtexto entrelazado anti-alteración."""
    canvas.saveState()
    page_w, page_h = landscape(letter)

    seed_a = int(integrity_hash[:4], 16) % 17 + 7
    seed_b = int(integrity_hash[4:8], 16) % 13 + 5

    canvas.setLineWidth(0.22)
    canvas.setStrokeColor(colors.Color(0.82, 0.86, 0.92, alpha=0.42))

    step_x = 8
    for base_y in range(28, int(page_h) - 24, 18):
        path = canvas.beginPath()
        first = True
        for x in range(18, int(page_w) - 18, step_x):
            wave_1 = math.sin((x + seed_a * 11) / 34.0) * 4.2
            wave_2 = math.cos((x - base_y + seed_b * 7) / 19.0) * 2.1
            y = base_y + wave_1 + wave_2
            if first:
                path.moveTo(x, y)
                first = False
            else:
                path.lineTo(x, y)
        canvas.drawPath(path, stroke=1, fill=0)

    short_hash = integrity_hash[:16].upper()
    watermark_str = f"REPORTE PROTEGIDO · INTEGRIDAD SHA256:{short_hash} · PROHIBIDA SU MODIFICACION"
    canvas.setFont("Helvetica", 5.5)
    canvas.setFillColor(colors.Color(0.75, 0.79, 0.85, alpha=0.35))

    for x_pos in range(-80, int(page_w) + 120, 250):
        for y_pos in range(50, int(page_h) - 40, 110):
            canvas.saveState()
            canvas.translate(x_pos, y_pos)
            canvas.rotate(19)
            canvas.drawString(0, 0, watermark_str)
            canvas.restoreState()

    canvas.setStrokeColor(colors.HexColor("#94A3B8"))
    canvas.setLineWidth(0.5)
    canvas.rect(18, 18, page_w - 36, page_h - 36, stroke=1, fill=0)
    canvas.setLineWidth(0.25)
    canvas.rect(20.5, 20.5, page_w - 41, page_h - 41, stroke=1, fill=0)

    canvas.setFillColor(COLOR_SLATE_SUB)
    canvas.setFont("Helvetica", 7)
    footer_left = (
        f"Sello de Integridad SHA-256: {integrity_hash[:32].upper()}  |  "
        f"Generado: {generated_at}"
    )
    footer_right = f"Página {canvas.getPageNumber()} · Documento protegido contra modificaciones"
    canvas.drawString(28, 25, footer_left)
    canvas.drawRightString(page_w - 28, 25, footer_right)

    canvas.restoreState()


def _build_weekday_table(
    weekday_days: list[DayEvaluation],
    styles: dict[str, ParagraphStyle],
) -> Table:
    """Construye la tabla exclusiva de Lunes a Viernes (6 checadas: 08:15 a 18:00+)."""
    slot_cols_with_notes: set[int] = set()
    has_any_notes = False
    for day in weekday_days:
        if day.has_notes:
            has_any_notes = True
        if day.row_status == "justified_complete":
            for idx, slot in enumerate(day.slots):
                if slot.status == "justified" or slot.note_number is not None:
                    slot_cols_with_notes.add(idx + 1)

    headers = [Paragraph("<b>Día / Fecha (L–V)</b>", styles["th"])]
    for idx, slot_def in enumerate(OFFICIAL_SLOTS):
        col_idx = idx + 1
        label = slot_def.short_label
        if col_idx in slot_cols_with_notes:
            label = f"{label}<br/><font size='5.8'>[COLUMNA CON NOTA]</font>"
        headers.append(Paragraph(f"<b>{label}</b>", styles["th"]))

    headers.extend(
        [
            Paragraph("<b>Retardo /<br/>Hrs Falta</b>", styles["th"]),
            Paragraph("<b>Descuento<br/>($)</b>", styles["th"]),
            Paragraph(
                "<b>Estado L–V</b>"
                + ("<br/><font size='5.8'>[CON NOTAS]</font>" if has_any_notes else ""),
                styles["th"],
            ),
        ]
    )

    rows_data: list[list] = [headers]
    style_cmds: list[tuple] = [
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_SLATE_SUB),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.45, COLOR_BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]

    for note_col in slot_cols_with_notes:
        style_cmds.append(("BACKGROUND", (note_col, 0), (note_col, 0), COLOR_NOTE_COL_HEADER))
        style_cmds.append(("BACKGROUND", (note_col, 1), (note_col, len(weekday_days)), COLOR_NOTE_COL_TINT))
        style_cmds.append(("BOX", (note_col, 0), (note_col, len(weekday_days)), 0.9, COLOR_NOTE_BORDER))

    if has_any_notes and weekday_days:
        style_cmds.append(("BACKGROUND", (9, 0), (9, 0), COLOR_NOTE_COL_HEADER))
        style_cmds.append(("BACKGROUND", (9, 1), (9, len(weekday_days)), COLOR_NOTE_COL_TINT))

    for day_idx, day in enumerate(weekday_days, start=1):
        date_txt = f"<b>{day.weekday_name} {day.display_date}</b>"
        if day.discarded_duplicates:
            date_txt += f"<br/><font size='5.8' color='#64748B'>(Dup.: {', '.join(day.discarded_duplicates)})</font>"

        if day.row_status == "holiday":
            h_lbl = xml_escape(day.holiday_label or "Día festivo oficial")
            rows_data.append(
                [
                    Paragraph(date_txt, styles["td_holiday"]),
                    Paragraph(f"<b>DÍA FESTIVO OFICIAL:</b> {h_lbl}", styles["td_holiday"]),
                    Paragraph("", styles["td_holiday"]),
                    Paragraph("", styles["td_holiday"]),
                    Paragraph("", styles["td_holiday"]),
                    Paragraph("", styles["td_holiday"]),
                    Paragraph("", styles["td_holiday"]),
                    Paragraph("0 min", styles["td_holiday"]),
                    Paragraph("$0.00", styles["td_holiday"]),
                    Paragraph("<b>FESTIVO</b>", styles["td_holiday"]),
                ]
            )
            style_cmds.append(("SPAN", (1, day_idx), (6, day_idx)))
            style_cmds.append(("BACKGROUND", (0, day_idx), (-1, day_idx), COLOR_HOLIDAY_BG))
            style_cmds.append(("BOX", (0, day_idx), (-1, day_idx), 0.7, COLOR_HOLIDAY_BORDER))
            continue

        if day.row_status == "absence":
            rows_data.append(
                [
                    Paragraph(date_txt, styles["td_missing"]),
                    Paragraph("<b>FALTA INJUSTIFICADA — SIN CHECADAS EN JORNADA L–V</b>", styles["td_missing"]),
                    Paragraph("", styles["td_missing"]),
                    Paragraph("", styles["td_missing"]),
                    Paragraph("", styles["td_missing"]),
                    Paragraph("", styles["td_missing"]),
                    Paragraph("", styles["td_missing"]),
                    Paragraph(f"<b>-{day.absence_hours_applied:g} hrs</b>", styles["td_missing"]),
                    Paragraph(f"<b>${day.daily_discount:,.2f}</b>", styles["td_missing"]),
                    Paragraph("<b>FALTA</b>", styles["td_missing"]),
                ]
            )
            style_cmds.append(("SPAN", (1, day_idx), (6, day_idx)))
            style_cmds.append(("BACKGROUND", (0, day_idx), (-1, day_idx), COLOR_MISSING_BG))
            style_cmds.append(("BOX", (0, day_idx), (-1, day_idx), 0.9, COLOR_MISSING_BORDER))
            continue

        if day.row_status == "justified_absence":
            note_num = next((s.note_number for s in day.slots if s.note_number is not None), 1)
            pen_str = (
                f"+{day.penalty_tardiness_minutes} min"
                if day.penalty_tardiness_minutes > 0
                else "0 min (Sin desc.)"
            )
            rows_data.append(
                [
                    Paragraph(date_txt, styles["td_note"]),
                    Paragraph(f"<b>FALTA JUSTIFICADA [Nota #{note_num}]</b>", styles["td_note"]),
                    Paragraph("", styles["td_note"]),
                    Paragraph("", styles["td_note"]),
                    Paragraph("", styles["td_note"]),
                    Paragraph("", styles["td_note"]),
                    Paragraph("", styles["td_note"]),
                    Paragraph(f"<b>{pen_str}</b>", styles["td_note"]),
                    Paragraph(f"<b>${day.daily_discount:,.2f}</b>", styles["td_note"]),
                    Paragraph(f"<b>FALTA JUSTIF.</b><br/><font size='6.3'>Nota #{note_num}</font>", styles["td_note"]),
                ]
            )
            style_cmds.append(("SPAN", (1, day_idx), (6, day_idx)))
            style_cmds.append(("BACKGROUND", (0, day_idx), (-1, day_idx), COLOR_NOTE_CELL_BG))
            style_cmds.append(("BOX", (0, day_idx), (-1, day_idx), 0.95, COLOR_NOTE_BORDER))
            continue

        if day.row_status == "incomplete":
            style_cmds.append(("BACKGROUND", (0, day_idx), (0, day_idx), COLOR_MISSING_ROW_BG))
            style_cmds.append(("BACKGROUND", (7, day_idx), (9, day_idx), COLOR_MISSING_ROW_BG))
            for c_i in range(1, 7):
                if c_i not in slot_cols_with_notes:
                    style_cmds.append(("BACKGROUND", (c_i, day_idx), (c_i, day_idx), COLOR_MISSING_ROW_BG))
        elif day_idx % 2 == 0:
            for c_i in range(0, 10):
                if c_i not in slot_cols_with_notes and not (c_i == 9 and has_any_notes):
                    style_cmds.append(("BACKGROUND", (c_i, day_idx), (c_i, day_idx), COLOR_ZEBRA))

        row_cells: list = [Paragraph(date_txt, styles["td"])]
        row_notes_labels: list[str] = []

        for s_idx, slot_eval in enumerate(day.slots):
            col_idx = s_idx + 1
            if slot_eval.status == "missing":
                cell_p = Paragraph("<b>SIN REGISTRO</b>", styles["td_missing"])
                style_cmds.append(("BACKGROUND", (col_idx, day_idx), (col_idx, day_idx), COLOR_MISSING_BG))
                style_cmds.append(("BOX", (col_idx, day_idx), (col_idx, day_idx), 0.85, COLOR_MISSING_BORDER))
            elif slot_eval.status == "justified":
                pen_txt = f" (+{slot_eval.penalty_minutes}m)" if slot_eval.penalty_minutes > 0 else " (0m)"
                cell_p = Paragraph(
                    f"<b>JUSTIFICADO</b><br/><font size='6.3'>[Nota #{slot_eval.note_number}]{pen_txt}</font>",
                    styles["td_note"],
                )
                row_notes_labels.append(f"Nota #{slot_eval.note_number}")
                style_cmds.append(("BACKGROUND", (col_idx, day_idx), (col_idx, day_idx), COLOR_NOTE_CELL_BG))
                style_cmds.append(("BOX", (col_idx, day_idx), (col_idx, day_idx), 0.95, COLOR_NOTE_BORDER))
            elif slot_eval.status == "late":
                cell_p = Paragraph(
                    f"<b>{slot_eval.recorded_time}</b><br/><font size='6.3'>(+{slot_eval.tardiness_minutes} min)</font>",
                    styles["td_late"],
                )
                if col_idx not in slot_cols_with_notes:
                    style_cmds.append(("BACKGROUND", (col_idx, day_idx), (col_idx, day_idx), COLOR_LATE_BG))
            elif slot_eval.status == "early_exit":
                cell_p = Paragraph(
                    f"<b>{slot_eval.recorded_time}</b><br/><font size='6'>(Antes 18:00)</font>",
                    styles["td_late"],
                )
            else:
                cell_p = Paragraph(slot_eval.recorded_time or "-", styles["td"])

            row_cells.append(cell_p)

        if day.total_tardiness_minutes > 0:
            late_str = f"<b>{day.total_tardiness_minutes} min</b>"
            if day.penalty_tardiness_minutes > 0 and day.schedule_tardiness_minutes > 0:
                late_str += f"<br/><font size='6'>({day.schedule_tardiness_minutes}h+{day.penalty_tardiness_minutes}p)</font>"
            row_cells.append(Paragraph(late_str, styles["td_late"]))
        else:
            row_cells.append(Paragraph("0 min", styles["td"]))

        disc_str = f"<b>${day.daily_discount:,.2f}</b>" if day.daily_discount > 0 else "$0.00"
        row_cells.append(Paragraph(disc_str, styles["td"]))

        if day.row_status == "incomplete":
            recorded_cnt = 6 - day.unjustified_missing_count - day.justified_count
            status_str = f"<b>INCOMPLETO ({recorded_cnt}/6)</b><br/><font size='6.3'>Faltan {day.unjustified_missing_count}</font>"
            row_cells.append(Paragraph(status_str, styles["td_missing"]))
            style_cmds.append(("BACKGROUND", (9, day_idx), (9, day_idx), COLOR_MISSING_BG))
        elif row_notes_labels:
            status_str = f"<b>JUSTIFICADO (6/6)</b><br/><font size='6.3'>{', '.join(row_notes_labels)}</font>"
            row_cells.append(Paragraph(status_str, styles["td_note"]))
            style_cmds.append(("BACKGROUND", (9, day_idx), (9, day_idx), COLOR_NOTE_CELL_BG))
            style_cmds.append(("BOX", (9, day_idx), (9, day_idx), 0.9, COLOR_NOTE_BORDER))
        else:
            row_cells.append(Paragraph("Completo (6/6)", styles["td_ok"]))

        rows_data.append(row_cells)

    col_widths = [
        1.05 * inch,
        0.96 * inch,
        0.94 * inch,
        0.94 * inch,
        0.94 * inch,
        0.94 * inch,
        0.98 * inch,
        0.98 * inch,
        0.97 * inch,
        1.50 * inch,
    ]
    tbl = Table(rows_data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle(style_cmds))
    return tbl


def _build_saturday_table(
    saturday_days: list[DayEvaluation],
    styles: dict[str, ParagraphStyle],
) -> Table:
    """Construye la sub-tabla exclusiva de Sábados (2 checadas: Entrada 09:00 y Salida 13:00+)."""
    sat_note_cols: set[int] = set()
    has_any_notes = False
    for day in saturday_days:
        if day.has_notes:
            has_any_notes = True
        if day.row_status == "justified_complete":
            if day.slots[0].status == "justified":
                sat_note_cols.add(1)
            if day.slots[5].status == "justified":
                sat_note_cols.add(2)

    headers = [
        Paragraph("<b>Día / Fecha (Sábado)</b>", styles["th"]),
        Paragraph(
            "<b>Entrada Sábado (09:00)</b>"
            + ("<br/><font size='5.8'>[CON NOTA]</font>" if 1 in sat_note_cols else ""),
            styles["th"],
        ),
        Paragraph(
            "<b>Salida Sábado (13:00+)</b>"
            + ("<br/><font size='5.8'>[CON NOTA]</font>" if 2 in sat_note_cols else ""),
            styles["th"],
        ),
        Paragraph("<b>Retardo /<br/>Hrs Falta</b>", styles["th"]),
        Paragraph("<b>Descuento<br/>($)</b>", styles["th"]),
        Paragraph(
            "<b>Estado Sábado</b>"
            + ("<br/><font size='5.8'>[CON NOTAS]</font>" if has_any_notes else ""),
            styles["th"],
        ),
    ]

    rows_data: list[list] = [headers]
    style_cmds: list[tuple] = [
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_SAT_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.45, COLOR_BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]

    for note_col in sat_note_cols:
        style_cmds.append(("BACKGROUND", (note_col, 0), (note_col, 0), COLOR_NOTE_COL_HEADER))
        style_cmds.append(("BACKGROUND", (note_col, 1), (note_col, len(saturday_days)), COLOR_NOTE_COL_TINT))
        style_cmds.append(("BOX", (note_col, 0), (note_col, len(saturday_days)), 0.9, COLOR_NOTE_BORDER))

    if has_any_notes and saturday_days:
        style_cmds.append(("BACKGROUND", (5, 0), (5, 0), COLOR_NOTE_COL_HEADER))
        style_cmds.append(("BACKGROUND", (5, 1), (5, len(saturday_days)), COLOR_NOTE_COL_TINT))

    for day_idx, day in enumerate(saturday_days, start=1):
        date_txt = f"<b>{day.weekday_name} {day.display_date}</b>"
        if day.discarded_duplicates:
            date_txt += f"<br/><font size='5.8' color='#64748B'>(Dup.: {', '.join(day.discarded_duplicates)})</font>"

        if day.row_status == "holiday":
            h_lbl = xml_escape(day.holiday_label or "Día festivo oficial")
            rows_data.append(
                [
                    Paragraph(date_txt, styles["td_holiday"]),
                    Paragraph(f"<b>DÍA FESTIVO OFICIAL:</b> {h_lbl}", styles["td_holiday"]),
                    Paragraph("", styles["td_holiday"]),
                    Paragraph("0 min", styles["td_holiday"]),
                    Paragraph("$0.00", styles["td_holiday"]),
                    Paragraph("<b>FESTIVO</b>", styles["td_holiday"]),
                ]
            )
            style_cmds.append(("SPAN", (1, day_idx), (2, day_idx)))
            style_cmds.append(("BACKGROUND", (0, day_idx), (-1, day_idx), COLOR_HOLIDAY_BG))
            continue

        if day.row_status == "absence":
            rows_data.append(
                [
                    Paragraph(date_txt, styles["td_missing"]),
                    Paragraph("<b>FALTA INJUSTIFICADA — SIN CHECADAS EN SÁBADO (09:00 a 13:00)</b>", styles["td_missing"]),
                    Paragraph("", styles["td_missing"]),
                    Paragraph(f"<b>-{day.absence_hours_applied:g} hrs</b>", styles["td_missing"]),
                    Paragraph(f"<b>${day.daily_discount:,.2f}</b>", styles["td_missing"]),
                    Paragraph("<b>FALTA (0/2)</b>", styles["td_missing"]),
                ]
            )
            style_cmds.append(("SPAN", (1, day_idx), (2, day_idx)))
            style_cmds.append(("BACKGROUND", (0, day_idx), (-1, day_idx), COLOR_MISSING_BG))
            style_cmds.append(("BOX", (0, day_idx), (-1, day_idx), 0.9, COLOR_MISSING_BORDER))
            continue

        if day.row_status == "justified_absence":
            note_num = next((s.note_number for s in day.slots if s.note_number is not None), 1)
            pen_str = f"+{day.penalty_tardiness_minutes} min" if day.penalty_tardiness_minutes > 0 else "0 min (Sin desc.)"
            rows_data.append(
                [
                    Paragraph(date_txt, styles["td_note"]),
                    Paragraph(f"<b>FALTA SABATINA JUSTIFICADA [Nota #{note_num}]</b>", styles["td_note"]),
                    Paragraph("", styles["td_note"]),
                    Paragraph(f"<b>{pen_str}</b>", styles["td_note"]),
                    Paragraph(f"<b>${day.daily_discount:,.2f}</b>", styles["td_note"]),
                    Paragraph(f"<b>FALTA JUSTIF.</b><br/><font size='6.3'>Nota #{note_num}</font>", styles["td_note"]),
                ]
            )
            style_cmds.append(("SPAN", (1, day_idx), (2, day_idx)))
            style_cmds.append(("BACKGROUND", (0, day_idx), (-1, day_idx), COLOR_NOTE_CELL_BG))
            style_cmds.append(("BOX", (0, day_idx), (-1, day_idx), 0.95, COLOR_NOTE_BORDER))
            continue

        row_cells: list = [Paragraph(date_txt, styles["td"])]
        row_notes_labels: list[str] = []
        sat_slots = [day.slots[0], day.slots[5]]  # Entrada (09:00) y Salida (13:00+)

        for s_pos, slot_eval in enumerate(sat_slots, start=1):
            if slot_eval.status == "missing":
                cell_p = Paragraph("<b>SIN REGISTRO</b>", styles["td_missing"])
                style_cmds.append(("BACKGROUND", (s_pos, day_idx), (s_pos, day_idx), COLOR_MISSING_BG))
                style_cmds.append(("BOX", (s_pos, day_idx), (s_pos, day_idx), 0.85, COLOR_MISSING_BORDER))
            elif slot_eval.status == "justified":
                pen_txt = f" (+{slot_eval.penalty_minutes}m)" if slot_eval.penalty_minutes > 0 else " (0m)"
                cell_p = Paragraph(
                    f"<b>JUSTIFICADO</b><br/><font size='6.3'>[Nota #{slot_eval.note_number}]{pen_txt}</font>",
                    styles["td_note"],
                )
                row_notes_labels.append(f"Nota #{slot_eval.note_number}")
                style_cmds.append(("BACKGROUND", (s_pos, day_idx), (s_pos, day_idx), COLOR_NOTE_CELL_BG))
                style_cmds.append(("BOX", (s_pos, day_idx), (s_pos, day_idx), 0.95, COLOR_NOTE_BORDER))
            elif slot_eval.status == "late":
                cell_p = Paragraph(
                    f"<b>{slot_eval.recorded_time}</b><br/><font size='6.3'>(+{slot_eval.tardiness_minutes} min)</font>",
                    styles["td_late"],
                )
                style_cmds.append(("BACKGROUND", (s_pos, day_idx), (s_pos, day_idx), COLOR_LATE_BG))
            elif slot_eval.status == "early_exit":
                cell_p = Paragraph(
                    f"<b>{slot_eval.recorded_time}</b><br/><font size='6'>(Antes 13:00)</font>",
                    styles["td_late"],
                )
            else:
                cell_p = Paragraph(slot_eval.recorded_time or "-", styles["td"])
            row_cells.append(cell_p)

        late_txt = f"<b>{day.total_tardiness_minutes} min</b>" if day.total_tardiness_minutes > 0 else "0 min"
        row_cells.append(Paragraph(late_txt, styles["td_late"] if day.total_tardiness_minutes > 0 else styles["td"]))
        disc_txt = f"<b>${day.daily_discount:,.2f}</b>" if day.daily_discount > 0 else "$0.00"
        row_cells.append(Paragraph(disc_txt, styles["td"]))

        if day.row_status == "incomplete":
            row_cells.append(Paragraph("<b>INCOMPLETO (1/2)</b><br/><font size='6.3'>Falta 1 checada</font>", styles["td_missing"]))
            style_cmds.append(("BACKGROUND", (5, day_idx), (5, day_idx), COLOR_MISSING_BG))
        elif row_notes_labels:
            row_cells.append(Paragraph(f"<b>JUSTIFICADO (2/2)</b><br/><font size='6.3'>{', '.join(row_notes_labels)}</font>", styles["td_note"]))
            style_cmds.append(("BACKGROUND", (5, day_idx), (5, day_idx), COLOR_NOTE_CELL_BG))
        else:
            row_cells.append(Paragraph("Completo (2/2)", styles["td_ok"]))

        rows_data.append(row_cells)

    col_widths = [1.45 * inch, 2.65 * inch, 2.65 * inch, 0.98 * inch, 0.97 * inch, 1.50 * inch]
    tbl = Table(rows_data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle(style_cmds))
    return tbl


def _build_employee_section(emp: EmployeeReport, styles: dict[str, ParagraphStyle]) -> list:
    """Construye la sección completa de un empleado separando Lunes–Viernes y Sábados."""
    elements: list = []

    weekday_days = [d for d in emp.days if d.weekday_index < 5]
    saturday_days = [d for d in emp.days if d.weekday_index == 5]

    safe_emp_id = xml_escape(emp.employee_id)
    safe_emp_name = xml_escape(emp.employee_name)
    emp_summary_html = (
        f"<b>Empleado:</b> [{safe_emp_id}] {safe_emp_name} &nbsp;|&nbsp; "
        f"<b>Días lab. evaluados:</b> {emp.days_analyzed} (Domingos: Descanso) &nbsp;|&nbsp; "
        f"<b>Faltas injust.:</b> {emp.unjustified_absences_count} ({emp.total_absence_hours:g}h) &nbsp;|&nbsp; "
        f"<b>Notas:</b> {emp.justified_notes_count} &nbsp;|&nbsp; "
        f"<b>Retardo:</b> {emp.total_tardiness_minutes} min &nbsp;|&nbsp; "
        f"<b>Descuento Total:</b> ${emp.total_discount:,.2f}"
    )
    emp_banner = Table([[Paragraph(emp_summary_html, styles["emp_banner"])]], colWidths=[10.2 * inch])
    emp_banner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), COLOR_SLATE_HEADER),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("BOX", (0, 0), (-1, -1), 0.75, COLOR_NAVY),
            ]
        )
    )
    elements.append(emp_banner)

    if weekday_days:
        elements.append(_build_weekday_table(weekday_days, styles))

    if saturday_days:
        elements.append(Spacer(1, 3))
        elements.append(_build_saturday_table(saturday_days, styles))

    # Pie consolidado del empleado
    abs_footer = f" + {emp.total_absence_hours:g}h faltas" if emp.total_absence_hours > 0 else ""
    totals_bar = Table(
        [
            [
                Paragraph(f"<b>TOTAL ACUMULADO PERIODO — {safe_emp_name}</b>", styles["td_footer"]),
                Paragraph(f"<b>{emp.total_tardiness_minutes} min{abs_footer}</b>", styles["td_footer"]),
                Paragraph(f"<b>${emp.total_discount:,.2f}</b>", styles["td_footer"]),
                Paragraph(
                    f"<b>{emp.unjustified_absences_count} faltas / {emp.justified_notes_count} notas</b>",
                    styles["td_footer"],
                ),
            ]
        ],
        colWidths=[6.75 * inch, 0.98 * inch, 0.97 * inch, 1.50 * inch],
    )
    totals_bar.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#E2E8F0")),
                ("BOX", (0, 0), (-1, -1), 0.8, COLOR_NAVY),
                ("INNERGRID", (0, 0), (-1, -1), 0.45, COLOR_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(totals_bar)
    elements.append(Spacer(1, 12))

    return [KeepTogether(elements)] if len(emp.days) <= 10 else elements


def generate_protected_pdf(state: ReportStateResponse) -> bytes:
    """Construye el PDF completo del reporte de asistencia y aplica cifrado AES-256."""
    if not state.has_data or state.summary is None:
        raise ValueError("No existen datos cargados para generar el reporte PDF.")

    integrity_hash = compute_report_integrity_hash(state)
    generated_at = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    raw_pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        raw_pdf_buffer,
        pagesize=landscape(letter),
        leftMargin=0.4 * inch,
        rightMargin=0.4 * inch,
        topMargin=0.42 * inch,
        bottomMargin=0.52 * inch,
        title="Reporte Oficial de Asistencia, Faltas y Retardos",
        author="Sistema Analizador de Asistencia",
        subject=f"Integridad SHA-256: {integrity_hash}",
    )

    base_styles = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "TitleCustom",
            parent=base_styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=13.5,
            leading=16.5,
            textColor=colors.white,
        ),
        "subtitle": ParagraphStyle(
            "SubtitleCustom",
            parent=base_styles["Normal"],
            fontName="Helvetica",
            fontSize=8.2,
            leading=10.8,
            textColor=colors.HexColor("#E2E8F0"),
        ),
        "meta": ParagraphStyle(
            "MetaCustom",
            parent=base_styles["Normal"],
            fontName="Helvetica",
            fontSize=7.8,
            leading=10.2,
            textColor=COLOR_NAVY,
        ),
        "emp_banner": ParagraphStyle(
            "EmpBanner",
            parent=base_styles["Normal"],
            fontName="Helvetica",
            fontSize=7.9,
            leading=10.8,
            textColor=colors.white,
        ),
        "th": ParagraphStyle(
            "TableHeader",
            parent=base_styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.0,
            leading=8.5,
            alignment=1,
            textColor=colors.white,
        ),
        "td": ParagraphStyle(
            "TableCell",
            parent=base_styles["Normal"],
            fontName="Helvetica",
            fontSize=7.2,
            leading=8.8,
            alignment=1,
            textColor=COLOR_NAVY,
            charSpace=0.15,
        ),
        "td_holiday": ParagraphStyle(
            "TableCellHoliday",
            parent=base_styles["Normal"],
            fontName="Helvetica",
            fontSize=7.2,
            leading=8.8,
            alignment=1,
            textColor=COLOR_HOLIDAY_TEXT,
        ),
        "td_ok": ParagraphStyle(
            "TableCellOk",
            parent=base_styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.0,
            leading=8.6,
            alignment=1,
            textColor=colors.HexColor("#166534"),
        ),
        "td_missing": ParagraphStyle(
            "TableCellMissing",
            parent=base_styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.0,
            leading=8.5,
            alignment=1,
            textColor=COLOR_MISSING_TEXT,
        ),
        "td_note": ParagraphStyle(
            "TableCellNote",
            parent=base_styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.0,
            leading=8.5,
            alignment=1,
            textColor=COLOR_NOTE_TEXT,
        ),
        "td_late": ParagraphStyle(
            "TableCellLate",
            parent=base_styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.0,
            leading=8.6,
            alignment=1,
            textColor=COLOR_LATE_TEXT,
        ),
        "td_footer": ParagraphStyle(
            "TableFooter",
            parent=base_styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.4,
            leading=9.2,
            alignment=1,
            textColor=COLOR_NAVY,
        ),
        "section_h2": ParagraphStyle(
            "SectionH2",
            parent=base_styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=10.2,
            leading=13,
            textColor=COLOR_NAVY,
            spaceBefore=6,
            spaceAfter=4,
        ),
        "td_left": ParagraphStyle(
            "TableCellLeft",
            parent=base_styles["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=9.6,
            alignment=0,
            textColor=COLOR_NAVY,
        ),
    }

    story: list = []

    mode_label = (
        "Hora Límite (L-V 08:15, 10:00, 15:00 | Sáb 09:00)"
        if state.config.tardiness_mode == "fixed_time"
        else "Duración (Entrada 08:15/09:00, Alm. 20m, Com. 60m)"
    )
    header_card = Table(
        [
            [
                Paragraph(
                    "REPORTE OFICIAL DE ASISTENCIA, FALTAS, FESTIVOS Y RETARDOS<br/>"
                    "<font size='7.8' color='#CBD5E1'>Lunes–Viernes: 08:15 a 18:00+ (6 reg.) · Sábados: 09:00 a 13:00+ (2 reg.) · Domingos: Descanso</font>",
                    styles["title"],
                ),
                Paragraph(
                    f"<b>Periodo evaluado:</b> {xml_escape(state.summary.period_start or '')} al {xml_escape(state.summary.period_end or '')}<br/>"
                    f"<b>Archivo fuente:</b> {xml_escape(state.summary.source_filename)}<br/>"
                    f"<b>Festivos en periodo:</b> {state.summary.holidays_count}",
                    styles["subtitle"],
                ),
            ]
        ],
        colWidths=[6.5 * inch, 3.7 * inch],
    )
    header_card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), COLOR_NAVY),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(header_card)
    story.append(Spacer(1, 5))

    kpi_table = Table(
        [
            [
                Paragraph(
                    f"<b>Modo Retardo:</b> {mode_label}<br/>"
                    f"<b>Margen Checada Doble:</b> {state.config.duplicate_margin_minutes} min (1ra ref.)<br/>"
                    f"<b>Horas por Falta:</b> L-V {state.config.absence_hours_weekday:g}h | Sáb {state.config.absence_hours_saturday:g}h",
                    styles["meta"],
                ),
                Paragraph(
                    f"<b>Salario por Hora:</b> ${state.config.hourly_wage:,.2f}<br/>"
                    f"<b>Retardo Acumulado:</b> {state.summary.total_tardiness_minutes} min (${state.summary.total_tardiness_discount:,.2f})<br/>"
                    f"<b>Faltas Injustificadas:</b> {state.summary.unjustified_absences_count} (${state.summary.total_absence_discount:,.2f})",
                    styles["meta"],
                ),
                Paragraph(
                    f"<b>DESCUENTO TOTAL PERIODO:</b> <font color='#922B21'><b>${state.summary.total_discount:,.2f}</b></font><br/>"
                    f"<b>Incidencias Pendientes:</b> {state.summary.pending_missing_slots_count}<br/>"
                    f"<b>Notas / Justificaciones:</b> {state.summary.justified_slots_count}",
                    styles["meta"],
                ),
            ]
        ],
        colWidths=[3.8 * inch, 3.3 * inch, 3.1 * inch],
    )
    kpi_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1F5F9")),
                ("BOX", (0, 0), (-1, -1), 0.6, COLOR_BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, COLOR_BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(kpi_table)
    story.append(Spacer(1, 5))

    legend_table = Table(
        [
            [
                Paragraph("<b>ROJO:</b> Checada faltante o FALTA completa", styles["td_missing"]),
                Paragraph("<b>ÁMBAR:</b> Columna / Celda / Falta con Nota", styles["td_note"]),
                Paragraph("<b>NARANJA:</b> Checada con retardo", styles["td_late"]),
                Paragraph("<b>AZUL:</b> Día Festivo Oficial", styles["td_holiday"]),
            ]
        ],
        colWidths=[2.7 * inch, 2.8 * inch, 2.35 * inch, 2.35 * inch],
    )
    legend_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), COLOR_MISSING_BG),
                ("BACKGROUND", (1, 0), (1, 0), COLOR_NOTE_CELL_BG),
                ("BACKGROUND", (2, 0), (2, 0), COLOR_LATE_BG),
                ("BACKGROUND", (3, 0), (3, 0), COLOR_HOLIDAY_BG),
                ("BOX", (0, 0), (-1, -1), 0.5, COLOR_BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, COLOR_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(legend_table)
    story.append(Spacer(1, 9))

    for emp in state.employees:
        story.extend(_build_employee_section(emp, styles))

    story.append(Spacer(1, 6))
    story.append(
        Paragraph("ANEXO DE OBSERVACIONES, JUSTIFICACIONES DE FALTAS Y PENALIZACIONES", styles["section_h2"])
    )

    if state.notes:
        notes_rows = [
            [
                Paragraph("<b>Nota</b>", styles["th"]),
                Paragraph("<b>Empleado</b>", styles["th"]),
                Paragraph("<b>Día / Fecha</b>", styles["th"]),
                Paragraph("<b>Incidencia / Bloque</b>", styles["th"]),
                Paragraph("<b>Penalización</b>", styles["th"]),
                Paragraph("<b>Justificación / Observación Registrada</b>", styles["th"]),
            ]
        ]
        for note in state.notes:
            pen_label = (
                f"+{note.penalty_minutes} min" if note.penalty_minutes > 0 else "0 min (Sin descuento)"
            )
            notes_rows.append(
                [
                    Paragraph(f"<b>Nota #{note.note_number}</b>", styles["td_note"]),
                    Paragraph(f"[{xml_escape(note.employee_id)}] {xml_escape(note.employee_name)}", styles["td"]),
                    Paragraph(xml_escape(note.display_date), styles["td"]),
                    Paragraph(xml_escape(note.slot_label), styles["td"]),
                    Paragraph(f"<b>{pen_label}</b>", styles["td_note"]),
                    Paragraph(xml_escape(note.justification), styles["td_left"]),
                ]
            )

        notes_table = Table(
            notes_rows,
            colWidths=[0.75 * inch, 1.45 * inch, 1.05 * inch, 2.15 * inch, 1.15 * inch, 3.65 * inch],
            repeatRows=1,
        )
        notes_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), COLOR_NOTE_COL_HEADER),
                    ("BACKGROUND", (0, 1), (-1, -1), COLOR_NOTE_COL_TINT),
                    ("BACKGROUND", (0, 1), (0, -1), COLOR_NOTE_CELL_BG),
                    ("GRID", (0, 0), (-1, -1), 0.5, COLOR_NOTE_BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(notes_table)
    else:
        empty_notes = Table(
            [
                [
                    Paragraph(
                        "No se registraron observaciones ni justificaciones manuales en este reporte.",
                        styles["meta"],
                    )
                ]
            ],
            colWidths=[10.2 * inch],
        )
        empty_notes.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), COLOR_ZEBRA),
                    ("BOX", (0, 0), (-1, -1), 0.5, COLOR_BORDER),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(empty_notes)

    def _on_page(canvas: Canvas, doc_obj: SimpleDocTemplate) -> None:
        _draw_tamper_evident_canvas(canvas, doc_obj, integrity_hash, generated_at)

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)

    raw_pdf_buffer.seek(0)
    reader = PdfReader(raw_pdf_buffer)
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.add_metadata(
        {
            "/Title": "Reporte Oficial de Asistencia, Faltas y Retardos",
            "/Producer": "AssistanceGenerator Security Engine",
            "/IntegritySHA256": integrity_hash,
            "/GeneratedAt": generated_at,
        }
    )

    owner_secret = secrets.token_urlsafe(32)
    allowed_permissions = (
        UserAccessPermissions.PRINT | UserAccessPermissions.PRINT_TO_REPRESENTATION
    )
    writer.encrypt(
        user_password="",
        owner_password=owner_secret,
        permissions_flag=allowed_permissions,
        algorithm="AES-256",
    )

    secured_buffer = io.BytesIO()
    writer.write(secured_buffer)
    return secured_buffer.getvalue()
