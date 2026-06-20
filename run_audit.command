#!/bin/bash
# macOS unified launcher for the LearnWorlds audit pipeline.
# Double-click this file. No credentials here — read from .env.
# If macOS blocks it, run once:  chmod +x run_audit.command

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || { echo "ERRO: não consegui entrar na pasta."; read -rn 1 -s; exit 1; }

echo "============================================================"
echo " LearnWorlds — Lançador de Auditoria (macOS)"
echo " Pasta: $SCRIPT_DIR"
echo "============================================================"
echo

# ── Python + venv ──────────────────────────────────────────────
if command -v python3 >/dev/null 2>&1; then PYTHON="python3"; else PYTHON="python"; fi
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    echo "A criar o ambiente (.venv)..."
    "$PYTHON" -m venv "$SCRIPT_DIR/.venv" || { echo "ERRO ao criar .venv"; read -rn 1 -s; exit 1; }
fi
# shellcheck disable=SC1091
source "$SCRIPT_DIR/.venv/bin/activate"
echo "A instalar/atualizar dependências..."
python -m pip install --upgrade pip -q 2>/dev/null
python -m pip install -r "$SCRIPT_DIR/requirements.txt" -q \
    || { echo "ERRO ao instalar dependências"; read -rn 1 -s; exit 1; }

if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "AVISO: não existe .env. Copia .env.example para .env e adiciona um token válido."
fi

# ── Timestamp partilhado (mesmo para todos os passos) ──────────
RUN_TS="$(date '+%Y-%m-%d_%H%M%S')"

# ── slugify via Python (consistente com o pipeline) ────────────
slugify_py() {
    python -c "from extractor.config import slugify as _s; print(_s('$1'))"
}

# ── Defaults do assessment.cfg ─────────────────────────────────
CFG_PROGRAM="$(python -c "
from extractor.config import _load_cfg_file
from pathlib import Path
c = _load_cfg_file(Path('assessment.cfg'))
print(c.get('PROGRAM', ''))
" 2>/dev/null)"
CFG_LABEL="$(python -c "
from extractor.config import _load_cfg_file
from pathlib import Path
c = _load_cfg_file(Path('assessment.cfg'))
print(c.get('LABEL', ''))
" 2>/dev/null)"
CFG_AID="$(python -c "
from extractor.config import _load_cfg_file
from pathlib import Path
c = _load_cfg_file(Path('assessment.cfg'))
print(c.get('ASSESSMENT_ID', ''))
" 2>/dev/null)"

# ── [1/6] PROGRAMA ─────────────────────────────────────────────
echo "[1/6] PROGRAMA"
PROGRAM="$(osascript <<OSA 2>/dev/null
try
    text returned of (display dialog "[1/6] PROGRAMA" & return & return & ¬
        "Insira a sigla do programa em letras minúsculas e o número da edição." & return & ¬
        "Exemplo: pggf2" ¬
        default answer "$CFG_PROGRAM" buttons {"Cancelar", "OK"} default button "OK")
on error
    return ""
end try
OSA
)"
if [ -z "$PROGRAM" ]; then
    echo "Cancelado."; read -rn 1 -s -p "Prima uma tecla para fechar..."; exit 0
fi
PROGRAM_SLUG="$(slugify_py "$PROGRAM")"

# ── [2/6] ATIVIDADE ────────────────────────────────────────────
echo "[2/6] ATIVIDADE"
LABEL="$(osascript <<OSA 2>/dev/null
try
    text returned of (display dialog "[2/6] ATIVIDADE" & return & return & ¬
        "Título da atividade (usado como nome da pasta de output)." & return & ¬
        "Exemplo: uc5-fintech" ¬
        default answer "$CFG_LABEL" buttons {"Cancelar", "OK"} default button "OK")
on error
    return ""
end try
OSA
)"
if [ -z "$LABEL" ]; then
    echo "Cancelado."; read -rn 1 -s -p "Prima uma tecla para fechar..."; exit 0
fi
LABEL_SLUG="$(slugify_py "$LABEL")"

# ── [3/6] ASSESSMENT ID ────────────────────────────────────────
echo "[3/6] ASSESSMENT ID"
AID="$(osascript <<OSA 2>/dev/null
try
    text returned of (display dialog "[3/6] ASSESSMENT ID" & return & return & ¬
        "ID da atividade — sequência de 24 caracteres no final do URL" & return & ¬
        "da atividade no admin LearnWorlds." & return & ¬
        "Exemplo: 6a05f692aa02a8f78f0b098d" ¬
        default answer "$CFG_AID" buttons {"Cancelar", "OK"} default button "OK")
on error
    return ""
end try
OSA
)"
if [ -z "$AID" ]; then
    echo "Cancelado."; read -rn 1 -s -p "Prima uma tecla para fechar..."; exit 0
fi

# ── Pasta de run partilhada ────────────────────────────────────
RUN_DIR="$SCRIPT_DIR/output/$PROGRAM_SLUG/$LABEL_SLUG/$RUN_TS"
echo
echo "  Programa     : $PROGRAM  →  $PROGRAM_SLUG"
echo "  Atividade    : $LABEL  →  $LABEL_SLUG"
echo "  Assessment ID: $AID"
echo "  Run folder   : output/$PROGRAM_SLUG/$LABEL_SLUG/$RUN_TS"
echo "------------------------------------------------------------"

# ── [4/6] GABARITO LW ─────────────────────────────────────────
echo "[4/6] GABARITO LW"
EXAM_CONFIG_DIR="$SCRIPT_DIR/input/$PROGRAM_SLUG/exam_configs"
mkdir -p "$EXAM_CONFIG_DIR"

