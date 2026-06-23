# LearnWorlds Grade Auditor

Ferramenta interna da equipa LMS da **Nova SBE Executive Education**.

Extrai dados de avaliações do LearnWorlds (API + export UI + gabaritos Word) e reconcilia-os
de forma determinística para suportar auditorias de notas — sinalizando contradições entre o
que o sistema pontuou e o que o gabarito (LW ou docente) indica como correcto.

**Guião de operação detalhado → [`PROCESSO.md`](PROCESSO.md)**

---

## Como funciona

Quando os alunos fazem um teste no LearnWorlds, as respostas ficam guardadas digitalmente na plataforma. Esta ferramenta faz três coisas:

1. **Extrai** as respostas submetidas pelos alunos (via API), o gabarito configurado no LearnWorlds (via export da UI), e — se existir — o gabarito entregue pelo docente em Word (via LLM).

2. **Reconcilia** as três fontes de forma determinística: compara cada resposta com o gabarito e sinaliza contradições — alunos que responderam correctamente mas ficaram com 0 pontos, respostas aceites pelo sistema mas contraditas pelo gabarito do docente, pontuações inconsistentes para a mesma resposta, entre outros.

3. **Interpreta** os resultados via LLM e produz um relatório em linguagem natural, em Português, com os problemas encontrados por ordem de prioridade e os próximos passos sugeridos.

O sistema nunca decide nada — cabe sempre a um humano verificar os casos sinalizados e decidir se há algo a corrigir.

---

## Como usar

### 1. Configuração inicial (uma vez)

**a) Preencher o `.env`** — copiar `.env.example` para `.env` e preencher:

```
LEARNWORLDS_API_URL=https://online.executiveducation.novasbe.pt/admin/api
LEARNWORLDS_SCHOOL_ID=<school id>
LEARNWORLDS_ACCESS_TOKEN=<token válido>
OPENAI_API_KEY=<chave OpenAI — necessária para gabaritos Word e interpretação>
OPENAI_MODEL=gpt-4o
```

**b) Preencher o `assessment.cfg`** — ficheiro na raiz do projeto, para o assessment em curso:

```
PROGRAM=pggf2
LABEL=uc5-fintech
LABEL_DISPLAY=UC5: Fintech e Inovação Financeira
ASSESSMENT_ID=000000000000000000000000
EPOCA=Normal
```

### 2. Lançar

Fazer duplo-clique em **`run_audit_gui.py`** (ou correr `python run_audit_gui.py`).

Abre uma janela com duas abas: **Nova Run** (pipeline completo) e **Re-correr** (reconciliar / interpretar uma run existente).

> Os launchers de terminal `run_audit.command` (macOS) e `run_audit.bat` (Windows) continuam disponíveis para quem preferir linha de comando.

---

## O que a GUI faz

### Aba "Nova Run"

Formulário pré-preenchido com os valores do `assessment.cfg`. Os 4 passos activam-se em sequência:

| Passo | Acção | O que corre |
|-------|-------|-------------|
| **[1/4]** Gabarito LW | Selecionar o XLSX exportado da UI | Extração de submissões (API) + importação do gabarito (XLSX) |
| **[2/4]** Word docs *(opcional)* | Selecionar 1 ou mais `.docx` | Extração de respostas via OpenAI (Fase 1: perguntas com opções LW → `manual_answer_key.csv`; Fase 2: lacunas/correspondência → `inferred_answer_key.csv`) |
| **[3/4]** Reconciliar | Botão | `run_reconcile` — sem API, sem LLM |
| **[4/4]** Interpretar | Botão | `interpret_run` — gera `audit_interpretation.md` via OpenAI |

### Aba "Re-correr"

Seleccionar uma pasta de run existente e correr só Reconciliar e/ou Interpretar — útil após corrigir manualmente um `inferred_answer_key.csv` ou um `manual_answer_key.csv`.

---

## O que cada ficheiro é

### Configuração

| Ficheiro | O que é |
|----------|---------|
| `assessment.cfg` | Assessment em curso: programa, label, ID, época. **Sem credenciais.** Actualizar antes de cada avaliação. |
| `.env` | Credenciais da API LearnWorlds e chave OpenAI. **Nunca partilhar.** Não está no git. |
| `.env.example` | Modelo do `.env` com placeholders. |
| `requirements.txt` | Dependências Python. |

### Launchers / interface

| Ficheiro | O que é |
|----------|---------|
| `run_audit_gui.py` | Interface gráfica principal (tkinter). Duplo-clique para abrir. |
| `run_audit.command` | Lançador alternativo macOS (terminal, diálogos osascript). |
| `run_audit.bat` | Lançador alternativo Windows. |

### Código

