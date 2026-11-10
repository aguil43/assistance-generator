/**
 * Módulo 5B: Lógica interactiva del Frontend (app.js).
 * Soporta horarios Lunes–Viernes (6 checadas), Sábados (09:00–13:00, 2 checadas),
 * Domingos de descanso, gestión de Días Festivos, rango de periodo ajustable,
 * faltas completas con rebaje salarial y justificaciones con penalización >= 0.
 */

const SESSION_KEY = "assistance_generator_session_id";

function getSessionId() {
  let sid = sessionStorage.getItem(SESSION_KEY);
  if (!sid) {
    sid =
      (crypto.randomUUID && crypto.randomUUID()) ||
      `sess-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    sessionStorage.setItem(SESSION_KEY, sid);
  }
  return sid;
}

const SESSION_ID = getSessionId();
let currentState = null;

const dom = {
  statusBanner: document.getElementById("status-banner"),
  btnExportPdf: document.getElementById("btn-export-pdf"),
  fileBadge: document.getElementById("file-badge"),
  dropzone: document.getElementById("dropzone"),
  fileInput: document.getElementById("excel-file-input"),
  btnLoadSample: document.getElementById("btn-load-sample"),
  btnLoadExtended: document.getElementById("btn-load-extended"),

  // Periodo y Festivos
  holidaysBadge: document.getElementById("holidays-badge"),
  periodStartInput: document.getElementById("period-start-input"),
  periodEndInput: document.getElementById("period-end-input"),
  btnResetPeriod: document.getElementById("btn-reset-period"),
  btnApplyPeriod: document.getElementById("btn-apply-period"),
  holidayForm: document.getElementById("holiday-form"),
  holidayDateInput: document.getElementById("holiday-date-input"),
  holidayLabelInput: document.getElementById("holiday-label-input"),
  holidaysList: document.getElementById("holidays-list"),

  // Configuración
  configForm: document.getElementById("config-form"),
  modeFixed: document.getElementById("mode-fixed"),
  modeDuration: document.getElementById("mode-duration"),
  marginInput: document.getElementById("duplicate-margin-input"),
  wageInput: document.getElementById("hourly-wage-input"),
  absenceWeekdayInput: document.getElementById("absence-weekday-input"),
  absenceSaturdayInput: document.getElementById("absence-saturday-input"),
  btnSaveConfig: document.getElementById("btn-save-config"),

  // KPIs
  kpiSection: document.getElementById("kpi-section"),
  kpiPeriod: document.getElementById("kpi-period"),
  kpiEmployees: document.getElementById("kpi-employees"),
  kpiMissing: document.getElementById("kpi-missing"),
  kpiUsersMissing: document.getElementById("kpi-users-missing"),
  kpiJustified: document.getElementById("kpi-justified"),
  kpiTardiness: document.getElementById("kpi-tardiness"),
  kpiModeActive: document.getElementById("kpi-mode-active"),
  kpiDiscount: document.getElementById("kpi-discount"),
  kpiWageMeta: document.getElementById("kpi-wage-meta"),

  // Menú de Incidencias / Faltas
  incidentsSection: document.getElementById("incidents-section"),
  missingCounterBadge: document.getElementById("missing-counter-badge"),
  justifyForm: document.getElementById("justify-form"),
  justifyFieldset: document.getElementById("justify-fieldset"),
  employeeSelect: document.getElementById("employee-select"),
  slotSelect: document.getElementById("slot-select"),
  justificationInput: document.getElementById("justification-input"),
  penaltyInput: document.getElementById("penalty-input"),
  penaltyHint: document.getElementById("penalty-hint"),
  selectedSlotStatus: document.getElementById("selected-slot-status"),
  btnApplyJustification: document.getElementById("btn-apply-justification"),

  appliedNotesPanel: document.getElementById("applied-notes-panel"),
  appliedNotesList: document.getElementById("applied-notes-list"),

  emptyPreview: document.getElementById("empty-preview"),
  previewContainer: document.getElementById("preview-container"),
};

/* ==========================================================================
   Utilidades de Accesibilidad, Formato y Peticiones
   ========================================================================== */
function setupValidationAccessibility() {
  const syncAria = (el) => {
    if (!el || typeof el.checkValidity !== "function") return;
    if (CSS.supports("selector(:user-invalid)")) {
      el.setAttribute("aria-invalid", el.matches(":user-invalid") ? "true" : "false");
    } else {
      const valid = el.checkValidity();
      el.classList.toggle("user-invalid-fallback", !valid);
      el.setAttribute("aria-invalid", valid ? "false" : "true");
    }
  };

  document.addEventListener("blur", (e) => syncAria(e.target), true);
  document.addEventListener("input", (e) => {
    if (e.target && e.target.hasAttribute && e.target.hasAttribute("aria-invalid")) {
      syncAria(e.target);
    }
  });

  // Bloquear signos negativos o decimales en minutos de penalización (>= 0 entero)
  dom.penaltyInput.addEventListener("keydown", (e) => {
    if (["-", "+", "e", "E", ".", ","].includes(e.key)) {
      e.preventDefault();
    }
  });

  dom.penaltyInput.addEventListener("input", () => {
    const cleaned = dom.penaltyInput.value.replace(/[^\d]/g, "");
    if (dom.penaltyInput.value !== cleaned) {
      dom.penaltyInput.value = cleaned;
    }
    dom.penaltyInput.setCustomValidity("");
  });
}

function showBanner(message, type = "info") {
  dom.statusBanner.textContent = message;
  dom.statusBanner.className = `status-banner status-banner--${type}`;
  dom.statusBanner.hidden = false;
}

function formatCurrency(amount) {
  return new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency: "MXN",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount || 0);
}

function formatIsoToDisplay(isoStr) {
  if (!isoStr || !isoStr.includes("-")) return isoStr || "";
  const [y, m, d] = isoStr.split("-");
  const dt = new Date(Number(y), Number(m) - 1, Number(d));
  const days = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"];
  return `${days[dt.getDay()]} ${d}/${m}/${y}`;
}

async function apiRequest(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-Session-Id", SESSION_ID);

  const response = await fetch(path, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let errMsg = `Error HTTP ${response.status}`;
    try {
      const errData = await response.json();
      if (typeof errData.detail === "string") {
        errMsg = errData.detail;
      } else if (Array.isArray(errData.detail)) {
        errMsg = errData.detail.map((d) => d.msg).join("; ");
      }
    } catch {
      // Ignorar si no es JSON
    }
    throw new Error(errMsg);
  }

  return response.json();
}

function buildConfigPayload(overrides = {}) {
  const mode = dom.modeDuration.checked ? "duration" : "fixed_time";
  const margin = parseInt(dom.marginInput.value || "5", 10);
  const wage = parseFloat(dom.wageInput.value || "0");
  const absWeekday = parseFloat(dom.absenceWeekdayInput.value || "8");
  const absSaturday = parseFloat(dom.absenceSaturdayInput.value || "4");
  const pStart = dom.periodStartInput.value ? dom.periodStartInput.value : null;
  const pEnd = dom.periodEndInput.value ? dom.periodEndInput.value : null;
  const currentHolidays = currentState?.config?.holidays || [];

  return {
    tardiness_mode: mode,
    duplicate_margin_minutes: Number.isNaN(margin) ? 5 : margin,
    hourly_wage: Number.isNaN(wage) ? 0 : wage,
    absence_hours_weekday: Number.isNaN(absWeekday) ? 8 : absWeekday,
    absence_hours_saturday: Number.isNaN(absSaturday) ? 4 : absSaturday,
    period_start: pStart,
    period_end: pEnd,
    holidays: currentHolidays,
    ...overrides,
  };
}

/* ==========================================================================
   Sincronización de Interfaz con el Estado del Backend
   ========================================================================== */
function renderAppState(state, preserveSelection = null) {
  currentState = state;

  // 1. Controles de configuración
  if (state.config.tardiness_mode === "duration") {
    dom.modeDuration.checked = true;
  } else {
    dom.modeFixed.checked = true;
  }
  dom.marginInput.value = state.config.duplicate_margin_minutes;
  dom.wageInput.value = state.config.hourly_wage;
  dom.absenceWeekdayInput.value = state.config.absence_hours_weekday;
  dom.absenceSaturdayInput.value = state.config.absence_hours_saturday;
  dom.periodStartInput.value = state.config.period_start || "";
  dom.periodEndInput.value = state.config.period_end || "";

  // Renderizar festivos configurados
  renderHolidaysChips(state.config.holidays || []);

  if (!state.has_data || !state.summary) {
    dom.btnExportPdf.disabled = true;
    dom.btnResetPeriod.disabled = true;
    dom.kpiSection.hidden = true;
    dom.emptyPreview.hidden = false;
    dom.previewContainer.hidden = true;
    dom.justifyFieldset.disabled = true;
    dom.appliedNotesPanel.hidden = true;
    dom.fileBadge.textContent = "Sin archivo";
    dom.fileBadge.className = "badge badge--neutral";
    dom.missingCounterBadge.textContent = "Sin registros cargados";
    dom.missingCounterBadge.className = "badge badge--neutral";
    return;
  }

  dom.btnExportPdf.disabled = false;
  dom.btnResetPeriod.disabled = false;
  dom.kpiSection.hidden = false;
  dom.emptyPreview.hidden = true;
  dom.previewContainer.hidden = false;

  const s = state.summary;
  dom.fileBadge.textContent = s.source_filename;
  dom.fileBadge.className = "badge badge--info";

  // Pre-posicionar el selector de fecha festiva dentro del periodo activo si está vacío
  if (!dom.holidayDateInput.value && s.active_period_start_iso) {
    dom.holidayDateInput.value = s.active_period_start_iso;
  }

  dom.kpiPeriod.textContent =
    s.period_start === s.period_end ? s.period_start : `${s.period_start} – ${s.period_end}`;
  dom.kpiEmployees.textContent = `${s.total_employees} empleado(s) · ${s.holidays_count} festivo(s)`;
  dom.kpiMissing.textContent = String(s.pending_missing_slots_count);
  dom.kpiUsersMissing.textContent = `${s.unjustified_absences_count} falta(s) completa(s) · ${s.employees_with_missing_count} usuario(s)`;
  dom.kpiJustified.textContent = String(s.justified_slots_count);
  dom.kpiTardiness.textContent = `${s.total_tardiness_minutes} min`;
  dom.kpiModeActive.textContent =
    state.config.tardiness_mode === "fixed_time"
      ? `Modo: Hora límite · Margen ${state.config.duplicate_margin_minutes}m`
      : `Modo: Duración · Margen ${state.config.duplicate_margin_minutes}m`;
  dom.kpiDiscount.textContent = formatCurrency(s.total_discount);
  dom.kpiWageMeta.textContent = `Retardos: ${formatCurrency(s.total_tardiness_discount)} · Faltas: ${formatCurrency(s.total_absence_discount)}`;

  renderMissingMenu(state.employees_with_missing, preserveSelection);
  renderAppliedNotes(state.notes);
  renderReportPreview(state);
}

function renderHolidaysChips(holidays) {
  dom.holidaysList.replaceChildren();
  dom.holidaysBadge.textContent = `${holidays.length} festivo(s)`;

  if (!holidays || holidays.length === 0) {
    const emptyMsg = document.createElement("span");
    emptyMsg.className = "hint";
    emptyMsg.textContent = "Sin días festivos declarados en este periodo.";
    dom.holidaysList.appendChild(emptyMsg);
    return;
  }

  holidays.forEach((h) => {
    const chip = document.createElement("span");
    chip.className = "holiday-chip";

    const txt = document.createElement("span");
    txt.textContent = `◆ ${formatIsoToDisplay(h.date)} — ${h.label}`;

    const btnRem = document.createElement("button");
    btnRem.type = "button";
    btnRem.className = "holiday-chip__remove";
    btnRem.setAttribute("aria-label", `Quitar festivo ${h.date}`);
    btnRem.textContent = "✕";
    btnRem.addEventListener("click", () => handleRemoveHoliday(h.date));

    chip.append(txt, btnRem);
    dom.holidaysList.appendChild(chip);
  });
}

function renderMissingMenu(employeesWithMissing, preserveSelection = null) {
  dom.employeeSelect.replaceChildren();
  dom.slotSelect.replaceChildren();

  if (!employeesWithMissing || employeesWithMissing.length === 0) {
    dom.justifyFieldset.disabled = true;
    dom.missingCounterBadge.textContent = "0 incidencias pendientes";
    dom.missingCounterBadge.className = "badge badge--ok";

    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "Sin registros faltantes ni faltas en el periodo";
    dom.employeeSelect.appendChild(opt);

    const slotOpt = document.createElement("option");
    slotOpt.value = "";
    slotOpt.textContent = "Sin incidencias";
    dom.slotSelect.appendChild(slotOpt);
    return;
  }

  dom.justifyFieldset.disabled = false;
  const totalPending = employeesWithMissing.reduce((acc, e) => acc + e.pending_missing_count, 0);
  if (totalPending > 0) {
    dom.missingCounterBadge.textContent = `${totalPending} incidencia(s) pendiente(s)`;
    dom.missingCounterBadge.className = "badge badge--danger";
  } else {
    dom.missingCounterBadge.textContent = "Todas las incidencias fueron justificadas";
    dom.missingCounterBadge.className = "badge badge--ok";
  }

  employeesWithMissing.forEach((emp) => {
    const opt = document.createElement("option");
    opt.value = emp.employee_id;
    const statusDetail =
      emp.pending_missing_count > 0
        ? `${emp.pending_missing_count} pendiente(s)`
        : `${emp.justified_missing_count} justificado(s)`;
    opt.textContent = `[ID ${emp.employee_id}] ${emp.employee_name} — (${statusDetail})`;
    dom.employeeSelect.appendChild(opt);
  });

  let targetEmpId = preserveSelection?.employeeId;
  if (!targetEmpId || !employeesWithMissing.some((e) => e.employee_id === targetEmpId)) {
    const firstWithPending = employeesWithMissing.find((e) => e.pending_missing_count > 0);
    targetEmpId = firstWithPending ? firstWithPending.employee_id : employeesWithMissing[0].employee_id;
  }
  dom.employeeSelect.value = targetEmpId;

  populateMissingSlotsForEmployee(targetEmpId, preserveSelection?.slotKey || null);
}

function populateMissingSlotsForEmployee(employeeId, preferredSlotKey = null) {
  dom.slotSelect.replaceChildren();
  const emp = currentState?.employees_with_missing?.find((e) => e.employee_id === employeeId);
  if (!emp || emp.missing_slots.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "Sin registros faltantes";
    dom.slotSelect.appendChild(opt);
    return;
  }

  emp.missing_slots.forEach((slot) => {
    const opt = document.createElement("option");
    const key = `${slot.date}|${slot.slot_id}`;
    opt.value = key;
    opt.textContent = slot.dropdown_label;
    dom.slotSelect.appendChild(opt);
  });

  let selectedKey = preferredSlotKey;
  if (!selectedKey || !emp.missing_slots.some((s) => `${s.date}|${s.slot_id}` === selectedKey)) {
    const firstUnjustified = emp.missing_slots.find((s) => !s.is_justified);
    const chosen = firstUnjustified || emp.missing_slots[0];
    selectedKey = `${chosen.date}|${chosen.slot_id}`;
  }
  dom.slotSelect.value = selectedKey;
  syncFormWithSelectedSlot();
}

function syncFormWithSelectedSlot() {
  const empId = dom.employeeSelect.value;
  const rawKey = dom.slotSelect.value;
  if (!empId || !rawKey) return;

  const [dateIso, slotId] = rawKey.split("|");
  const emp = currentState?.employees_with_missing?.find((e) => e.employee_id === empId);
  const slotObj = emp?.missing_slots?.find((s) => s.date === dateIso && s.slot_id === slotId);
  if (!slotObj) return;

  if (slotObj.slot_id === "all_missing_day") {
    dom.penaltyHint.textContent =
      "Justificación grupal del día: aplicará el motivo a todos los bloques faltantes de ese día (la penalización se suma una sola vez).";
  } else if (slotObj.is_full_absence) {
    dom.penaltyHint.textContent =
      "Para FALTA COMPLETA: use 0 min si es falta justificada con goce de sueldo ($0 descuento), o ingrese los minutos a rebajar.";
  } else {
    dom.penaltyHint.textContent =
      "Use 0 para justificar sin penalización, o un entero positivo (min) para sumar al retardo.";
  }

  if (slotObj.is_justified) {
    dom.justificationInput.value = slotObj.current_justification || "";
    dom.penaltyInput.value = String(slotObj.current_penalty_minutes ?? 0);
    dom.selectedSlotStatus.textContent = `Editando Nota #${slotObj.note_number} aplicada en ${slotObj.display_date}.`;
    dom.btnApplyJustification.textContent = "Actualizar Observación en Reporte";
  } else {
    dom.justificationInput.value = "";
    dom.penaltyInput.value = "0";
    dom.selectedSlotStatus.textContent = `Capturando justificación para ${slotObj.display_date} (${slotObj.slot_label}).`;
    dom.btnApplyJustification.textContent = "Guardar Observación en Reporte";
  }
}

