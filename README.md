# LearnWorlds Assessment Exporter & Auditor

Ferramenta interna da equipa LMS da **Nova SBE Executive Education**.

Extrai dados de avaliações do LearnWorlds (API + export UI + gabaritos Word) e reconcilia-os
de forma determinística para suportar auditorias de notas.

**Guião de operação detalhado → [`PROCESSO.md`](PROCESSO.md)**

---

## Como usar (modo normal)

### 1. Configuração inicial (uma vez)

**a) Preencher o `.env`** — copiar `.env.example` para `.env` e preencher:

```
LEARNWORLDS_API_URL=https://online.executiveducation.novasbe.pt/admin/api
LEARNWORLDS_SCHOOL_ID=<school id>
LEARNWORLDS_ACCESS_TOKEN=<token válido>
OPENAI_API_KEY=<chave OpenAI — só necessária para gabaritos Word>
OPENAI_MODEL=gpt-4o
```

**b) Preencher o `assessment.cfg`** — ficheiro na raiz do projeto, para o assessment em curso:

```
PROGRAM=pggf2
LABEL=uc5-fintech
ASSESSMENT_ID=6a05f692aa02a8f78f0b098d
```

### 2. Lançar (macOS)

Fazer duplo-clique em **`run_audit.command`**.

> Primeira vez: correr uma vez no Terminal `chmod +x run_audit.command`

### 2. Lançar (Windows)

Fazer duplo-clique em **`run_audit.bat`**.

---

## O que o lançador faz (6 passos)

O lançador abre diálogos gráficos em sequência. Os campos 1–3 vêm **pré-preenchidos** com
os valores do `assessment.cfg` — basta confirmar com OK se estiverem corretos.

| Passo | Diálogo | O que acontece |
|-------|---------|----------------|
| **[1/6]** | Programa | Sigla + edição (ex: `pggf2`) |
| **[2/6]** | Atividade | Título/label (ex: `uc5-fintech`) |
| **[3/6]** | Assessment ID | ID de 24 chars do URL do admin LW |
| **[4/6]** | Gabarito LW | Selecionar o XLSX exportado da UI → copia+renomeia para `input/` → corre submissões (API) + importação do gabarito |
| **[5/6]** | Guião Word | Sim/Não → se Sim: selecionar 1 ou mais .docx (Cmd+clique para múltiplos) → copia+renomeia para `input/` → extrai respostas via OpenAI |
| **[6/6]** | Reconciliação | Sim/Não → se Sim: reconcilia submissões com gabarito e gera relatórios |

---

## O que cada ficheiro é

### Ficheiros de configuração

| Ficheiro | O que é |
|----------|---------|
| `assessment.cfg` | Configuração do assessment em curso: programa, label, assessment ID. **Não tem credenciais.** Actualizar antes de cada avaliação. |
| `.env` | Credenciais da API LearnWorlds e chave OpenAI. **Nunca partilhar.** Não está no git. |
| `.env.example` | Modelo do `.env` com placeholders. Sem valores reais. |
| `requirements.txt` | Lista de dependências Python. |

### Launchers

| Ficheiro | O que é |
|----------|---------|
| `run_audit.command` | Lançador macOS — duplo-clique para correr o pipeline completo (6 passos). |
| `run_audit.bat` | Lançador Windows — duplo-clique para correr o pipeline completo (6 passos). |

### Código