| Pasta / ficheiro | O que é |
|------------------|---------|
| `extractor/` | Extractores de dados (API + XLSX). |
| `extractor/run_extract.py` | Submissões via API (`GET /v2/assessments/{id}/responses`). |
| `extractor/run_exam_config.py` | Gabarito LW a partir do XLSX exportado da UI. |
| `extractor/run_grades.py` | Notas oficiais do curso via API (uso avançado). |
| `extractor/config.py` | Lê `assessment.cfg` + `.env`. |
| `reconcile/` | Reconciliador determinístico (sem I/O de rede). |
| `reconcile/run_reconcile.py` | Junta submissões + gabaritos, aplica regras, gera relatórios. |
| `reconcile/core.py` | Lógica de negócio: `check_answer()`, `join_key()`, flags. |
| `tools/extract_answer_key.py` | Extrai respostas de ficheiros Word via OpenAI → `manual_answer_key.csv` + `inferred_answer_key.csv`. |
| `tools/interpret_run.py` | Gera `audit_interpretation.md` — interpretação em Português via OpenAI. |

### Dados (não estão no git)

| Pasta | O que é |
|-------|---------|
| `input/<programa>/exam_configs/` | Cópias dos XLSX exportados da UI. |
| `input/<programa>/word_docs/` | Cópias dos guiões Word dos docentes. |
| `output/<programa>/<label>/<timestamp>/` | Resultados de uma corrida. Nunca sobrescritos. |

---

## Estrutura de output

```
output/
  <programa>/                         ex: pggf2
    <label>/                          ex: uc5-fintech
      <YYYY-MM-DD_HHmmss>/            uma pasta por corrida, nunca sobrescrita
        submissions/
          raw/
            raw_response.json         resposta crua da API
            extraction_report.json    contagens e avisos
          submissions_export.csv      1 linha por bloco de resposta por aluno
          submissions_export.xlsx
        exam_config/
          raw/
            raw_exam_config.json
            extraction_report.json
          exam_config_as_is.csv       gabarito LW: respostas corretas + opções
          exam_config_as_is.xlsx
        answer_key/                   só se o passo Word foi corrido
          manual_answer_key.csv       gabarito docente (perguntas com opções LW)
          inferred_answer_key.csv     respostas inferidas (lacunas / correspondência)
        question_index.csv            índice unificado de todas as perguntas activas
        reconcile/                    só se a reconciliação foi corrida
          reconciliation_report/
            reconciliation_report.csv     1 linha por (aluno × pergunta)
            reconciliation_report.xlsx
          grade_reconciliation/
            grade_reconciliation.csv      nota oficial vs recalculada por aluno
            grade_reconciliation.xlsx
          consistency_report/
            consistency_report.csv        mesma resposta pontuada diferente entre alunos
            consistency_report.xlsx
          manual_review_queue/
            manual_review_queue.csv       perguntas que precisam de revisão humana
            manual_review_queue.xlsx
          reconciliation_summary.json
          reconciliation_summary.md       sumário legível com contagens e flags
        audit_interpretation.md           interpretação IA em Português
```

---

## Flags de reconciliação

| Flag | Significado |
|------|-------------|
| `answer_correct_per_doc_but_zero` | ⚠️ Resposta correcta segundo o gabarito do docente, mas LW deu 0 — erro de parametrização no LW |
| `answer_accepted_but_zero` | Resposta aceite pelo gabarito LW, mas 0 pontos atribuídos |
| `answer_not_accepted_but_full` | Resposta não aceite pelo gabarito, mas nota máxima atribuída |
| `mcma_wrong_not_penalized` | MCMA: aluno seleccionou todas as opções correctas + opções erradas e recebeu nota máxima — o LW não penalizou as erradas |
| `answer_accepted_but_partial` | Crédito parcial configurado (informativo) |
| `inconsistent_scoring` | Mesma pergunta + mesma resposta → pontos diferentes entre alunos |

---

## Segurança

- **`.env`** — credenciais sensíveis. Nunca commitar, nunca partilhar fora da equipa LMS.
- **`output/`** — dados pessoais de alunos. Manter na pasta restrita OneDrive. Não criar links públicos.
- **`input/`** — cópias dos gabaritos. Mesmas restrições.
- Todos estão no `.gitignore`. O `assessment.cfg` é o único ficheiro de configuração que vai para o git.

---

## Troubleshooting rápido

| Sintoma | Solução |
|---------|---------|
| `HTTP 401/403` | Token LW expirado — colar novo token em `.env` |
| `HTTP 404 Unit not found` | `ASSESSMENT_ID` errado em `assessment.cfg` |
| `No .env file found` | Copiar `.env.example` para `.env` e preencher |
| `OPENAI_API_KEY not set` | Adicionar `OPENAI_API_KEY=sk-...` ao `.env` |
| `429 insufficient_quota` | Conta OpenAI sem créditos — recarregar em platform.openai.com |
| macOS bloqueia o launcher | `chmod +x run_audit.command` no Terminal |
| Tracebacks completos | Adicionar `DEBUG=true` ao `.env` |