function renderAppliedNotes(notes) {
  dom.appliedNotesList.replaceChildren();
  if (!notes || notes.length === 0) {
    dom.appliedNotesPanel.hidden = true;
    return;
  }

  dom.appliedNotesPanel.hidden = false;
  notes.forEach((note) => {
    const row = document.createElement("div");
    row.className = "note-item";

    const info = document.createElement("div");
    info.className = "note-item__info";
    const penText =
      note.penalty_minutes > 0 ? `+${note.penalty_minutes} min penalización` : "0 min (sin descuento)";
    info.textContent = `Nota #${note.note_number} · [${note.employee_id}] ${note.employee_name} · ${note.display_date} (${note.slot_label}) · ${penText} — "${note.justification}"`;

    const actions = document.createElement("div");
    actions.className = "note-item__actions";

    const btnEdit = document.createElement("button");
    btnEdit.type = "button";
    btnEdit.className = "btn btn--ghost btn--sm";
    btnEdit.textContent = "Editar";
    btnEdit.addEventListener("click", () => {
      focusIncidentSlot(note.employee_id, note.date, note.slot_id);
    });

    const btnDel = document.createElement("button");
    btnDel.type = "button";
    btnDel.className = "btn btn--danger-outline";
    btnDel.textContent = "Quitar";
    btnDel.addEventListener("click", () => {
      handleDeleteJustification(note.employee_id, note.date, note.slot_id);
    });

    actions.append(btnEdit, btnDel);
    row.append(info, actions);
    dom.appliedNotesList.appendChild(row);
  });
}