| Pasta / ficheiro | O que é |
|------------------|---------|
| `extractor/` | Pacote Python com os extractores de dados da API e do export UI. |
| `extractor/run_extract.py` | Extrai as submissões dos alunos via API (`GET /v2/assessments/{id}/responses`). |
| `extractor/run_exam_config.py` | Importa o gabarito LW a partir do XLSX exportado manualmente da UI. |
| `extractor/run_grades.py` | Extrai notas oficiais do curso via API (uso avançado, não incluído no launcher). |
| `extractor/config.py` | Carrega configuração de `assessment.cfg` + `.env`, define `resolve_step_dir()`. |
| `reconcile/` | Pacote Python com o reconciliador determinístico. |
| `reconcile/run_reconcile.py` | Junta submissões + gabarito, aplica regras de auditoria, gera relatórios. |
| `reconcile/core.py` | Lógica de negócio: `check_answer()`, `reconcile_grade()`, `join_key()`, `norm()`. |
| `tools/extract_answer_key.py` | Extrai respostas corretas de ficheiros Word via OpenAI e cruza com o gabarito LW. |

### Pastas de dados (não estão no git)

| Pasta | O que é |
|-------|---------|
| `input/<programa>/exam_configs/` | Cópias dos XLSX exportados da UI, renomeados como `<label>_exam_config.xlsx`. Criada automaticamente pelo launcher. |
| `input/<programa>/word_docs/` | Cópias dos guiões Word dos docentes, renomeados como `<label>_<nome_original>.docx`. Criada automaticamente. |
| `output/<programa>/<label>/<timestamp>/` | Resultados de uma corrida. Nunca sobrescritos. Ver estrutura abaixo. |

---

## Estrutura de output

```
output/
  <programa>/                         ex: pggf2
    <label>/                          ex: uc5-fintech
      <YYYY-MM-DD_HHmmss>/            uma pasta por corrida, nunca sobrescrita
        submissions/
          raw/
            raw_response.json         resposta crua da API, todas as páginas
            extraction_report.json    contagens e avisos da extração
          submissions_export.csv      1 linha por bloco de resposta por aluno
          submissions_export.xlsx
        exam_config/
          raw/
            raw_exam_config.json
            extraction_report.json
          exam_config_as_is.csv       gabarito LW: respostas corretas + opções
          exam_config_as_is.xlsx
        answer_key/                   só se o Passo 5 (Word) foi corrido
          manual_answer_key.csv       gabarito docente extraído via LLM
        reconcile/                    só se o Passo 6 foi corrido
          reconciliation_report/
            reconciliation_report.csv     1 linha por (aluno × pergunta)
            reconciliation_report.xlsx
          grade_reconciliation/
            grade_reconciliation.csv      nota oficial vs recalculada, por aluno
            grade_reconciliation.xlsx
          consistency_report/
            consistency_report.csv        mesma resposta pontuada diferente entre alunos
            consistency_report.xlsx
          manual_review_queue/
            manual_review_queue.csv       perguntas sem gabarito ou ambíguas
            manual_review_queue.xlsx
          reconciliation_summary.json
          reconciliation_summary.md       sumário legível com contagens e flags
```

---

## Segurança

- **`.env`** — credenciais sensíveis. Nunca commitar, nunca partilhar fora da equipa LMS.
- **`output/`** — dados pessoais de alunos e resultados de avaliação. Manter na pasta restrita OneDrive. Não criar links públicos. Não enviar por email.
- **`input/`** — cópias dos gabaritos (contêm as respostas corretas). Mesmas restrições.
- Todos estão no `.gitignore`. O `assessment.cfg` é o único ficheiro de configuração que vai para o git (não tem credenciais).

---

## Troubleshooting rápido

| Sintoma | Solução |
|---------|---------|
| `Authentication failed (HTTP 401/403)` | Token LW inválido/expirado — colar novo token em `.env` |
| `No .env file found` | Copiar `.env.example` para `.env` e preencher |
| `OPENAI_API_KEY not set` | Adicionar `OPENAI_API_KEY=sk-...` ao `.env` |
| macOS bloqueia o launcher | Correr uma vez: `chmod +x run_audit.command` |
| Tracebacks completos | Adicionar `DEBUG=true` ao `.env` |
| `submissions_export.csv` não encontrado na reconciliação | Correr primeiro o Passo 4 (gabarito LW) com o mesmo `--run-dir` |
