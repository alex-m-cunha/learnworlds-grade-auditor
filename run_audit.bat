@echo off
chcp 65001 >nul
REM Windows unified launcher for the LearnWorlds audit pipeline (6 steps).
REM Double-click this file. No credentials here — read from .env.
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"

echo ============================================================
echo  LearnWorlds -- Lancador de Auditoria (Windows)
echo  Pasta: %SCRIPT_DIR%
echo ============================================================
echo.

REM ── Python + venv ─────────────────────────────────────────────
where py >nul 2>&1
if %ERRORLEVEL%==0 (set "PYTHON=py -3") else (set "PYTHON=python")
if not exist "%SCRIPT_DIR%\.venv" (
    echo A criar o ambiente (.venv)...
    %PYTHON% -m venv "%SCRIPT_DIR%\.venv"
    if errorlevel 1 ( echo ERRO ao criar .venv & pause & exit /b 1 )
)
call "%SCRIPT_DIR%\.venv\Scripts\activate.bat"
echo A instalar/atualizar dependencias...
python -m pip install --upgrade pip -q >nul 2>&1
python -m pip install -r "%SCRIPT_DIR%\requirements.txt" -q >nul 2>&1
if errorlevel 1 ( echo ERRO ao instalar dependencias & pause & exit /b 1 )

if not exist "%SCRIPT_DIR%\.env" echo AVISO: nao existe .env. Copia .env.example para .env e adiciona um token valido.

REM ── Timestamp partilhado ──────────────────────────────────────
for /f "delims=" %%T in ('python -c "from datetime import datetime; print(datetime.now().strftime(\"%%Y-%%m-%%d_%%H%%M%%S\"))"') do set "RUN_TS=%%T"

REM ── Defaults do assessment.cfg ───────────────────────────────
set "CFG_PROGRAM="
set "CFG_LABEL="
set "CFG_AID="
for /f "delims=" %%V in ('python -c "from extractor.config import _load_cfg_file; from pathlib import Path; c=_load_cfg_file(Path(\"assessment.cfg\")); print(c.get(\"PROGRAM\",\"\"))" 2^>nul') do set "CFG_PROGRAM=%%V"
for /f "delims=" %%V in ('python -c "from extractor.config import _load_cfg_file; from pathlib import Path; c=_load_cfg_file(Path(\"assessment.cfg\")); print(c.get(\"LABEL\",\"\"))" 2^>nul') do set "CFG_LABEL=%%V"
for /f "delims=" %%V in ('python -c "from extractor.config import _load_cfg_file; from pathlib import Path; c=_load_cfg_file(Path(\"assessment.cfg\")); print(c.get(\"ASSESSMENT_ID\",\"\"))" 2^>nul') do set "CFG_AID=%%V"

REM ── Escrever script PowerShell auxiliar temporario ────────────
set "PS_TEMP=%TEMP%\lw_audit_dialog.ps1"
(
    echo Add-Type -AssemblyName Microsoft.VisualBasic
    echo Add-Type -AssemblyName System.Windows.Forms
    echo function Ask-Text {
    echo     param([string]$msg, [string]$def = "")
    echo     [Microsoft.VisualBasic.Interaction]::InputBox($msg, 'LearnWorlds', $def^)
    echo }
    echo function Ask-YesNo {
    echo     param([string]$msg)
    echo     $r = [System.Windows.Forms.MessageBox]::Show($msg, 'LearnWorlds', 'YesNo', 'Question'^)
    echo     if ($r -eq 'Yes'^) { 'Sim' } else { 'Nao' }
    echo }
    echo function Pick-File {
    echo     param([string]$msg, [string]$initDir)
    echo     $d = New-Object System.Windows.Forms.OpenFileDialog
    echo     $d.Title = $msg
    echo     $d.Filter = 'Excel (*.xlsx;*.xls^)|*.xlsx;*.xls|Todos (*.*^)|*.*'
    echo     if (Test-Path $initDir^) { $d.InitialDirectory = $initDir }
    echo     if ($d.ShowDialog() -eq 'OK'^) { $d.FileName } else { '' }
    echo }
    echo function Pick-Files {
    echo     param([string]$msg, [string]$initDir)
    echo     $d = New-Object System.Windows.Forms.OpenFileDialog
    echo     $d.Title = $msg
    echo     $d.Filter = 'Word (*.docx^)|*.docx|Todos (*.*^)|*.*'
    echo     $d.Multiselect = $true
    echo     if (Test-Path $initDir^) { $d.InitialDirectory = $initDir }
    echo     if ($d.ShowDialog() -eq 'OK'^) { $d.FileNames -join '|' } else { '' }
    echo }
) > "%PS_TEMP%"