function focusIncidentSlot(employeeId, dateIso, slotId) {
  dom.employeeSelect.value = employeeId;
  populateMissingSlotsForEmployee(employeeId, `${dateIso}|${slotId}`);
  dom.incidentsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  dom.justificationInput.focus();
}

/* ==========================================================================
   Renderizado de la Vista Previa del Reporte:
   - Tabla 1: Jornada Lunes a Viernes (08:15 a 18:00+ · 6 checadas · Completo 6/6)
   - Tabla 2: Jornada Sábados (09:00 a 13:00+ · 2 checadas · Completo 2/2)
   - Domingos: Excluidos como días de descanso oficial (sin filas vacías 6/6)
   ========================================================================== */
function buildDayCellsForSpecialStates(tr, day, empId, colSpanCount, isSaturday = false) {
  if (day.row_status === "holiday") {
    tr.classList.add("row-holiday");
    const tdSpan = document.createElement("td");
    tdSpan.colSpan = colSpanCount;
    tdSpan.textContent = `◆ DÍA FESTIVO OFICIAL: ${day.holiday_label || "Día festivo"}`;
    const tdLate = document.createElement("td");
    tdLate.textContent = "0 min";
    const tdDisc = document.createElement("td");
    tdDisc.textContent = formatCurrency(0);
    const tdSt = document.createElement("td");
    tdSt.textContent = "◆ FESTIVO";
    tr.append(tdSpan, tdLate, tdDisc, tdSt);
    return true;
  }

  if (day.row_status === "absence") {
    tr.classList.add("row-absence");
    const tdSpan = document.createElement("td");
    tdSpan.colSpan = colSpanCount;
    tdSpan.className = "cell-missing";
    const msgDiv = document.createElement("div");
    const shiftType = isSaturday ? "Sábado 09:00–13:00" : "Jornada Lunes–Viernes";
    msgDiv.textContent = `⚠ FALTA INJUSTIFICADA — Sin checadas registradas (${shiftType})`;
    const btnJust = document.createElement("button");
    btnJust.type = "button";
    btnJust.className = "cell-ActionLink";
    btnJust.textContent = "Justificar falta →";
    btnJust.addEventListener("click", () => {
      focusIncidentSlot(empId, day.date, "full_absence");
    });
    tdSpan.append(msgDiv, btnJust);

    const tdHrs = document.createElement("td");
    tdHrs.className = "cell-missing";
    tdHrs.textContent = `-${day.absence_hours_applied} hrs`;

    const tdDisc = document.createElement("td");
    tdDisc.className = "cell-missing";
    tdDisc.textContent = formatCurrency(day.daily_discount);

    const tdSt = document.createElement("td");
    tdSt.className = "cell-missing";
    tdSt.textContent = isSaturday ? "● FALTA (0/2)" : "● FALTA (0/6)";

    tr.append(tdSpan, tdHrs, tdDisc, tdSt);
    return true;
  }

  if (day.row_status === "justified_absence") {
    tr.classList.add("row-justified-absence");
    const noteSlot = day.slots.find((s) => s.note_number !== null);
    const noteNum = noteSlot ? noteSlot.note_number : 1;

    const tdSpan = document.createElement("td");
    tdSpan.colSpan = colSpanCount;
    tdSpan.className = "cell-justified";
    tdSpan.title = noteSlot?.justification || "";
    tdSpan.textContent = `★ FALTA JUSTIFICADA [Nota #${noteNum}] — "${noteSlot?.justification || ""}"`;

    const tdPen = document.createElement("td");
    tdPen.className = "cell-justified";
    tdPen.textContent =
      day.penalty_tardiness_minutes > 0
        ? `+${day.penalty_tardiness_minutes} min`
        : "0 min (Sin desc.)";

    const tdDisc = document.createElement("td");
    tdDisc.className = "cell-justified";
    tdDisc.textContent = formatCurrency(day.daily_discount);

    const tdSt = document.createElement("td");
    tdSt.className = "cell-justified";
    tdSt.textContent = `★ FALTA JUSTIF. (Nota #${noteNum})`;

    tr.append(tdSpan, tdPen, tdDisc, tdSt);
    return true;
  }

  return false;
}

