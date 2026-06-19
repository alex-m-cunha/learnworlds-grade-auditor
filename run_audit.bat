@echo off
REM Windows guided launcher for the full audit pipeline.
REM Double-click. Asks for the assessment id, lets you PICK the exam-config XLSX
REM (defaults to the input\ folder), and runs:
REM   submissions (API)  ->  exam-config (XLSX)  ->  reconcile
REM No credentials here — everything is read from .env.

setlocal enableextensions
cd /d "%~dp0"

echo ============================================================
echo  LearnWorlds - Auditoria de Avaliacao (Windows)
echo  Pasta: %CD%
echo ============================================================

REM --- Python + venv ---
where py >nul 2>&1
if %ERRORLEVEL%==0 (set "PYTHON=py -3") else (set "PYTHON=python")

if not exist "%~dp0.venv" (
    echo A criar o ambiente ^(.venv^)...
    %PYTHON% -m venv "%~dp0.venv"
    if errorlevel 1 ( echo ERRO ao criar .venv & pause & exit /b 1 )
)
call "%~dp0.venv\Scripts\activate.bat"
echo A instalar/atualizar dependencias...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r "%~dp0requirements.txt" >nul 2>&1
if errorlevel 1 ( echo ERRO ao instalar dependencias & pause & exit /b 1 )

if not exist "%~dp0.env" echo AVISO: nao existe .env. Copia .env.example para .env e mete um token valido.

REM --- 1) Escolher o XLSX (gabarito) via dialogo ---
echo.
echo Escolhe o ficheiro de configuracao do teste (XLSX)...
set "XLSX="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command ^
  "Add-Type -AssemblyName System.Windows.Forms; $f=New-Object System.Windows.Forms.OpenFileDialog; $f.Filter='Excel (*.xlsx)|*.xlsx'; $f.InitialDirectory=(Join-Path '%~dp0' 'input'); if($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){[Console]::WriteLine($f.FileName)}"`) do set "XLSX=%%I"

REM --- 2) Assessment ID ---
set /p "AID=Assessment ID do teste: "
if "%AID%"=="" ( echo Cancelado: e preciso o Assessment ID. & pause & exit /b 1 )

REM --- 3) Titulo (pasta de output) ---
set /p "LABEL=Titulo (nome da pasta de output): "
if "%LABEL%"=="" ( echo Cancelado: e preciso um titulo. & pause & exit /b 1 )

echo.
echo Assessment : %AID%
echo Titulo     : %LABEL%
echo XLSX       : %XLSX%
echo ------------------------------------------------------------

echo ^>^> Submissoes...
python -m extractor.run_extract --assessment-id "%AID%" --label "%LABEL%"
if errorlevel 1 ( echo ERRO nas submissoes ^(token invalido?^). & pause & exit /b 1 )

if not "%XLSX%"=="" (
    echo.
    echo ^>^> Configuracao do teste ^(gabarito^)...
    python -m extractor.run_exam_config --xlsx "%XLSX%" --assessment-id "%AID%" --label "%LABEL%"
    if errorlevel 1 ( echo ERRO no exam-config & pause & exit /b 1 )
    echo.
    echo ^>^> Reconciliacao...
    python -m reconcile.run_reconcile --label "%LABEL%"
    if errorlevel 1 ( echo ERRO na reconciliacao & pause & exit /b 1 )
) else (
    echo ^(sem XLSX - saltado o gabarito e a reconciliacao^)
)

echo ------------------------------------------------------------
echo Concluido. Ve os caminhos exatos acima ^(pasta "output\"^).
pause
endlocal