REM ── Macro para chamar funcoes PS via dot-sourcing ─────────────
REM   Uso: call :PS_CALL VarName "FunctionCall"
REM   Resultado fica em !VarName!

REM ── [1/6] PROGRAMA ────────────────────────────────────────────
echo [1/6] PROGRAMA
set "PROGRAM="
for /f "delims=" %%P in ('powershell -NoProfile -Command ". \"%PS_TEMP%\"; Ask-Text \"[1/6] PROGRAMA`n`nInsira a sigla do programa em letras minusculas e o numero da edicao.`nExemplo: pggf2\" \"!CFG_PROGRAM!\"" 2^>nul') do set "PROGRAM=%%P"
if "!PROGRAM!"=="" set /p "PROGRAM=[1/6] Programa (ex: pggf2): "
if "!PROGRAM!"=="" ( echo Cancelado. & pause & exit /b 0 )

for /f "delims=" %%S in ('python -c "from extractor.config import slugify as s; print(s(\"!PROGRAM!\"))"') do set "PROGRAM_SLUG=%%S"

REM ── [2/6] ATIVIDADE ───────────────────────────────────────────
echo [2/6] ATIVIDADE
set "LABEL="
for /f "delims=" %%L in ('powershell -NoProfile -Command ". \"%PS_TEMP%\"; Ask-Text \"[2/6] ATIVIDADE`n`nTitulo da atividade (nome da pasta de output).`nExemplo: uc5-fintech\" \"!CFG_LABEL!\"" 2^>nul') do set "LABEL=%%L"
if "!LABEL!"=="" set /p "LABEL=[2/6] Atividade (ex: uc5-fintech): "
if "!LABEL!"=="" ( echo Cancelado. & pause & exit /b 0 )

for /f "delims=" %%S in ('python -c "from extractor.config import slugify as s; print(s(\"!LABEL!\"))"') do set "LABEL_SLUG=%%S"

REM ── [3/6] ASSESSMENT ID ───────────────────────────────────────
echo [3/6] ASSESSMENT ID
set "AID="
for /f "delims=" %%A in ('powershell -NoProfile -Command ". \"%PS_TEMP%\"; Ask-Text \"[3/6] ASSESSMENT ID`n`nID da atividade -- sequencia de 24 caracteres no final`ndo URL da atividade no admin LearnWorlds.`nExemplo: 6a05f692aa02a8f78f0b098d\" \"!CFG_AID!\"" 2^>nul') do set "AID=%%A"
if "!AID!"=="" set /p "AID=[3/6] Assessment ID (24 chars): "
if "!AID!"=="" ( echo Cancelado. & pause & exit /b 0 )

REM ── Pasta de run partilhada ───────────────────────────────────
set "RUN_DIR=%SCRIPT_DIR%\output\!PROGRAM_SLUG!\!LABEL_SLUG!\!RUN_TS!"
echo.
echo   Programa     : !PROGRAM!  -^>  !PROGRAM_SLUG!
echo   Atividade    : !LABEL!  -^>  !LABEL_SLUG!
echo   Assessment ID: !AID!
echo   Run folder   : output\!PROGRAM_SLUG!\!LABEL_SLUG!\!RUN_TS!
echo ------------------------------------------------------------

REM ── [4/6] GABARITO LW ────────────────────────────────────────
echo [4/6] GABARITO LW
set "EXAM_CONFIG_DIR=%SCRIPT_DIR%\input\!PROGRAM_SLUG!\exam_configs"
if not exist "!EXAM_CONFIG_DIR!" mkdir "!EXAM_CONFIG_DIR!"

set "XLSX="
for /f "delims=" %%F in ('powershell -NoProfile -Command ". \"%PS_TEMP%\"; Pick-File \"[4/6] GABARITO LW`n`nSelecione o ficheiro XLSX exportado da atividade no LearnWorlds.`nCourse Outline ^> Atividade ^> Edit questions ^> Export ^> Export as .xls\" \"!EXAM_CONFIG_DIR!\"" 2^>nul') do set "XLSX=%%F"
if "!XLSX!"=="" ( echo Cancelado: o ficheiro XLSX e obrigatorio. & pause & exit /b 0 )

set "DEST_XLSX=!EXAM_CONFIG_DIR!\!LABEL_SLUG!_exam_config.xlsx"
copy "!XLSX!" "!DEST_XLSX!" >nul
echo   Gabarito LW copiado para: input\!PROGRAM_SLUG!\exam_configs\!LABEL_SLUG!_exam_config.xlsx