function buildSlotCell(slot, empId, dateIso, hasNoteTint, earlyExitText) {
  const td = document.createElement("td");
  if (hasNoteTint) {
    td.classList.add("col-note-tint");
  }

  if (slot.status === "missing") {
    td.classList.add("cell-missing");
    const txt = document.createElement("div");
    txt.textContent = "⚠ SIN REGISTRO";
    const actionBtn = document.createElement("button");
    actionBtn.type = "button";
    actionBtn.className = "cell-ActionLink";
    actionBtn.textContent = "Justificar →";
    actionBtn.addEventListener("click", () => {
      focusIncidentSlot(empId, dateIso, slot.slot_id);
    });
    td.append(txt, actionBtn);
  } else if (slot.status === "justified") {
    td.classList.add("cell-justified");
    td.title = slot.justification || "";
    const mainTxt = document.createElement("div");
    mainTxt.textContent = "★ JUSTIFICADO";
    const subTxt = document.createElement("span");
    subTxt.className = "cell-sub";
    const penLabel = slot.penalty_minutes > 0 ? ` (+${slot.penalty_minutes}m)` : " (0m)";
    subTxt.textContent = `[Nota #${slot.note_number}]${penLabel}`;
    td.append(mainTxt, subTxt);
  } else if (slot.status === "late") {
    td.classList.add("cell-late");
    td.textContent = slot.recorded_time;
    const subTxt = document.createElement("span");
    subTxt.className = "cell-sub";
    subTxt.textContent = `▲ +${slot.tardiness_minutes} min`;
    td.appendChild(subTxt);
  } else if (slot.status === "early_exit") {
    td.classList.add("cell-late");
    td.textContent = slot.recorded_time;
    const subTxt = document.createElement("span");
    subTxt.className = "cell-sub";
    subTxt.textContent = earlyExitText;
    td.appendChild(subTxt);
  } else {
    td.textContent = slot.recorded_time || "—";
  }

  return td;
}

