"""
Módulo 2: Motor de Reglas de Horario, Sábados, Descansos, Festivos, Faltas y Descuentos.

Responsabilidades:
- Generar el calendario completo del periodo [period_start, period_end] para todos los empleados.
- Diferenciar reglas según el día calendario:
    * Lunes a Viernes: 6 registros (08:15, 09:40, 10:00, 14:00, 15:00, 18:00+).
    * Sábado: 2 registros (Entrada 09:00 y Salida 13:00+); bloques de alimentos marcados N/A.
    * Domingo: Día de descanso oficial (sin falta ni descuento).
    * Día Festivo configurado: Exento de asistencia (sin falta ni descuento).
- Detectar Faltas Completas (0 checadas en Lunes a Sábado no festivo):
    * Si no está justificada, aplica descuento salarial según las horas configuradas
      (absence_hours_weekday o absence_hours_saturday).
    * Si se justifica en la interfaz ('full_absence'), retira el descuento por falta y aplica
      únicamente los minutos de penalización indicados (>= 0).
- Deduplicar checadas repetidas conservando siempre la primera hora registrada.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from math import inf

from app.config import (
    ALL_MISSING_DAY_SLOT_ID,
    ALLOWED_BREAKFAST_MINUTES,
    ALLOWED_LUNCH_MINUTES,
    FULL_ABSENCE_SLOT_ID,
    MAX_PERIOD_DAYS,
    OFFICIAL_SLOTS,
    SATURDAY_SLOTS,
    SATURDAY_SLOTS_BY_ID,
    SLOTS_BY_ID,
    WEEKDAY_SHORT_ES,
    ScheduleSlotDef,
)
from app.models.schemas import (
    AttendanceConfig,
    DayEvaluation,
    EmployeeReport,
    EmployeeWithMissing,
    JustificationInput,
    JustificationRecord,
    MissingSlotOption,
    ReportStateResponse,
    ReportSummary,
    SlotEvaluation,
)
from app.modules.excel_parser import ParsedAttendanceFile


def _dt_to_minutes(dt: datetime) -> int:
    """Convierte la hora de un datetime a minutos transcurridos desde las 00:00."""
    return dt.hour * 60 + dt.minute


def deduplicate_daily_punches(
    sorted_punches: list[datetime],
    margin_minutes: int,
    max_slots: int = 6,
) -> tuple[list[datetime], list[str]]:
    """
    Filtra checadas repetidas por error humano y garantiza como máximo `max_slots` registros
    válidos (6 de lunes a viernes, 2 en sábado), conservando siempre la primera checada.
    """
    if not sorted_punches:
        return [], []

    ordered = sorted(sorted_punches)
    kept: list[datetime] = [ordered[0]]
    discarded: list[str] = []
    margin_seconds = margin_minutes * 60

    # Paso 1: Deduplicación por margen configurable entre checadas consecutivas
    reference_dt = ordered[0]
    for candidate in ordered[1:]:
        diff_seconds = (candidate - reference_dt).total_seconds()
        if diff_seconds <= margin_seconds:
            discarded.append(candidate.strftime("%H:%M"))
        else:
            kept.append(candidate)
            reference_dt = candidate

    # Paso 2: Si aún hay más checadas que bloques esperados en ese día (ej. >6 L-V o >2 Sáb),
    # colapsar los pares más próximos conservando SIEMPRE el primer registro.
    while len(kept) > max_slots:
        min_gap = inf
        dup_second_idx = 1
        for i in range(len(kept) - 1):
            gap = (kept[i + 1] - kept[i]).total_seconds()
            if gap < min_gap:
                min_gap = gap
                dup_second_idx = i + 1
        removed = kept.pop(dup_second_idx)
        discarded.append(removed.strftime("%H:%M"))

    return kept, discarded


def assign_punches_to_slots(
    valid_punches: list[datetime],
    active_slots: tuple[ScheduleSlotDef, ...] = OFFICIAL_SLOTS,
) -> dict[str, datetime | None]:
    """
    Asigna de 0 a N checadas ordenadas cronológicamente a los bloques activos del día
    (6 bloques L-V o 2 bloques Sábado) minimizando la distancia temporal sin romper el orden.
    """
    result: dict[str, datetime | None] = {slot.slot_id: None for slot in active_slots}
    k = len(valid_punches)
    n = len(active_slots)

    if k == 0 or n == 0:
        return result

    if k >= n:
        for idx, slot in enumerate(active_slots):
            result[slot.slot_id] = valid_punches[idx]
        return result

    punch_mins = [_dt_to_minutes(dt) for dt in valid_punches]
    target_mins = [slot.target_minutes for slot in active_slots]

    dp = [[inf] * n for _ in range(k)]
    parent = [[-1] * n for _ in range(k)]

    for j in range(n - k + 1):
        dp[0][j] = abs(punch_mins[0] - target_mins[j])

    for i in range(1, k):
        max_j = n - (k - i)
        for j in range(i, max_j + 1):
            best_prev_cost = inf
            best_prev_idx = -1
            for prev_j in range(i - 1, j):
                if dp[i - 1][prev_j] < best_prev_cost:
                    best_prev_cost = dp[i - 1][prev_j]
                    best_prev_idx = prev_j
            dp[i][j] = best_prev_cost + abs(punch_mins[i] - target_mins[j])
            parent[i][j] = best_prev_idx

    best_last_j = k - 1
    best_total = inf
    for j in range(k - 1, n):
        if dp[k - 1][j] < best_total:
            best_total = dp[k - 1][j]
            best_last_j = j

    assigned_indices = [0] * k
    curr_j = best_last_j
    for i in range(k - 1, -1, -1):
        assigned_indices[i] = curr_j
        curr_j = parent[i][curr_j]

    for punch_idx, slot_idx in enumerate(assigned_indices):
        slot_def = active_slots[slot_idx]
        result[slot_def.slot_id] = valid_punches[punch_idx]

    return result


def assign_punches_to_official_slots(valid_punches: list[datetime]) -> dict[str, datetime | None]:
    """Alias compatible para asignación de los 6 bloques de lunes a viernes."""
    return assign_punches_to_slots(valid_punches, OFFICIAL_SLOTS)


def _calculate_weekday_tardiness(
    assigned: dict[str, datetime | None],
    config: AttendanceConfig,
) -> dict[str, int]:
    """Calcula minutos de retardo de Lunes a Viernes según el modo seleccionado."""
    tardiness: dict[str, int] = {slot.slot_id: 0 for slot in OFFICIAL_SLOTS}

    entry_dt = assigned.get("entry")
    if entry_dt is not None:
        entry_min = _dt_to_minutes(entry_dt)
        entry_target = SLOTS_BY_ID["entry"].target_minutes
        if entry_min > entry_target:
            tardiness["entry"] = entry_min - entry_target

    b_out_dt = assigned.get("breakfast_out")
    b_in_dt = assigned.get("breakfast_in")
    if b_in_dt is not None:
        b_in_min = _dt_to_minutes(b_in_dt)
        if config.tardiness_mode == "duration" and b_out_dt is not None:
            b_duration = max(0, b_in_min - _dt_to_minutes(b_out_dt))
            tardiness["breakfast_in"] = max(0, b_duration - ALLOWED_BREAKFAST_MINUTES)
        else:
            b_target = SLOTS_BY_ID["breakfast_in"].target_minutes
            tardiness["breakfast_in"] = max(0, b_in_min - b_target)

    l_out_dt = assigned.get("lunch_out")
    l_in_dt = assigned.get("lunch_in")
    if l_in_dt is not None:
        l_in_min = _dt_to_minutes(l_in_dt)
        if config.tardiness_mode == "duration" and l_out_dt is not None:
            l_duration = max(0, l_in_min - _dt_to_minutes(l_out_dt))
            tardiness["lunch_in"] = max(0, l_duration - ALLOWED_LUNCH_MINUTES)
        else:
            l_target = SLOTS_BY_ID["lunch_in"].target_minutes
            tardiness["lunch_in"] = max(0, l_in_min - l_target)

    return tardiness


def _calculate_saturday_tardiness(assigned: dict[str, datetime | None]) -> dict[str, int]:
    """Calcula minutos de retardo en Sábado (Entrada oficial 09:00 hrs)."""
    tardiness: dict[str, int] = {"entry": 0, "exit": 0}
    entry_dt = assigned.get("entry")
    if entry_dt is not None:
        entry_min = _dt_to_minutes(entry_dt)
        sat_target = SATURDAY_SLOTS_BY_ID["entry"].target_minutes  # 540 (09:00)
        if entry_min > sat_target:
            tardiness["entry"] = entry_min - sat_target
    return tardiness


def calculate_monetary_discount(total_minutes: int, hourly_wage: float) -> float:
    """Calcula el descuento económico proporcional a los minutos de retardo acumulados."""
    if total_minutes <= 0 or hourly_wage <= 0:
        return 0.0
    return round((total_minutes / 60.0) * hourly_wage, 2)


def calculate_absence_discount(absence_hours: float, hourly_wage: float) -> float:
    """Calcula el descuento económico por horas de falta injustificada."""
    if absence_hours <= 0 or hourly_wage <= 0:
        return 0.0
    return round(absence_hours * hourly_wage, 2)


def resolve_period_dates(
    parsed_file: ParsedAttendanceFile,
    config: AttendanceConfig,
) -> tuple[date, date, list[date]]:
    """Determina la lista continua de fechas del periodo a evaluar."""
    start_dt = (
        date.fromisoformat(config.period_start)
        if config.period_start
        else parsed_file.min_date
    )
    end_dt = (
        date.fromisoformat(config.period_end)
        if config.period_end
        else parsed_file.max_date
    )

    if end_dt < start_dt:
        raise ValueError(
            "La fecha final del periodo no puede ser anterior a la fecha inicial."
        )

    span_days = (end_dt - start_dt).days + 1
    if span_days > MAX_PERIOD_DAYS:
        raise ValueError(
            f"El rango del periodo ({span_days} días) excede el máximo permitido de {MAX_PERIOD_DAYS} días."
        )

    dates = [start_dt + timedelta(days=offset) for offset in range(span_days)]
    return start_dt, end_dt, dates


def evaluate_attendance_report(
    parsed_file: ParsedAttendanceFile,
    config: AttendanceConfig,
    justifications: dict[tuple[str, str, str], JustificationInput],
) -> ReportStateResponse:
    """
    Ejecuta el análisis completo sobre todos los empleados y todos los días calendario del periodo,
    aplicando reglas de Lunes-Viernes, Sábados (09:00-13:00), Domingos de descanso, Festivos y Faltas.
    """
    period_start_dt, period_end_dt, calendar_dates = resolve_period_dates(parsed_file, config)
    holidays_map: dict[str, str] = {h.date: h.label for h in config.holidays}

    employee_reports: list[EmployeeReport] = []
    employees_with_missing: list[EmployeeWithMissing] = []
    applied_notes: list[JustificationRecord] = []

    note_counter = 0
    global_pending_missing = 0
    global_unjustified_absences = 0
    global_justified_slots = 0
    global_tardiness_minutes = 0
    global_tardiness_discount = 0.0
    global_absence_discount = 0.0

    for emp_raw in parsed_file.employees:
        day_evaluations: list[DayEvaluation] = []
        emp_missing_options: list[MissingSlotOption] = []

        emp_working_days_analyzed = 0
        emp_incomplete_days = 0
        emp_unjustified_absences = 0
        emp_justified_absences = 0
        emp_absence_hours = 0.0
        emp_justified_notes = 0
        emp_schedule_tardiness = 0
        emp_penalty_tardiness = 0

        for day_date in calendar_dates:
            date_iso = day_date.isoformat()
            display_date = day_date.strftime("%d/%m/%Y")
            w_idx = day_date.weekday()  # 0=Lun .. 5=Sáb, 6=Dom
            w_short = WEEKDAY_SHORT_ES[w_idx]
            day_with_name = f"{w_short} {display_date}"
            raw_dts = emp_raw.punches_by_date.get(day_date, [])

            # ------------------------------------------------------------------
            # CASO 1: Día Festivo Oficial configurado en el periodo
            # ------------------------------------------------------------------
            if date_iso in holidays_map:
                h_label = holidays_map[date_iso]
                slot_evals = [
                    SlotEvaluation(
                        slot_id=s.slot_id,
                        slot_label=s.label,
                        short_label=s.short_label,
                        target_time=s.target_time,
                        recorded_time=raw_dts[idx].strftime("%H:%M") if idx < len(raw_dts) else None,
                        status="holiday",
                    )
                    for idx, s in enumerate(OFFICIAL_SLOTS)
                ]
                day_evaluations.append(
                    DayEvaluation(
                        date=date_iso,
                        display_date=display_date,
                        weekday_index=w_idx,
                        weekday_name=w_short,
                        day_type="holiday",
                        holiday_label=h_label,
                        expected_punches_count=0,
                        raw_punches_count=len(raw_dts),
                        valid_punches_count=len(raw_dts),
                        discarded_duplicates=[],
                        has_missing_punches=False,
                        unjustified_missing_count=0,
                        justified_count=0,
                        has_notes=False,
                        row_status="holiday",
                        slots=slot_evals,
                        schedule_tardiness_minutes=0,
                        penalty_tardiness_minutes=0,
                        total_tardiness_minutes=0,
                        tardiness_discount=0.0,
                        absence_discount=0.0,
                        daily_discount=0.0,
                    )
                )
                continue

            # ------------------------------------------------------------------
            # CASO 2: Domingo (Día de Descanso Oficial)
            # ------------------------------------------------------------------
            if w_idx == 6:
                slot_evals = [
                    SlotEvaluation(
                        slot_id=s.slot_id,
                        slot_label=s.label,
                        short_label=s.short_label,
                        target_time=s.target_time,
                        recorded_time=raw_dts[idx].strftime("%H:%M") if idx < len(raw_dts) else None,
                        status="rest",
                    )
                    for idx, s in enumerate(OFFICIAL_SLOTS)
                ]
                day_evaluations.append(
                    DayEvaluation(
                        date=date_iso,
                        display_date=display_date,
                        weekday_index=w_idx,
                        weekday_name=w_short,
                        day_type="sunday",
                        expected_punches_count=0,
                        raw_punches_count=len(raw_dts),
                        valid_punches_count=len(raw_dts),
                        discarded_duplicates=[],
                        has_missing_punches=False,
                        unjustified_missing_count=0,
                        justified_count=0,
                        has_notes=False,
                        row_status="rest_day",
                        slots=slot_evals,
                        schedule_tardiness_minutes=0,
                        penalty_tardiness_minutes=0,
                        total_tardiness_minutes=0,
                        tardiness_discount=0.0,
                        absence_discount=0.0,
                        daily_discount=0.0,
                    )
                )
                continue

            # Es día laboral (Lunes a Sábado)
            is_saturday = w_idx == 5
            day_type = "saturday" if is_saturday else "weekday"
            expected_count = 2 if is_saturday else 6
            emp_working_days_analyzed += 1

            # ------------------------------------------------------------------
            # CASO 3: FALTA COMPLETA (0 checadas en día laboral Lunes a Sábado)
            # ------------------------------------------------------------------
            if len(raw_dts) == 0:
                sched_abs_hours = (
                    config.absence_hours_saturday if is_saturday else config.absence_hours_weekday
                )
                abs_desc = (
                    f"FALTA COMPLETA (Sábado 09:00–13:00 · {sched_abs_hours:g}h)"
                    if is_saturday
                    else f"FALTA COMPLETA (Jornada L-V · {sched_abs_hours:g}h)"
                )
                abs_key = (emp_raw.employee_id, date_iso, FULL_ABSENCE_SLOT_ID)
                just_input = justifications.get(abs_key)

                slot_evals = []
                if just_input is not None:
                    note_counter += 1
                    emp_justified_absences += 1
                    emp_justified_notes += 1
                    global_justified_slots += 1
                    pen_mins = just_input.penalty_minutes
                    emp_penalty_tardiness += pen_mins
                    day_tardiness_disc = calculate_monetary_discount(pen_mins, config.hourly_wage)

                    applied_notes.append(
                        JustificationRecord(
                            note_number=note_counter,
                            employee_id=emp_raw.employee_id,
                            employee_name=emp_raw.employee_name,
                            date=date_iso,
                            display_date=day_with_name,
                            slot_id=FULL_ABSENCE_SLOT_ID,
                            slot_label=abs_desc,
                            justification=just_input.justification,
                            penalty_minutes=pen_mins,
                        )
                    )

                    for s in OFFICIAL_SLOTS:
                        if is_saturday and s.slot_id not in SATURDAY_SLOTS_BY_ID:
                            slot_evals.append(
                                SlotEvaluation(
                                    slot_id=s.slot_id,
                                    slot_label=s.label,
                                    short_label=s.short_label,
                                    target_time=s.target_time,
                                    status="not_applicable",
                                )
                            )
                        else:
                            active_def = SATURDAY_SLOTS_BY_ID[s.slot_id] if is_saturday else s
                            slot_evals.append(
                                SlotEvaluation(
                                    slot_id=s.slot_id,
                                    slot_label=active_def.label,
                                    short_label=active_def.short_label,
                                    target_time=active_def.target_time,
                                    status="justified_absence",
                                    penalty_minutes=pen_mins if s.slot_id == "entry" else 0,
                                    note_number=note_counter,
                                    justification=just_input.justification,
                                )
                            )

                    emp_missing_options.append(
                        MissingSlotOption(
                            date=date_iso,
                            display_date=day_with_name,
                            slot_id=FULL_ABSENCE_SLOT_ID,
                            slot_label=abs_desc,
                            dropdown_label=f"{day_with_name} — {abs_desc} [Justificada: Nota #{note_counter}]",
                            is_full_absence=True,
                            is_justified=True,
                            current_justification=just_input.justification,
                            current_penalty_minutes=pen_mins,
                            note_number=note_counter,
                        )
                    )

                    day_evaluations.append(
                        DayEvaluation(
                            date=date_iso,
                            display_date=display_date,
                            weekday_index=w_idx,
                            weekday_name=w_short,
                            day_type=day_type,
                            expected_punches_count=expected_count,
                            raw_punches_count=0,
                            valid_punches_count=0,
                            discarded_duplicates=[],
                            has_missing_punches=True,
                            is_unjustified_absence=False,
                            absence_hours_applied=0.0,
                            unjustified_missing_count=0,
                            justified_count=1,
                            has_notes=True,
                            row_status="justified_absence",
                            slots=slot_evals,
                            schedule_tardiness_minutes=0,
                            penalty_tardiness_minutes=pen_mins,
                            total_tardiness_minutes=pen_mins,
                            tardiness_discount=day_tardiness_disc,
                            absence_discount=0.0,
                            daily_discount=day_tardiness_disc,
                        )
                    )
                else:
                    # Falta NO justificada -> se marca como FALTA en rojo y descuenta las horas configuradas
                    emp_incomplete_days += 1
                    emp_unjustified_absences += 1
                    global_unjustified_absences += 1
                    global_pending_missing += 1
                    emp_absence_hours += sched_abs_hours
                    day_abs_disc = calculate_absence_discount(sched_abs_hours, config.hourly_wage)

                    for s in OFFICIAL_SLOTS:
                        if is_saturday and s.slot_id not in SATURDAY_SLOTS_BY_ID:
                            slot_evals.append(
                                SlotEvaluation(
                                    slot_id=s.slot_id,
                                    slot_label=s.label,
                                    short_label=s.short_label,
                                    target_time=s.target_time,
                                    status="not_applicable",
                                )
                            )
                        else:
                            active_def = SATURDAY_SLOTS_BY_ID[s.slot_id] if is_saturday else s
                            slot_evals.append(
                                SlotEvaluation(
                                    slot_id=s.slot_id,
                                    slot_label=active_def.label,
                                    short_label=active_def.short_label,
                                    target_time=active_def.target_time,
                                    status="absence",
                                )
                            )

                    emp_missing_options.append(
                        MissingSlotOption(
                            date=date_iso,
                            display_date=day_with_name,
                            slot_id=FULL_ABSENCE_SLOT_ID,
                            slot_label=abs_desc,
                            dropdown_label=f"{day_with_name} — {abs_desc}",
                            is_full_absence=True,
                            is_justified=False,
                        )
                    )

                    day_evaluations.append(
                        DayEvaluation(
                            date=date_iso,
                            display_date=display_date,
                            weekday_index=w_idx,
                            weekday_name=w_short,
                            day_type=day_type,
                            expected_punches_count=expected_count,
                            raw_punches_count=0,
                            valid_punches_count=0,
                            discarded_duplicates=[],
                            has_missing_punches=True,
                            is_unjustified_absence=True,
                            absence_hours_applied=sched_abs_hours,
                            unjustified_missing_count=1,
                            justified_count=0,
                            has_notes=False,
                            row_status="absence",
                            slots=slot_evals,
                            schedule_tardiness_minutes=0,
                            penalty_tardiness_minutes=0,
                            total_tardiness_minutes=0,
                            tardiness_discount=0.0,
                            absence_discount=day_abs_disc,
                            daily_discount=day_abs_disc,
                        )
                    )
                continue

            # ------------------------------------------------------------------
            # CASO 4: Día laboral (Lunes-Viernes o Sábado) con al menos 1 checada
            # ------------------------------------------------------------------
            active_slots_tuple = SATURDAY_SLOTS if is_saturday else OFFICIAL_SLOTS
            valid_dts, discarded_dups = deduplicate_daily_punches(
                raw_dts,
                margin_minutes=config.duplicate_margin_minutes,
                max_slots=expected_count,
            )
            assigned = assign_punches_to_slots(valid_dts, active_slots_tuple)
            slot_tardiness_map = (
                _calculate_saturday_tardiness(assigned)
                if is_saturday
                else _calculate_weekday_tardiness(assigned, config)
            )

            slot_evals = []
            day_missing_opts: list[MissingSlotOption] = []
            unjustified_missing_count = 0
            justified_count = 0
            day_schedule_late = 0
            day_penalty_late = 0

            for col_slot_def in OFFICIAL_SLOTS:
                # En Sábado, las 4 columnas de alimentos no aplican
                if is_saturday and col_slot_def.slot_id not in SATURDAY_SLOTS_BY_ID:
                    slot_evals.append(
                        SlotEvaluation(
                            slot_id=col_slot_def.slot_id,
                            slot_label=col_slot_def.label,
                            short_label=col_slot_def.short_label,
                            target_time=col_slot_def.target_time,
                            recorded_time=None,
                            status="not_applicable",
                        )
                    )
                    continue

                effective_slot_def = (
                    SATURDAY_SLOTS_BY_ID[col_slot_def.slot_id] if is_saturday else col_slot_def
                )
                rec_dt = assigned.get(effective_slot_def.slot_id)
                just_key = (emp_raw.employee_id, date_iso, effective_slot_def.slot_id)

                if rec_dt is not None:
                    rec_str = rec_dt.strftime("%H:%M")
                    late_mins = slot_tardiness_map.get(effective_slot_def.slot_id, 0)
                    day_schedule_late += late_mins

                    if late_mins > 0:
                        status = "late"
                    elif (
                        effective_slot_def.slot_id == "exit"
                        and _dt_to_minutes(rec_dt) < effective_slot_def.target_minutes
                    ):
                        status = "early_exit"
                    else:
                        status = "ok"

                    slot_evals.append(
                        SlotEvaluation(
                            slot_id=effective_slot_def.slot_id,
                            slot_label=effective_slot_def.label,
                            short_label=effective_slot_def.short_label,
                            target_time=effective_slot_def.target_time,
                            recorded_time=rec_str,
                            status=status,
                            tardiness_minutes=late_mins,
                            penalty_minutes=0,
                        )
                    )
                else:
                    just_input = justifications.get(just_key)
                    if just_input is not None:
                        note_counter += 1
                        justified_count += 1
                        emp_justified_notes += 1
                        global_justified_slots += 1
                        day_penalty_late += just_input.penalty_minutes

                        applied_notes.append(
                            JustificationRecord(
                                note_number=note_counter,
                                employee_id=emp_raw.employee_id,
                                employee_name=emp_raw.employee_name,
                                date=date_iso,
                                display_date=day_with_name,
                                slot_id=effective_slot_def.slot_id,
                                slot_label=effective_slot_def.label,
                                justification=just_input.justification,
                                penalty_minutes=just_input.penalty_minutes,
                            )
                        )

                        slot_evals.append(
                            SlotEvaluation(
                                slot_id=effective_slot_def.slot_id,
                                slot_label=effective_slot_def.label,
                                short_label=effective_slot_def.short_label,
                                target_time=effective_slot_def.target_time,
                                recorded_time=None,
                                status="justified",
                                tardiness_minutes=0,
                                penalty_minutes=just_input.penalty_minutes,
                                note_number=note_counter,
                                justification=just_input.justification,
                            )
                        )

                        day_missing_opts.append(
                            MissingSlotOption(
                                date=date_iso,
                                display_date=day_with_name,
                                slot_id=effective_slot_def.slot_id,
                                slot_label=effective_slot_def.label,
                                dropdown_label=f"{day_with_name} — {effective_slot_def.label} [Justificado: Nota #{note_counter}]",
                                is_full_absence=False,
                                is_justified=True,
                                current_justification=just_input.justification,
                                current_penalty_minutes=just_input.penalty_minutes,
                                note_number=note_counter,
                            )
                        )
                    else:
                        unjustified_missing_count += 1
                        global_pending_missing += 1

                        slot_evals.append(
                            SlotEvaluation(
                                slot_id=effective_slot_def.slot_id,
                                slot_label=effective_slot_def.label,
                                short_label=effective_slot_def.short_label,
                                target_time=effective_slot_def.target_time,
                                recorded_time=None,
                                status="missing",
                                tardiness_minutes=0,
                                penalty_minutes=0,
                            )
                        )

                        day_missing_opts.append(
                            MissingSlotOption(
                                date=date_iso,
                                display_date=day_with_name,
                                slot_id=effective_slot_def.slot_id,
                                slot_label=effective_slot_def.label,
                                dropdown_label=f"{day_with_name} — {effective_slot_def.label}",
                                is_full_absence=False,
                                is_justified=False,
                            )
                        )

            # Si hay 2 o más registros faltantes el mismo día (ej. falla eléctrica con solo entrada y salida),
            # ofrecer también opción rápida para justificar todos los faltantes del día en un solo paso.
            if unjustified_missing_count >= 2:
                emp_missing_options.append(
                    MissingSlotOption(
                        date=date_iso,
                        display_date=day_with_name,
                        slot_id=ALL_MISSING_DAY_SLOT_ID,
                        slot_label=f"Todos los faltantes del día ({unjustified_missing_count} registros)",
                        dropdown_label=(
                            f"{day_with_name} — ★ JUSTIFICAR LOS {unjustified_missing_count} "
                            f"FALTANTES DEL DÍA (Ej. falla eléctrica / comisión)"
                        ),
                        is_full_absence=False,
                        is_justified=False,
                    )
                )
            emp_missing_options.extend(day_missing_opts)

            has_missing = (unjustified_missing_count + justified_count) > 0
            if unjustified_missing_count > 0:
                row_status = "incomplete"
                emp_incomplete_days += 1
            elif justified_count > 0:
                row_status = "justified_complete"
            else:
                row_status = "complete"

            day_total_late = day_schedule_late + day_penalty_late
            day_tardiness_disc = calculate_monetary_discount(day_total_late, config.hourly_wage)

            emp_schedule_tardiness += day_schedule_late
            emp_penalty_tardiness += day_penalty_late

            day_evaluations.append(
                DayEvaluation(
                    date=date_iso,
                    display_date=display_date,
                    weekday_index=w_idx,
                    weekday_name=w_short,
                    day_type=day_type,
                    expected_punches_count=expected_count,
                    raw_punches_count=len(raw_dts),
                    valid_punches_count=len(valid_dts),
                    discarded_duplicates=discarded_dups,
                    has_missing_punches=has_missing,
                    is_unjustified_absence=False,
                    absence_hours_applied=0.0,
                    unjustified_missing_count=unjustified_missing_count,
                    justified_count=justified_count,
                    has_notes=justified_count > 0,
                    row_status=row_status,
                    slots=slot_evals,
                    schedule_tardiness_minutes=day_schedule_late,
                    penalty_tardiness_minutes=day_penalty_late,
                    total_tardiness_minutes=day_total_late,
                    tardiness_discount=day_tardiness_disc,
                    absence_discount=0.0,
                    daily_discount=day_tardiness_disc,
                )
            )

        emp_total_tardiness = emp_schedule_tardiness + emp_penalty_tardiness
        emp_tardiness_discount = calculate_monetary_discount(emp_total_tardiness, config.hourly_wage)
        emp_absence_discount = calculate_absence_discount(emp_absence_hours, config.hourly_wage)
        emp_total_discount = round(emp_tardiness_discount + emp_absence_discount, 2)

        global_tardiness_minutes += emp_total_tardiness
        global_tardiness_discount = round(global_tardiness_discount + emp_tardiness_discount, 2)
        global_absence_discount = round(global_absence_discount + emp_absence_discount, 2)

        employee_reports.append(
            EmployeeReport(
                employee_id=emp_raw.employee_id,
                employee_name=emp_raw.employee_name,
                days_analyzed=emp_working_days_analyzed,
                incomplete_days_count=emp_incomplete_days,
                unjustified_absences_count=emp_unjustified_absences,
                justified_absences_count=emp_justified_absences,
                total_absence_hours=round(emp_absence_hours, 2),
                justified_notes_count=emp_justified_notes,
                schedule_tardiness_minutes=emp_schedule_tardiness,
                penalty_tardiness_minutes=emp_penalty_tardiness,
                total_tardiness_minutes=emp_total_tardiness,
                tardiness_discount=emp_tardiness_discount,
                absence_discount=emp_absence_discount,
                total_discount=emp_total_discount,
                days=day_evaluations,
            )
        )

        if emp_missing_options:
            real_opts = [opt for opt in emp_missing_options if opt.slot_id != ALL_MISSING_DAY_SLOT_ID]
            pending_cnt = sum(1 for opt in real_opts if not opt.is_justified)
            justified_cnt = sum(1 for opt in real_opts if opt.is_justified)
            employees_with_missing.append(
                EmployeeWithMissing(
                    employee_id=emp_raw.employee_id,
                    employee_name=emp_raw.employee_name,
                    pending_missing_count=pending_cnt,
                    justified_missing_count=justified_cnt,
                    total_missing_count=len(real_opts),
                    missing_slots=emp_missing_options,
                )
            )

    summary = ReportSummary(
        source_filename=parsed_file.source_filename,
        detected_min_date=parsed_file.min_date.isoformat(),
        detected_max_date=parsed_file.max_date.isoformat(),
        active_period_start_iso=period_start_dt.isoformat(),
        active_period_end_iso=period_end_dt.isoformat(),
        period_start=period_start_dt.strftime("%d/%m/%Y"),
        period_end=period_end_dt.strftime("%d/%m/%Y"),
        holidays_count=len(config.holidays),
        total_employees=len(employee_reports),
        employees_with_missing_count=len(employees_with_missing),
        pending_missing_slots_count=global_pending_missing,
        unjustified_absences_count=global_unjustified_absences,
        justified_slots_count=global_justified_slots,
        total_tardiness_minutes=global_tardiness_minutes,
        total_tardiness_discount=global_tardiness_discount,
        total_absence_discount=global_absence_discount,
        total_discount=round(global_tardiness_discount + global_absence_discount, 2),
    )

    return ReportStateResponse(
        has_data=True,
        config=config,
        summary=summary,
        employees_with_missing=employees_with_missing,
        employees=employee_reports,
        notes=applied_notes,
    )
