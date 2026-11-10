# Sistema Analizador de Asistencia, Faltas y Generador de Reportes PDF

Aplicación modular con backend en **FastAPI (Python)** e interfaz web interactiva para analizar archivos `.xlsx` / `.csv` exportados directamente por el reloj checador (`Reporte de Eventos`), evaluando horarios diferenciados por día de la semana, días festivos, faltas completas, retardos y descuentos salariales, exportando reportes en **PDF protegidos contra modificaciones**.

---

## Reglas de Horario Soportadas

* **Lunes a Viernes (6 checadas)**:
  * Entrada oficial: `08:15 hrs`
  * Almuerzo: `09:40` a `10:00 hrs`
  * Comida: `14:00` a `15:00 hrs`
  * Salida final: `18:00+ hrs`
* **Sábado (2 checadas)**:
  * Entrada oficial: `09:00 hrs`
  * Salida final: `13:00+ hrs` *(los bloques de alimentos se marcan automáticamente como `N/A`)*
* **Domingo**:
  * Día de descanso oficial (no genera faltas ni descuentos).
* **Días Festivos Configurables**:
  * Se declaran desde la interfaz para el periodo activo; eximen a todo el personal de faltas y descuentos en esa fecha.
* **Faltas Completas (Lunes a Sábado sin checadas)**:
  * Se marcan en **Rojo (`FALTA`)** y descuentan automáticamente las horas configuradas para Lunes–Viernes (ej. `8 hrs` por defecto, ajustable) o Sábado (`4 hrs` por defecto, ajustable), salvo que se justifiquen en el menú de incidencias.

---

## Arquitectura Modular

1. **[`app/modules/excel_parser.py`](app/modules/excel_parser.py) — Módulo 1 (Ingesta y Limpieza)**:
   * Detecta dinámicamente los encabezados (`ID`, `Nombre`, `Fecha / Hora`) e ignora filas vacías, títulos y pies de página.
2. **[`app/modules/attendance_engine.py`](app/modules/attendance_engine.py) — Módulo 2 (Motor de Horarios, Sábados, Festivos, Faltas y Descuentos)**:
   * Deduplicación con margen configurable (`5 min` por defecto) conservando siempre la primera hora registrada.
   * Switch de modo de retardo (`Por Hora Límite` vs `Por Duración de Bloque`).
   * Calendario continuo del periodo (`period_start` a `period_end`), detección de faltas completas y cálculo de descuentos económicos.
3. **[`app/modules/state_manager.py`](app/modules/state_manager.py) — Módulo 3 (Gestor de Estado e Incidencias)**:
   * Sincroniza automáticamente el rango del periodo al subir un Excel y permite recalcular en vivo al editar festivos, configuraciones o justificaciones.
4. **[`app/modules/pdf_generator.py`](app/modules/pdf_generator.py) — Módulo 4 (Generador PDF Protegido)**:
   * Código de colores (Rojo para faltantes/faltas, Ámbar/Dorado para columnas y filas con notas, Naranja para retardos, Azul para festivos) + **cifrado AES-256** sin permisos de edición y **trama Guilloché + microtexto con sello SHA-256**.
5. **[`app/api/routes.py`](app/api/routes.py) + [`static/`](static/) — Módulo 5 (API REST e Interfaz Web)**.

---

## Ejecución Rápida

### 1. Iniciar el servidor web
Haz doble clic en `run_server.bat` o ejecuta en terminal:
```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Abre en tu navegador: **`http://127.0.0.1:8000`**

### 2. Ejecutar pruebas automatizadas
```powershell
.\.venv\Scripts\python -m pytest -v
```