function buildWeekdaySubtable(emp, weekdayDays) {
  const noteCols = new Set();
  let hasNotes = false;
  weekdayDays.forEach((day) => {
    if (day.has_notes) hasNotes = true;
    if (day.row_status === "justified_complete") {
      day.slots.forEach((slot, idx) => {
        if (slot.status === "justified" || slot.note_number !== null) {
          noteCols.add(idx);
        }
      });
    }
  });

  const captionId = `caption-lv-${emp.employee_id}`;
  const wrapper = document.createElement("div");
  wrapper.className = "table-wrapper";
  wrapper.tabIndex = 0;
  wrapper.setAttribute("role", "region");
  wrapper.setAttribute("aria-labelledby", captionId);

  const table = document.createElement("table");
  table.className = "report-table";

  const caption = document.createElement("caption");
  caption.id = captionId;
  caption.textContent = `Jornada Lunes a Viernes (6 registros diarios: Entrada 08:15 · Almuerzo 09:40–10:00 · Comida 14:00–15:00 · Salida 18:00+)`;
  table.appendChild(caption);

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");

  const thDate = document.createElement("th");
  thDate.scope = "col";
  thDate.textContent = "Día / Fecha (L–V)";
  headRow.appendChild(thDate);

  const lvHeaders = [
    "Entrada (08:15)",
    "Sal. Almuerzo (09:40)",
    "Reg. Almuerzo (10:00)",
    "Sal. Comida (14:00)",
    "Reg. Comida (15:00)",
    "Salida (18:00+)",
  ];

  lvHeaders.forEach((label, idx) => {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = label;
    if (noteCols.has(idx)) {
      th.classList.add("col-has-note");
      const sub = document.createElement("span");
      sub.className = "cell-sub";
      sub.textContent = "★ COLUMNA CON NOTA";
      th.appendChild(sub);
    }
    headRow.appendChild(th);
  });

  const thLate = document.createElement("th");
  thLate.scope = "col";
  thLate.textContent = "Retardo / Hrs Falta";

  const thDisc = document.createElement("th");
  thDisc.scope = "col";
  thDisc.textContent = "Descuento ($)";

  const thStatus = document.createElement("th");
  thStatus.scope = "col";
  thStatus.textContent = "Estado L–V";
  if (hasNotes) thStatus.classList.add("col-has-note");

  headRow.append(thLate, thDisc, thStatus);
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  weekdayDays.forEach((day) => {
    const tr = document.createElement("tr");
    const thRowDate = document.createElement("th");
    thRowDate.scope = "row";
    thRowDate.textContent = `${day.weekday_name} ${day.display_date}`;
    if (day.discarded_duplicates?.length > 0) {
      const dupSub = document.createElement("span");
      dupSub.className = "cell-sub";
      dupSub.textContent = `Dup. omitido: ${day.discarded_duplicates.join(", ")}`;
      thRowDate.appendChild(dupSub);
    }
    tr.appendChild(thRowDate);

    if (buildDayCellsForSpecialStates(tr, day, emp.employee_id, 6, false)) {
      tbody.appendChild(tr);
      return;
    }

    if (day.row_status === "incomplete") {
      tr.classList.add("row-incomplete");
    }

    const dayNoteTags = [];
    day.slots.forEach((slot, idx) => {
      if (slot.status === "justified") {
        dayNoteTags.push(`Nota #${slot.note_number}`);
      }
      tr.appendChild(buildSlotCell(slot, emp.employee_id, day.date, noteCols.has(idx), "Antes 18:00"));
    });

    const tdLate = document.createElement("td");
    if (day.total_tardiness_minutes > 0) {
      tdLate.classList.add("cell-late");
      tdLate.textContent = `${day.total_tardiness_minutes} min`;
      if (day.penalty_tardiness_minutes > 0) {
        const sub = document.createElement("span");
        sub.className = "cell-sub";
        sub.textContent = `${day.schedule_tardiness_minutes}h + ${day.penalty_tardiness_minutes}p`;
        tdLate.appendChild(sub);
      }
    } else {
      tdLate.textContent = "0 min";
    }

    const tdDisc = document.createElement("td");
    tdDisc.textContent = formatCurrency(day.daily_discount);

    const tdStatus = document.createElement("td");
    if (day.row_status === "incomplete") {
      const recordedCnt = 6 - day.unjustified_missing_count - day.justified_count;
      tdStatus.classList.add("cell-missing");
      tdStatus.textContent = `● INCOMPLETO (${recordedCnt}/6 · Faltan ${day.unjustified_missing_count})`;
    } else if (dayNoteTags.length > 0) {
      tdStatus.classList.add("cell-justified");
      tdStatus.textContent = `★ JUSTIFICADO (6/6 · ${dayNoteTags.join(", ")})`;
    } else {
      tdStatus.textContent = "✓ Completo (6/6)";
    }

    tr.append(tdLate, tdDisc, tdStatus);
    tbody.appendChild(tr);
  });

  table.appendChild(tbody);
  wrapper.appendChild(table);
  return wrapper;
}

