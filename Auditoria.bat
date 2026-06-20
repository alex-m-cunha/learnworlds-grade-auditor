@echo off
cd /d "%~dp0"
where python >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('Python nao encontrado. Instala em python.org.','Erro')"
    exit /b 1
)
python run_audit_gui.py