echo.
echo ^>^> [4/6a] A extrair submissoes (API)...
python -m extractor.run_extract --assessment-id "!AID!" --label "!LABEL!" --run-dir "!RUN_DIR!"
if errorlevel 1 ( echo ERRO nas submissoes (token invalido?). & pause & exit /b 1 )

echo.
echo ^>^> [4/6b] A importar gabarito LearnWorlds...
python -m extractor.run_exam_config --xlsx "!DEST_XLSX!" --assessment-id "!AID!" --label "!LABEL!" --run-dir "!RUN_DIR!"
if errorlevel 1 ( echo ERRO no gabarito LW. & pause & exit /b 1 )

REM ── [5/6] GUIAO WORD ─────────────────────────────────────────
echo.
echo [5/6] GUIAO WORD
set "WORD_REPLY=Nao"
for /f "delims=" %%R in ('powershell -NoProfile -Command ". \"%PS_TEMP%\"; Ask-YesNo \"[5/6] GUIAO WORD`n`nPretende extrair respostas corretas de um guiao de avaliacao em Word?\"" 2^>nul') do set "WORD_REPLY=%%R"

if "!WORD_REPLY!"=="Sim" (
    set "WORD_DOCS_DIR=%SCRIPT_DIR%\input\!PROGRAM_SLUG!\word_docs"
    if not exist "!WORD_DOCS_DIR!" mkdir "!WORD_DOCS_DIR!"

    set "DOCS_RAW="
    for /f "delims=" %%D in ('powershell -NoProfile -Command ". \"%PS_TEMP%\"; Pick-Files \"Selecione um ou mais ficheiros Word (.docx):\" \"!WORD_DOCS_DIR!\"" 2^>nul') do set "DOCS_RAW=%%D"

    if "!DOCS_RAW!"=="" (
        echo   Nenhum ficheiro selecionado -- passo 5 ignorado.
    ) else (
        set "DOC_ARGS="
        for %%D in ("!DOCS_RAW:|=" "!") do (
            set "doc_path=%%~D"
            for %%F in ("!doc_path!") do set "doc_stem=%%~nF"
            set "doc_dest=!WORD_DOCS_DIR!\!LABEL_SLUG!_!doc_stem!.docx"
            copy "!doc_path!" "!doc_dest!" >nul
            echo   Guiao copiado para: input\!PROGRAM_SLUG!\word_docs\!LABEL_SLUG!_!doc_stem!.docx
            set "DOC_ARGS=!DOC_ARGS! "!doc_dest!""
        )
        echo.
        echo ^>^> [5/6] A extrair respostas do guiao Word...
        python tools\extract_answer_key.py --run-dir "!RUN_DIR!" --docs !DOC_ARGS!
        if errorlevel 1 ( echo ERRO na extracao do guiao. & pause & exit /b 1 )
    )
) else (
    echo   Passo 5 ignorado.
)

REM ── [6/6] RECONCILIACAO ──────────────────────────────────────
echo.
echo [6/6] RECONCILIACAO
set "RECONCILE_REPLY=Nao"
for /f "delims=" %%R in ('powershell -NoProfile -Command ". \"%PS_TEMP%\"; Ask-YesNo \"[6/6] RECONCILIACAO`n`nDeseja fazer uma analise de reconciliacao entre o gabarito`ne as respostas dos participantes?\"" 2^>nul') do set "RECONCILE_REPLY=%%R"

if "!RECONCILE_REPLY!"=="Sim" (
    echo.
    echo ^>^> [6/6] A reconciliar...
    python -m reconcile.run_reconcile --run-dir "!RUN_DIR!"
    if errorlevel 1 ( echo ERRO na reconciliacao. & pause & exit /b 1 )

    echo.
    echo ^>^> [6b/6] A gerar interpretacao automatica (IA)...
    python tools\interpret_run.py --run-dir "!RUN_DIR!"
    if errorlevel 1 echo   AVISO: interpretacao nao gerada (OPENAI_API_KEY ausente ou erro de rede).
) else (
    echo   Passo 6 ignorado.
)

REM ── Concluido ─────────────────────────────────────────────────
echo.
echo ============================================================
echo  Concluido.
echo  Output: output\!PROGRAM_SLUG!\!LABEL_SLUG!\!RUN_TS!
echo ============================================================
del "%PS_TEMP%" >nul 2>&1
pause
endlocal