function buildSaturdaySubtable(emp, saturdayDays) {
  const satNoteCols = new Set();
  let hasNotes = false;
  saturdayDays.forEach((day) => {
    if (day.has_notes) hasNotes = true;
    if (day.row_status === "justified_complete") {
      if (day.slots[0].status === "justified") satNoteCols.add(0);
      if (day.slots[5].status === "justified") satNoteCols.add(1);
    }
  });

  const captionId = `caption-sab-${emp.employee_id}`;
  const wrapper = document.createElement("div");
  wrapper.className = "table-wrapper";
  wrapper.tabIndex = 0;
  wrapper.setAttribute("role", "region");
  wrapper.setAttribute("aria-labelledby", captionId);

  const table = document.createElement("table");
  table.className = "report-table report-table--saturday";

  const caption = document.createElement("caption");
  caption.id = captionId;
  caption.textContent = `Jornada Sábados (Únicamente 2 registros: Entrada 09:00 hrs y Salida 13:00+ hrs)`;
  table.appendChild(caption);

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");

  const thDate = document.createElement("th");
  thDate.scope = "col";
  thDate.textContent = "Día / Fecha (Sábado)";
  headRow.appendChild(thDate);

  ["Entrada Sábado (09:00)", "Salida Sábado (13:00+)"].forEach((label, idx) => {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = label;
    if (satNoteCols.has(idx)) {
      th.classList.add("col-has-note");
      const sub = document.createElement("span");
      sub.className = "cell-sub";
      sub.textContent = "★ COLUMNA CON NOTA";
      th.appendChild(sub);
    }
    headRow.appendChild(th);
  });

  const thLate = document.createElement("th");
  thLate.scope = "col";
  thLate.textContent = "Retardo / Hrs Falta";

  const thDisc = document.createElement("th");
  thDisc.scope = "col";
  thDisc.textContent = "Descuento ($)";

  const thStatus = document.createElement("th");
  thStatus.scope = "col";
  thStatus.textContent = "Estado Sábado";
  if (hasNotes) thStatus.classList.add("col-has-note");

  headRow.append(thLate, thDisc, thStatus);
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  saturdayDays.forEach((day) => {
    const tr = document.createElement("tr");
    const thRowDate = document.createElement("th");
    thRowDate.scope = "row";
    thRowDate.textContent = `${day.weekday_name} ${day.display_date}`;
    if (day.discarded_duplicates?.length > 0) {
      const dupSub = document.createElement("span");
      dupSub.className = "cell-sub";
      dupSub.textContent = `Dup. omitido: ${day.discarded_duplicates.join(", ")}`;
      thRowDate.appendChild(dupSub);
    }
    tr.appendChild(thRowDate);

    if (buildDayCellsForSpecialStates(tr, day, emp.employee_id, 2, true)) {
      tbody.appendChild(tr);
      return;
    }

    if (day.row_status === "incomplete") {
      tr.classList.add("row-incomplete");
    }

    const dayNoteTags = [];
    const activeSatSlots = [day.slots[0], day.slots[5]];
    activeSatSlots.forEach((slot, idx) => {
      if (slot.status === "justified") {
        dayNoteTags.push(`Nota #${slot.note_number}`);
      }
      tr.appendChild(buildSlotCell(slot, emp.employee_id, day.date, satNoteCols.has(idx), "Antes 13:00"));
    });

    const tdLate = document.createElement("td");
    if (day.total_tardiness_minutes > 0) {
      tdLate.classList.add("cell-late");
      tdLate.textContent = `${day.total_tardiness_minutes} min`;
    } else {
      tdLate.textContent = "0 min";
    }

    const tdDisc = document.createElement("td");
    tdDisc.textContent = formatCurrency(day.daily_discount);

    const tdStatus = document.createElement("td");
    if (day.row_status === "incomplete") {
      tdStatus.classList.add("cell-missing");
      tdStatus.textContent = "● INCOMPLETO (1/2 · Falta 1)";
    } else if (dayNoteTags.length > 0) {
      tdStatus.classList.add("cell-justified");
      tdStatus.textContent = `★ JUSTIFICADO (2/2 · ${dayNoteTags.join(", ")})`;
    } else {
      tdStatus.textContent = "✓ Completo (2/2)";
    }

    tr.append(tdLate, tdDisc, tdStatus);
    tbody.appendChild(tr);
  });

  table.appendChild(tbody);
  wrapper.appendChild(table);
  return wrapper;
}

