# Processo de auditoria de avaliações — end-to-end

Guião completo para, a partir do LearnWorlds, **extrair** os dados das avaliações e
**reconciliar** as fontes de forma determinística, produzindo relatórios auditáveis.

> **Âmbito:** este projeto faz (1) a **extração** (API → CSV / export UI → CSV / Word → CSV via LLM)
> e (2) uma **reconciliação determinística** que junta as fontes e **assinala** discrepâncias com
> regras exatas e auditáveis. Fica **fora de âmbito**: qualquer juízo semântico/pedagógico ou
> "corrigir" dados. A reconciliação **não decide** se uma pergunta está mal — apenas levanta o caso
> para revisão humana.

---

## Pré-requisitos (configuração inicial — uma vez)

### 1. Python 3.10+

Verifica com `python3 --version`. Instalar em [python.org](https://www.python.org) se necessário.

O ambiente virtual (`.venv`) e as dependências são instalados **automaticamente** pelo lançador
quando é corrido pela primeira vez. Não é necessário fazer nada manualmente.

### 2. Ficheiro `.env`

Copiar `.env.example` para `.env` (na raiz do projeto) e preencher:

```
LEARNWORLDS_API_URL=https://online.executiveducation.novasbe.pt/admin/api
LEARNWORLDS_SCHOOL_ID=<school id da Nova Fórum>
LEARNWORLDS_ACCESS_TOKEN=<token de acesso LearnWorlds — ver abaixo>
OPENAI_API_KEY=<chave da API OpenAI — só necessária para gabaritos Word>
OPENAI_MODEL=gpt-4o
```

> **Token LearnWorlds:** o token é gerado manualmente no admin LW e tem validade limitada.
> Esta ferramenta **nunca** cria nem renova tokens automaticamente. Token expirado → erro
> HTTP 401 claro. Colar o novo token no `.env` e correr de novo.

> **Segurança:** o `.env` tem credenciais sensíveis. Está no `.gitignore` e nunca deve ser
> partilhado fora da equipa LMS nem enviado por email.

### 3. Ficheiro `assessment.cfg`

Ficheiro na raiz do projeto, já incluído no repositório. Actualizar antes de cada avaliação:

```
PROGRAM=pggf2          ← sigla do programa + edição (ex: pggf2, mba3)
LABEL=uc5-fintech      ← título da atividade (usado como nome da pasta de output)
ASSESSMENT_ID=6a05f692aa02a8f78f0b098d   ← ID de 24 chars do URL do admin LW
```

O `ASSESSMENT_ID` encontra-se no final do URL ao abrir a atividade no admin LearnWorlds:
`https://.../admin/assessments/**6a05f692aa02a8f78f0b098d**/edit`

> O `assessment.cfg` não tem credenciais — pode ir para o git e ser partilhado.

### 4. Primeira vez no macOS

```bash
chmod +x run_audit.command
```

---

## Como correr — modo normal (lançador gráfico)

**macOS:** duplo-clique em `run_audit.command`
**Windows:** duplo-clique em `run_audit.bat`

O lançador abre 6 diálogos gráficos em sequência. Os campos 1–3 vêm **pré-preenchidos**
com os valores do `assessment.cfg` — confirmar com OK ou corrigir se for uma atividade diferente.

---

## Os 6 passos do lançador

### [1/6] PROGRAMA

Diálogo pré-preenchido com `PROGRAM` do `assessment.cfg`.

Exemplo: `pggf2`

Usado para organizar os outputs em `output/pggf2/` e os inputs em `input/pggf2/`.

---

### [2/6] ATIVIDADE

Diálogo pré-preenchido com `LABEL` do `assessment.cfg`.

Exemplo: `uc5-fintech`

Usado como nome da subpasta: `output/pggf2/uc5-fintech/<timestamp>/`.

---

### [3/6] ASSESSMENT ID

Diálogo pré-preenchido com `ASSESSMENT_ID` do `assessment.cfg`.

Sequência de 24 caracteres no final do URL da atividade no admin LearnWorlds.

---

### [4/6] GABARITO LW

Abre um selector de ficheiro para escolher o **XLSX exportado da UI do LearnWorlds**.

**Como exportar o XLSX:**
1. Admin LearnWorlds → curso → **Course Outline**
2. Clicar na atividade (assessment) → **Edit questions**
3. Botão **Export** → **Export as .xls**
4. Guardar o ficheiro (normalmente vai para Downloads)

Depois de selecionar o ficheiro no diálogo:
- O lançador **copia e renomeia** o XLSX para `input/<programa>/exam_configs/<label>_exam_config.xlsx`
  (o ficheiro original fica intacto onde estava)
- Corre automaticamente:
  - **Extração de submissões** — `GET /v2/assessments/{id}/responses` → `submissions_export.csv`
  - **Importação do gabarito** — XLSX → `exam_config_as_is.csv`

> ⚠️ Exportar o XLSX da **mesma versão** do teste que os alunos fizeram. Se o teste foi editado
> depois das submissões, os enunciados podem não coincidir e a reconciliação perde matches.

---

### [5/6] GUIÃO WORD *(opcional)*

Diálogo Sim/Não: "Pretende extrair respostas corretas de um guião de avaliação em Word?"

**Se Sim:** abre um selector de ficheiro para escolher os ficheiros `.docx` dos docentes.

- Selecionar **1 ou mais ficheiros** ao mesmo tempo: **Cmd+clique** (macOS) ou **Ctrl+clique** (Windows)
- Exemplo: selecionar os 3 guiões de um teste com 3 UCs (15+15+15 perguntas) de uma vez

Depois de selecionar:
- O lançador **copia e renomeia** cada ficheiro para `input/<programa>/word_docs/<label>_<nome_original>.docx`
- Corre **`tools/extract_answer_key.py`** via OpenAI (requer `OPENAI_API_KEY` no `.env`)
- Produz `answer_key/manual_answer_key.csv` com:
  - `lw_correct_answer` (do gabarito LW) vs `doc_correct_answer` (extraído do Word)
  - `answers_match` (`yes / no / lw_only / doc_only`)
  - `confidence` (`high / medium / low / unmatched`)
  - `needs_review` — `true` se houver discrepância ou baixa confiança

**Tipos de pergunta incluídos:** TMC, TMCMA, TTF, TD, TST
**Excluídos:** TP, TFU (correção manual — não têm resposta certa a extrair)

> O LLM reconhece respostas marcadas por: negrito, estilo Word, asterisco, checkmark, cor,
> sublinhado, etiqueta explícita ("Resposta: B)"), tabela, ou riscado nas erradas.

**Se Não:** passo ignorado, avança para o passo 6.

---

### [6/6] RECONCILIAÇÃO *(opcional)*

Diálogo Sim/Não: "Deseja fazer uma análise de reconciliação entre o gabarito e as respostas
dos participantes?"

**Se Sim:** corre o reconciliador determinístico (sem API, sem LLM) e produz relatórios em
`reconcile/`. O que detecta:

| Flag | Significado |
|------|-------------|
| `answer_accepted_but_zero` | Resposta aceite pelo gabarito, mas 0 pontos atribuídos |
| `answer_not_accepted_but_full` | Resposta não aceite pelo gabarito, mas nota máxima atribuída |
| `answer_accepted_but_partial` | Crédito parcial configurado (informativo, não é contradição) |
| `inconsistent_scoring` | Mesma pergunta + mesma resposta → pontos diferentes entre alunos |

Também reconcilia a `grade` oficial (campo da API) com `round(Σpoints/Σmax × 100)` calculado
em `Decimal` (sem erros de vírgula flutuante).

**Se Não:** passo ignorado. Os CSVs das submissões e gabarito ficam disponíveis na pasta de run.

---

## Estrutura de output

Cada corrida cria uma pasta com timestamp — **nunca sobrescreve** corridas anteriores:

```
output/
  <programa>/                             ex: pggf2
    <label>/                              ex: uc5-fintech
      <YYYY-MM-DD_HHmmss>/                uma pasta por corrida
        submissions/
          raw/
            raw_response.json             resposta crua da API (todas as páginas)
            extraction_report.json        contagens, avisos, endpoint chamado
          submissions_export.csv          1 linha por bloco de resposta por aluno
          submissions_export.xlsx
        exam_config/
          raw/
            raw_exam_config.json
            extraction_report.json
          exam_config_as_is.csv           gabarito LW: respostas corretas + opções
          exam_config_as_is.xlsx
        answer_key/                       só se o Passo [5/6] foi corrido
          manual_answer_key.csv
        reconcile/                        só se o Passo [6/6] foi corrido
          reconciliation_report/
            reconciliation_report.csv     1 linha por (aluno × pergunta), com flag
            reconciliation_report.xlsx
          grade_reconciliation/
            grade_reconciliation.csv      nota oficial vs recalculada, por aluno
            grade_reconciliation.xlsx
          consistency_report/
            consistency_report.csv        respostas iguais pontuadas diferente
            consistency_report.xlsx
          manual_review_queue/
            manual_review_queue.csv       perguntas sem gabarito ou MCMA ambíguo
            manual_review_queue.xlsx
          reconciliation_summary.json
          reconciliation_summary.md       sumário legível com contagens e flags
```

---

## Estrutura de inputs

Os ficheiros externos (XLSX do LW, Word dos docentes) são **copiados e renomeados** para a
pasta `input/` com a nomenclatura do projeto. Os originais ficam intactos onde estavam.

```
input/
  <programa>/
    exam_configs/
      <label>_exam_config.xlsx          cópia renomeada do export LW
    word_docs/
      <label>_<nome_original>.docx      cópia renomeada de cada guião Word
```

---

## Fontes de dados e o que contêm

| Ficheiro | O que contém | Origem |
|----------|--------------|--------|
| `submissions_export.csv` | O que cada aluno respondeu: 1 linha por bloco de resposta, com `blockType`, `answer`, `points`, `blockMaxScore`, `user_id`, `username`, `email`, `grade` | API `GET /v2/assessments/{id}/responses` |
| `exam_config_as_is.csv` | Gabarito LW: respostas corretas configuradas, opções disponíveis, feedback, `blockType` | Export manual da UI (a API não expõe o gabarito) |
| `manual_answer_key.csv` | Gabarito docente: resposta correta por pergunta extraída dos Word docs via LLM, com `confidence` e `answers_match` vs LW | Word docs dos docentes → OpenAI |
| `reconciliation_report.csv` | Cruzamento de todas as respostas com o gabarito: `verifiable`, `is_correct`, `flag` por linha | Processamento local |
| `grade_reconciliation.csv` | Nota oficial (`grade` da API) vs nota recalculada (`Σpoints/Σmax×100`) por aluno | Processamento local |
| `manual_review_queue.csv` | Perguntas que precisam de revisão humana: sem gabarito, sem match de configuração, ou MCMA ambíguo. 1 linha por pergunta (deduplicada) | Processamento local |

---

## Revisão final (humano)

A reconciliação **assinala**, não decide. Após correr:

1. Abrir `reconciliation_summary.md` para o sumário
2. Rever `manual_review_queue.csv` — perguntas sem gabarito ou ambíguas
3. Rever `reconciliation_report.csv` filtrando `flag != ""` — contradições pontuação/gabarito
4. Se o Passo [5/6] foi corrido: rever `manual_answer_key.csv` filtrando `needs_review=true`
   ou `answers_match=no` — discrepâncias entre o gabarito LW e a intenção do docente

---

## Uso avançado — CLI direta

Todos os scripts aceitam `--help`. Exemplos:

```bash
# Só submissões, sem launcher
python -m extractor.run_extract --assessment-id <id> --label "uc5"

# Importar gabarito LW manualmente
python -m extractor.run_exam_config --xlsx "/path/export.xlsx" --assessment-id <id> --label "uc5"

# Extrair gabarito docente manualmente
python tools/extract_answer_key.py \
    --exam-config "output/pggf2/uc5-fintech/2026-06-20_120000/exam_config/exam_config_as_is.csv" \
    --docs "input/pggf2/word_docs/uc5-fintech_gabarito_ua1.docx"

# Reconciliar manualmente apontando para uma pasta de run
python -m reconcile.run_reconcile \
    --run-dir "output/pggf2/uc5-fintech/2026-06-20_120000"

# Notas do curso (passo opcional, não incluído no launcher)
python -m extractor.run_grades --course-id <slug> --label "uc5"
```

---

## Notas técnicas

- **Token LW:** nunca criado nem renovado pela ferramenta. Token expirado → erro HTTP 401/403 claro.
- **`grade` é percentagem** — `round(Σpoints/Σmax × 100)`, não pontos brutos. Confirmado nos dados da API.
- **`derived_score_status`** no `submissions_export.csv` é calculado localmente a partir de `points`/`blockMaxScore` (não é campo oficial da API).
- **Matching robusto:** API e export UI diferem em pontuação/espaços. A comparação usa normalização alfanumérica (`join_key`) para não gerar falsos positivos.
- **MCMA:** respostas concatenadas sem delimitador. O reconciliador reconstrói o conjunto por contenção das opções conhecidas; se ambíguo → `unverifiable_mcma` na fila de revisão.
- **Overflow do export da UI:** perguntas com >5 opções transbordam para colunas sem cabeçalho; o importador trata e preserva tudo em `row_raw`.
- Detalhe técnico de colunas e endpoints: [`extractor/README.md`](extractor/README.md)