XLSX="$(osascript <<OSA 2>/dev/null
try
    set f to choose file with prompt ¬
        "[4/6] GABARITO LW" & return & return & ¬
        "Selecione o ficheiro XLSX exportado da atividade no LearnWorlds." & return & ¬
        "Course Outline > Atividade > Edit questions > Export > Export as .xls" ¬
        default location POSIX file "$EXAM_CONFIG_DIR"
    POSIX path of f
on error
    return ""
end try
OSA
)"
if [ -z "$XLSX" ]; then
    echo "Cancelado: o ficheiro XLSX é obrigatório."
    read -rn 1 -s -p "Prima uma tecla para fechar..."; exit 0
fi

DEST_XLSX="$EXAM_CONFIG_DIR/${LABEL_SLUG}_exam_config.xlsx"
cp "$XLSX" "$DEST_XLSX"
echo "  Gabarito LW copiado para: input/$PROGRAM_SLUG/exam_configs/${LABEL_SLUG}_exam_config.xlsx"

echo
echo ">> [4/6a] A extrair submissões (API)..."
python -m extractor.run_extract \
    --assessment-id "$AID" \
    --label "$LABEL" \
    --run-dir "$RUN_DIR" \
    || { echo "ERRO nas submissões (token inválido?)."; read -rn 1 -s -p "Prima uma tecla para fechar..."; exit 1; }

echo
echo ">> [4/6b] A importar gabarito LearnWorlds..."
python -m extractor.run_exam_config \
    --xlsx "$DEST_XLSX" \
    --assessment-id "$AID" \
    --label "$LABEL" \
    --run-dir "$RUN_DIR" \
    || { echo "ERRO no gabarito LW."; read -rn 1 -s -p "Prima uma tecla para fechar..."; exit 1; }

# ── [5/6] GUIÃO WORD ──────────────────────────────────────────
echo
echo "[5/6] GUIÃO WORD"
WORD_REPLY="$(osascript <<'OSA' 2>/dev/null
try
    button returned of (display dialog ¬
        "[5/6] GUIÃO WORD" & return & return & ¬
        "Pretende extrair respostas corretas de um guião de avaliação em Word?" ¬
        buttons {"Não", "Sim"} default button "Sim")
on error
    return "Não"
end try
OSA
)"

if [ "$WORD_REPLY" = "Sim" ]; then
    WORD_DOCS_DIR="$SCRIPT_DIR/input/$PROGRAM_SLUG/word_docs"
    mkdir -p "$WORD_DOCS_DIR"

    DOCS_RAW="$(osascript <<OSA 2>/dev/null
try
    set fileList to choose file with prompt ¬
        "Selecione um ou mais ficheiros Word (.docx):" ¬
        default location POSIX file "$WORD_DOCS_DIR" ¬
        with multiple selections allowed
    set pathList to ""
    repeat with f in fileList
        set pathList to pathList & POSIX path of f & linefeed
    end repeat
    return pathList
on error
    return ""
end try
OSA
)"

    if [ -z "$DOCS_RAW" ]; then
        echo "  Nenhum ficheiro selecionado — passo 5 ignorado."
    else
        DOC_ARGS=()
        while IFS= read -r doc; do
            [ -z "$doc" ] && continue
            stem="$(basename "$doc" .docx)"
            dest="$WORD_DOCS_DIR/${LABEL_SLUG}_${stem}.docx"
            cp "$doc" "$dest"
            DOC_ARGS+=("$dest")
            echo "  Guião copiado para: input/$PROGRAM_SLUG/word_docs/${LABEL_SLUG}_${stem}.docx"
        done <<< "$DOCS_RAW"

        if [ "${#DOC_ARGS[@]}" -gt 0 ]; then
            echo
            echo ">> [5/6] A extrair respostas do guião Word..."
            python tools/extract_answer_key.py \
                --run-dir "$RUN_DIR" \
                --docs "${DOC_ARGS[@]}" \
                || { echo "ERRO na extração do guião."; read -rn 1 -s -p "Prima uma tecla para fechar..."; exit 1; }
        fi
    fi
else
    echo "  Passo 5 ignorado."
fi

# ── [6/6] RECONCILIAÇÃO ───────────────────────────────────────
echo
echo "[6/6] RECONCILIAÇÃO"
RECONCILE_REPLY="$(osascript <<'OSA' 2>/dev/null
try
    button returned of (display dialog ¬
        "[6/6] RECONCILIAÇÃO" & return & return & ¬
        "Deseja fazer uma análise de reconciliação entre o gabarito" & return & ¬
        "e as respostas dos participantes?" ¬
        buttons {"Não", "Sim"} default button "Sim")
on error
    return "Não"
end try
OSA
)"

if [ "$RECONCILE_REPLY" = "Sim" ]; then
    echo
    echo ">> [6/6] A reconciliar..."
    python -m reconcile.run_reconcile \
        --run-dir "$RUN_DIR" \
        || { echo "ERRO na reconciliação."; read -rn 1 -s -p "Prima uma tecla para fechar..."; exit 1; }
else
    echo "  Passo 6 ignorado."
fi

# ── Concluído ──────────────────────────────────────────────────
echo
echo "============================================================"
echo " Concluído."
echo " Output: output/$PROGRAM_SLUG/$LABEL_SLUG/$RUN_TS"
echo "============================================================"
echo
read -rn 1 -s -p "Prima uma tecla para fechar..."