function renderReportPreview(state) {
  dom.previewContainer.replaceChildren();

  state.employees.forEach((emp) => {
    const block = document.createElement("article");
    block.className = "emp-block";

    const banner = document.createElement("div");
    banner.className = "emp-block__banner";

    const title = document.createElement("strong");
    title.textContent = `[ID ${emp.employee_id}] ${emp.employee_name}`;

    const metrics = document.createElement("div");
    metrics.className = "emp-block__metrics";
    metrics.textContent =
      `Días lab. evaluados: ${emp.days_analyzed} (Domingos: Descanso)  |  ` +
      `Faltas injust.: ${emp.unjustified_absences_count} (${emp.total_absence_hours}h)  |  ` +
      `Notas: ${emp.justified_notes_count}  |  Retardo: ${emp.total_tardiness_minutes} min  |  ` +
      `Descuento Total: ${formatCurrency(emp.total_discount)}`;

    banner.append(title, metrics);
    block.appendChild(banner);

    const weekdayDays = emp.days.filter((d) => d.weekday_index < 5);
    const saturdayDays = emp.days.filter((d) => d.weekday_index === 5);

    if (weekdayDays.length > 0) {
      block.appendChild(buildWeekdaySubtable(emp, weekdayDays));
    }

    if (saturdayDays.length > 0) {
      block.appendChild(buildSaturdaySubtable(emp, saturdayDays));
    }

    const totalsBar = document.createElement("div");
    totalsBar.className = "emp-totals-bar";

    const totLeft = document.createElement("span");
    totLeft.textContent = `TOTAL ACUMULADO PERIODO — ${emp.employee_name}`;

    const totRight = document.createElement("span");
    const absExtra = emp.total_absence_hours > 0 ? ` + ${emp.total_absence_hours}h faltas` : "";
    totRight.textContent =
      `Retardo: ${emp.total_tardiness_minutes} min${absExtra}  ·  ` +
      `Descuento Total: ${formatCurrency(emp.total_discount)} ` +
      `(Ret.: ${formatCurrency(emp.tardiness_discount)} + Faltas: ${formatCurrency(emp.absence_discount)})  ·  ` +
      `${emp.unjustified_absences_count} falta(s) / ${emp.justified_notes_count} nota(s)`;

    totalsBar.append(totLeft, totRight);
    block.appendChild(totalsBar);

    dom.previewContainer.appendChild(block);
  });
}

/* ==========================================================================
   Acciones e Interacción con la API
   ========================================================================== */
async function handleFileUpload(file) {
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);

  try {
    showBanner(`Analizando archivo "${file.name}"...`, "info");
    const state = await apiRequest("/api/upload", {
      method: "POST",
      body: formData,
    });
    renderAppState(state);
    showBanner(
      `Archivo "${file.name}" procesado: ${state.summary.total_employees} empleado(s), ` +
        `periodo ${state.summary.period_start} al ${state.summary.period_end}.`,
      "success"
    );
  } catch (err) {
    showBanner(`Error al cargar archivo: ${err.message}`, "error");
  } finally {
    dom.fileInput.value = "";
  }
}

async function handleLoadSample(extraCases = false) {
  try {
    showBanner("Cargando datos de demostración del reloj checador...", "info");
    const state = await apiRequest(`/api/load-sample?extra_cases=${extraCases}`, {
      method: "POST",
    });
    renderAppState(state);
    showBanner(
      `Datos cargados (${state.summary.source_filename}). Rango detectado: ${state.summary.period_start} al ${state.summary.period_end}.`,
      "success"
    );
  } catch (err) {
    showBanner(`No se pudo cargar el ejemplo: ${err.message}`, "error");
  }
}

async function pushConfigToServer(payload, successMessage) {
  dom.btnSaveConfig.disabled = true;
  try {
    const state = await apiRequest("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderAppState(state, {
      employeeId: dom.employeeSelect.value,
      slotKey: dom.slotSelect.value,
    });
    if (successMessage) {
      showBanner(successMessage, "success");
    }
  } catch (err) {
    showBanner(`Error en configuración: ${err.message}`, "error");
  } finally {
    dom.btnSaveConfig.disabled = false;
  }
}

async function submitConfigUpdate() {
  if (!dom.configForm.reportValidity()) return;
  const payload = buildConfigPayload();
  await pushConfigToServer(
    payload,
    `Configuración aplicada: Modo ${payload.tardiness_mode === "fixed_time" ? "Hora Límite" : "Duración"}, ` +
      `falta L-V ${payload.absence_hours_weekday}h / Sáb ${payload.absence_hours_saturday}h, salario ${formatCurrency(payload.hourly_wage)}/hr.`
  );
}

async function handleApplyPeriod() {
  const payload = buildConfigPayload();
  await pushConfigToServer(payload, "Rango de fechas del periodo actualizado.");
}

async function handleResetPeriod() {
  if (!currentState?.summary) return;
  dom.periodStartInput.value = currentState.summary.detected_min_date || "";
  dom.periodEndInput.value = currentState.summary.detected_max_date || "";
  const payload = buildConfigPayload({
    period_start: currentState.summary.detected_min_date,
    period_end: currentState.summary.detected_max_date,
  });
  await pushConfigToServer(payload, "Se restauraron las fechas originales detectadas en el Excel.");
}

async function handleAddHoliday(event) {
  event.preventDefault();
  if (!dom.holidayForm.reportValidity()) return;

  const dtVal = dom.holidayDateInput.value;
  const lblVal = dom.holidayLabelInput.value.trim() || "Día festivo oficial";
  const existing = (currentState?.config?.holidays || []).filter((h) => h.date !== dtVal);
  const nextHolidays = [...existing, { date: dtVal, label: lblVal }];

  const payload = buildConfigPayload({ holidays: nextHolidays });
  await pushConfigToServer(
    payload,
    `Día festivo agregado (${formatIsoToDisplay(dtVal)} — ${lblVal}). Las inasistencias de ese día quedaron exentas.`
  );
  dom.holidayLabelInput.value = "";
}

