@echo off
title Analizador de Asistencia y Reportes PDF
echo Iniciando servidor FastAPI en http://127.0.0.1:8000 ...
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
pause
