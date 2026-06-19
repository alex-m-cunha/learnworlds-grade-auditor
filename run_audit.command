#!/bin/bash
# macOS guided launcher for the full audit pipeline.
# Double-click. It asks for the assessment id, lets you PICK the exam-config XLSX
# (defaults to the input/ folder), and runs:
#   submissions (API)  ->  exam-config (XLSX)  ->  reconcile
#
# No credentials here — everything is read from .env. If macOS blocks it, run once:
#   chmod +x run_audit.command

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || { echo "ERRO: não consegui entrar na pasta."; read -n 1 -s; exit 1; }

echo "============================================================"
echo " LearnWorlds — Auditoria de Avaliação (macOS)"
echo " Pasta: $SCRIPT_DIR"
echo "============================================================"

# --- Python + venv -----------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then PYTHON="python3"; else PYTHON="python"; fi
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    echo "A criar o ambiente (.venv)..."
    "$PYTHON" -m venv "$SCRIPT_DIR/.venv" || { echo "ERRO ao criar .venv"; read -n 1 -s; exit 1; }
fi
# shellcheck disable=SC1091
source "$SCRIPT_DIR/.venv/bin/activate"
echo "A instalar/atualizar dependências..."
python -m pip install --upgrade pip >/dev/null 2>&1
python -m pip install -r "$SCRIPT_DIR/requirements.txt" >/dev/null 2>&1 \
    || { echo "ERRO ao instalar dependências"; read -n 1 -s; exit 1; }

if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "AVISO: não existe .env. Copia .env.example para .env e mete um token válido."
fi

# --- 1) Escolher o XLSX (gabarito) -------------------------------------------
echo
echo "Escolhe o ficheiro de configuração do teste (XLSX exportado da LearnWorlds)..."
XLSX=$(osascript <<OSA 2>/dev/null
set defLoc to POSIX file "$SCRIPT_DIR/input"
try
    set f to choose file with prompt "Escolhe o XLSX de configuração do teste (ou Cancelar para só submissões):" default location defLoc
    POSIX path of f
on error
    return ""
end try
OSA
)

# --- 2) Assessment ID --------------------------------------------------------
AID=$(osascript <<'OSA' 2>/dev/null
try
    text returned of (display dialog "Assessment ID do teste (da LearnWorlds):" default answer "" buttons {"Cancelar","OK"} default button "OK")
on error
    return ""
end try
OSA
)
if [ -z "$AID" ]; then
    echo "Cancelado: é preciso o Assessment ID."
    read -n 1 -s -p "Carrega numa tecla para fechar..."; exit 1
fi

# --- 3) Título (pasta de output); por defeito derivado do nome do XLSX -------
DEF_LABEL=""
if [ -n "$XLSX" ]; then
    base=$(basename "$XLSX"); base="${base%.xlsx}"
    DEF_LABEL=$(printf '%s' "$base" | sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2}[_-]//')
fi
LABEL=$(osascript <<OSA 2>/dev/null
try
    text returned of (display dialog "Título (nome da pasta de output):" default answer "$DEF_LABEL" buttons {"Cancelar","OK"} default button "OK")
on error
    return ""
end try
OSA
)
[ -z "$LABEL" ] && LABEL="$DEF_LABEL"
if [ -z "$LABEL" ]; then
    echo "Cancelado: é preciso um título."
    read -n 1 -s -p "Carrega numa tecla para fechar..."; exit 1
fi

echo
echo "Assessment : $AID"
echo "Título     : $LABEL"
echo "XLSX       : ${XLSX:-(nenhum — só submissões)}"
echo "------------------------------------------------------------"

# --- Pipeline ----------------------------------------------------------------
echo ">> Submissões..."
python -m extractor.run_extract --assessment-id "$AID" --label "$LABEL" \
    || { echo "ERRO nas submissões (token inválido?)."; read -n 1 -s; exit 1; }

if [ -n "$XLSX" ]; then
    echo
    echo ">> Configuração do teste (gabarito)..."
    python -m extractor.run_exam_config --xlsx "$XLSX" --assessment-id "$AID" --label "$LABEL" \
        || { echo "ERRO no exam-config."; read -n 1 -s; exit 1; }
    echo
    echo ">> Reconciliação..."
    python -m reconcile.run_reconcile --label "$LABEL" \
        || { echo "ERRO na reconciliação."; read -n 1 -s; exit 1; }
else
    echo "(sem XLSX — saltado o gabarito e a reconciliação)"
fi

echo "------------------------------------------------------------"
echo "Concluído. Vê os caminhos exatos acima (pasta 'output/')."
echo
read -n 1 -s -p "Carrega numa tecla para fechar..."