async function handleRemoveHoliday(dateIso) {
  const nextHolidays = (currentState?.config?.holidays || []).filter((h) => h.date !== dateIso);
  const payload = buildConfigPayload({ holidays: nextHolidays });
  await pushConfigToServer(payload, `Día festivo ${formatIsoToDisplay(dateIso)} eliminado.`);
}

async function handleJustificationSubmit(event) {
  event.preventDefault();

  const textTrimmed = dom.justificationInput.value.trim();
  if (!textTrimmed) {
    dom.justificationInput.setCustomValidity("Ingrese el motivo de la ausencia del registro o falta.");
  } else {
    dom.justificationInput.setCustomValidity("");
  }

  const penaltyVal = Number(dom.penaltyInput.value);
  if (!Number.isInteger(penaltyVal) || penaltyVal < 0) {
    dom.penaltyInput.setCustomValidity("Ingrese un entero mayor o igual a 0.");
  } else {
    dom.penaltyInput.setCustomValidity("");
  }

  if (!dom.justifyForm.reportValidity()) return;

  const empId = dom.employeeSelect.value;
  const [dateIso, slotId] = dom.slotSelect.value.split("|");

  dom.btnApplyJustification.disabled = true;
  try {
    const state = await apiRequest("/api/justify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        employee_id: empId,
        date: dateIso,
        slot_id: slotId,
        justification: textTrimmed,
        penalty_minutes: penaltyVal,
      }),
    });
    renderAppState(state, { employeeId: empId });
    showBanner(
      `Observación guardada exitosamente (${penaltyVal} min de penalización). Se actualizó el cálculo y el resaltado para el PDF.`,
      "success"
    );
  } catch (err) {
    showBanner(`No se pudo guardar la justificación: ${err.message}`, "error");
  } finally {
    dom.btnApplyJustification.disabled = false;
  }
}

async function handleDeleteJustification(employeeId, dateIso, slotId) {
  try {
    const params = new URLSearchParams({
      employee_id: employeeId,
      date: dateIso,
      slot_id: slotId,
    });
    const state = await apiRequest(`/api/justify?${params.toString()}`, {
      method: "DELETE",
    });
    renderAppState(state, {
      employeeId,
      slotKey: `${dateIso}|${slotId}`,
    });
    showBanner("Observación eliminada; el registro o falta volvió a su estado original.", "info");
  } catch (err) {
    showBanner(`Error al eliminar observación: ${err.message}`, "error");
  }
}

async function handleExportPdf() {
  if (!currentState?.has_data) return;
  dom.btnExportPdf.disabled = true;
  const originalLabel = dom.btnExportPdf.textContent;
  dom.btnExportPdf.textContent = "Generando PDF Protegido...";

  try {
    const response = await fetch(`/api/export-pdf?session_id=${encodeURIComponent(SESSION_ID)}`, {
      method: "GET",
      headers: { "X-Session-Id": SESSION_ID },
    });

    if (!response.ok) {
      const errJson = await response.json().catch(() => ({}));
      throw new Error(errJson.detail || `Error HTTP ${response.status}`);
    }

    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : "Reporte_Asistencia_Protegido.pdf";

    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);

    showBanner(
      `PDF "${filename}" descargado con cifrado AES-256 y trama de integridad anti-modificación.`,
      "success"
    );
  } catch (err) {
    showBanner(`Error al generar el PDF: ${err.message}`, "error");
  } finally {
    dom.btnExportPdf.disabled = false;
    dom.btnExportPdf.textContent = originalLabel;
  }
}

/* ==========================================================================
   Inicialización
   ========================================================================== */
function init() {
  setupValidationAccessibility();

  dom.fileInput.addEventListener("change", (e) => {
    const file = e.target.files?.[0];
    if (file) handleFileUpload(file);
  });

  dom.dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dom.dropzone.classList.add("is-dragover");
  });

  dom.dropzone.addEventListener("dragleave", () => {
    dom.dropzone.classList.remove("is-dragover");
  });

  dom.dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dom.dropzone.classList.remove("is-dragover");
    const file = e.dataTransfer?.files?.[0];
    if (file) handleFileUpload(file);
  });

  dom.btnLoadSample.addEventListener("click", () => handleLoadSample(false));
  dom.btnLoadExtended.addEventListener("click", () => handleLoadSample(true));

  // Periodo y Festivos
  dom.btnApplyPeriod.addEventListener("click", handleApplyPeriod);
  dom.btnResetPeriod.addEventListener("click", handleResetPeriod);
  dom.holidayForm.addEventListener("submit", handleAddHoliday);

  // Configuración general
  dom.configForm.addEventListener("submit", (e) => {
    e.preventDefault();
    submitConfigUpdate();
  });
  dom.modeFixed.addEventListener("change", submitConfigUpdate);
  dom.modeDuration.addEventListener("change", submitConfigUpdate);

  // Menú de justificaciones
  dom.employeeSelect.addEventListener("change", () => {
    populateMissingSlotsForEmployee(dom.employeeSelect.value);
  });
  dom.slotSelect.addEventListener("change", syncFormWithSelectedSlot);
  dom.justificationInput.addEventListener("input", () => {
    dom.justificationInput.setCustomValidity("");
  });
  dom.justifyForm.addEventListener("submit", handleJustificationSubmit);

  dom.btnExportPdf.addEventListener("click", handleExportPdf);

  apiRequest("/api/state")
    .then((state) => renderAppState(state))
    .catch(() => {});
}

init();
