# Processo de auditoria de avaliações — end-to-end

Guião completo para, a partir do LearnWorlds, **extrair** os dados das avaliações e
**reconciliar** as fontes de forma determinística, produzindo relatórios auditáveis.

> **Âmbito:** este projeto faz (1) a **extração** (API → CSV / export UI → CSV / Word → CSV via LLM)
> e (2) uma **reconciliação determinística** que junta as fontes e **assinala** discrepâncias com
> regras exatas e auditáveis. Fica **fora de âmbito**: qualquer juízo semântico/pedagógico ou
> "corrigir" dados. A reconciliação **não decide** se uma pergunta está mal — apenas levanta o caso
> para revisão humana.

---

## Arquitectura do pipeline

```text
┌─────────────────────┐   ┌──────────────────────┐   ┌──────────────────────────┐
│   LearnWorlds API   │   │  LearnWorlds UI       │   │   Guiões Word            │
│                     │   │  Export (XLSX)        │   │   (Docentes)             │
└──────────┬──────────┘   └──────────┬────────────┘   └────────────┬─────────────┘
           │  [Passo 4]              │  [Passo 4]                  │  [Passo 5]
           ▼                         ▼                              ▼
  submissions_export.csv   exam_config_as_is.csv             LLM (OpenAI)
  (o que cada aluno         (gabarito LW — opções                  │
   respondeu, pontos,        e respostas correctas,                ├─ Fase 1: perguntas com
   blockType)                tipos de pergunta)                    │  opções LW (TMC, TTF…)
                                                                   │  → manual_answer_key.csv
                                                                   │
                                                                   └─ Fase 2 ★: perguntas SEM
                                                                      opções no LW
                                                                      (lacunas / correspondência)
                                                                      → inferred_answer_key.csv

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
               [Passo 6]  RECONCILIADOR  (sem API, sem LLM)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Merge em memória das três fontes:
    gabarito LW  +  gabarito docente (Fase 1)  +  respostas inferidas (Fase 2)
           │
           ├─ verifiable = "yes"                     verificado pelo gabarito LW
           ├─ verifiable = "inferred" ★              verificado por inferência LLM
           ├─ verifiable = "inferred_low_confidence" confiança baixa → revisão manual
           └─ verifiable = "no_config_match"         sem gabarito em nenhuma fonte

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                              OUTPUTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  reconciliation_report.csv    grade_reconciliation.csv    manual_review_queue.csv
  reconciliation_summary.md    question_index.csv
  audit_interpretation.md   ←  [Passo 6b — LLM]
```

★ **Fase 2** é automática sempre que o Passo 5 corre com `--run-dir`. O LLM detecta perguntas
de preenchimento de lacunas e de correspondência que o LearnWorlds **não exporta** no XLSX e
procura as respostas correctas no guião Word, com padrões específicos ("Variações aceites
(espaço N)", pares com cor ou "Par N"). As respostas ficam em `inferred_answer_key.csv`,
claramente marcadas como inferidas. High/medium confidence entram na reconciliação;
low/unmatched ficam na fila de revisão manual. Estas respostas **nunca substituem** a
verificação humana — são um ponto de partida, não uma decisão.

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

Depois de selecionar, corre **`tools/extract_answer_key.py`** via OpenAI (requer `OPENAI_API_KEY` no `.env`) em **duas fases automáticas**:

**Fase 1 — Perguntas com gabarito LW** (TMC, TMCMA, TTF, TD, TST):
- Cruza o gabarito LW com o guião Word e produz `answer_key/manual_answer_key.csv`:
  - `lw_correct_answer` (do gabarito LW) vs `doc_correct_answer` (extraído do Word)
  - `answers_match` (`yes / no / lw_only / doc_only`)
  - `confidence` (`high / medium / low / unmatched`)
  - `needs_review` — `true` se houver discrepância ou baixa confiança
- O LLM reconhece respostas marcadas por: negrito, estilo Word, asterisco, checkmark, cor,
  sublinhado, etiqueta explícita ("Resposta: B)"), tabela, ou riscado nas erradas.
- **Excluídos:** TP, TFU (correção manual — não têm resposta certa a extrair)

**Fase 2 — Perguntas sem gabarito LW** (preenchimento de lacunas, correspondência):
- O LearnWorlds **não exporta** estas perguntas no XLSX. O LLM localiza-as no guião Word
  e infere as respostas correctas usando padrões específicos:
  - **Lacunas:** etiquetas "Variações aceites (espaço N)", variantes separadas por "`;`",
    lacunas separadas por "`|`" → ex: `"WACC; custo médio ponderado de capital | retorno mínimo"`
  - **Correspondência:** pares com cor idêntica ou etiquetas "Par N — Coluna A / Coluna B"
    → ex: `"Forward cambial → Fixa a taxa de câmbio; Swap → Troca taxa variável"`
- Produz `answer_key/inferred_answer_key.csv` com `blockType`, `question_text`,
  `doc_correct_answer`, `confidence`, `notes`, `source_doc`
- Respostas com high/medium confidence entram na reconciliação (Passo 6)
- Respostas com low/unmatched ficam na fila de revisão manual

> ⚠️ As respostas da Fase 2 são inferências automáticas — não são um gabarito oficial.
> Verificar sempre antes de tomar decisões sobre notas.

**Se Não:** passo ignorado, avança para o passo 6.

---

### [6/6] RECONCILIAÇÃO *(opcional)*

Diálogo Sim/Não: "Deseja fazer uma análise de reconciliação entre o gabarito e as respostas
dos participantes?"

**Se Sim:** corre o reconciliador determinístico (sem API, sem LLM) e produz relatórios em
`reconcile/`. O que detecta:

| Flag | Significado |
|------|-------------|
| `answer_correct_per_doc_but_zero` | ⚠️ Aluno respondeu conforme a intenção do docente (gabarito Word) mas o LW não aceitou e deu 0 pontos — **erro de parametrização no LW** |
| `answer_accepted_but_zero` | Resposta aceite pelo gabarito LW, mas 0 pontos atribuídos |
| `answer_not_accepted_but_full` | Resposta não aceite pelo gabarito, mas nota máxima atribuída |
| `answer_accepted_but_partial` | Crédito parcial configurado (informativo, não é contradição) |
| `inconsistent_scoring` | Mesma pergunta + mesma resposta → pontos diferentes entre alunos |

A reconciliação também indica a **origem da verificação** (`answer_matched_source`):
- `lw` — resposta verificada contra o gabarito LW
- `doc` — resposta verificada contra o gabarito do docente (Word), quando o LW tinha configuração errada
- `inferred` — resposta verificada contra inferência LLM (Fase 2, lacunas/correspondência)

Também reconcilia a `grade` oficial (campo da API) com `round(Σpoints/Σmax × 100)` calculado
em `Decimal` (sem erros de vírgula flutuante).

**Se Não:** passo ignorado. Os CSVs das submissões e gabarito ficam disponíveis na pasta de run.

---

### [6b/6] INTERPRETAÇÃO AUTOMÁTICA *(automático após reconciliação)*

Após o Passo [6/6] completar com sucesso, o lançador corre automaticamente
`tools/interpret_run.py`, que chama a OpenAI para produzir uma interpretação sintética
da auditoria em Português.

O que produz — `audit_interpretation.md` (na raiz da pasta de run):
- **Resumo executivo** — visão geral em 2-3 parágrafos
- **✅ O que correu bem** — pontos positivos
- **⚠️ Problemas a corrigir** — flags com nomes de alunos, respostas concretas, acção sugerida
- **ℹ️ Informação relevante** — limitações de auditabilidade, contexto metodológico
- **Next steps** — prioridade (🔴🟡🔵) e acção a tomar

> Se `OPENAI_API_KEY` não estiver configurada em `.env`, este passo é ignorado sem erro.
> Pode também correr manualmente:
> ```bash
> python tools/interpret_run.py --run-dir "output/pggf2/uc5-fintech/2026-06-20_120000"
> ```

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
          manual_answer_key.csv           gabarito docente (Fase 1 — perguntas com opções LW)
          inferred_answer_key.csv         respostas inferidas (Fase 2 — lacunas/correspondência)
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
        audit_interpretation.md           só se o Passo 6b foi corrido — interpretação IA
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
| `exam_config_as_is.csv` | Gabarito LW: respostas correctas configuradas, opções disponíveis, feedback, `blockType`. **Não inclui** lacunas nem correspondência — o LW não as exporta. | Export manual da UI (a API não expõe o gabarito) |
| `manual_answer_key.csv` | Gabarito docente (Fase 1): resposta correcta por pergunta extraída dos Word docs via LLM, com `confidence` e `answers_match` vs LW. Cobre perguntas com opções LW (TMC, TTF…). | Word docs dos docentes → OpenAI |
| `inferred_answer_key.csv` | Respostas inferidas (Fase 2): respostas para lacunas e correspondência que o LW não exporta, extraídas do guião Word via LLM. `confidence` indica fiabilidade — verificar antes de usar. | Word docs dos docentes → OpenAI |
| `reconciliation_report.csv` | Cruzamento de todas as respostas com o gabarito: `verifiable` (`yes` / `inferred` / `no_config_match` / …), `is_correct`, `flag`, `answer_matched_source` por linha. | Processamento local |
| `grade_reconciliation.csv` | Nota oficial (`grade` da API) vs nota recalculada (`Σpoints/Σmax×100`) por aluno | Processamento local |
| `manual_review_queue.csv` | Perguntas que precisam de revisão humana: sem gabarito, MCMA ambíguo, ou inferência com baixa confiança. 1 linha por pergunta (deduplicada). | Processamento local |

---

## Revisão final (humano)

A reconciliação **assinala**, não decide. Após correr:

1. Abrir `audit_interpretation.md` — ponto de entrada principal; lista todos os problemas por ordem de gravidade e os próximos passos
2. Rever `reconciliation_report.csv` filtrando `flag != ""` — contradições pontuação/gabarito
3. Se o Passo [5/6] foi corrido:
   - Rever `manual_answer_key.csv` filtrando `needs_review=true` ou `answers_match=no` — discrepâncias entre o gabarito LW e a intenção do docente
   - **Confirmar as respostas inferidas** (ver secção abaixo)

### Confirmar respostas inferidas (lacunas e correspondência)

As perguntas de preenchimento de lacunas e de correspondência não têm gabarito no LW — a resposta foi inferida automaticamente a partir do guião Word. O processo de confirmação é simples:

1. Em `audit_interpretation.md`, ler a secção **"Perguntas sem gabarito disponível / Resposta inferida automaticamente"**
2. Para cada pergunta listada, comparar a resposta inferida com o que o docente escreveu no guião Word
3. **Se estiver correcta** → as notas para essa pergunta estão boas; nenhuma acção necessária
4. **Se faltar uma variante** (ex: um aluno escreveu uma formulação válida que não está nas variantes listadas):
   - Abrir `answer_key/inferred_answer_key.csv` na pasta da run
   - Adicionar a variante em falta no campo `doc_correct_answer` da pergunta (separar por `"; "` dentro da mesma lacuna, `" | "` entre lacunas)
   - Re-correr a reconciliação: `python -m reconcile.run_reconcile --run-dir <pasta da run>`
   - Re-gerar a interpretação: `python tools/interpret_run.py --run-dir <pasta da run>`

### Casos de "resposta correcta com texto a mais"

Se o `audit_interpretation.md` listar alunos em **"Resposta correcta com texto a mais — rejeitada pelo LearnWorlds"**, significa que o sistema detectou que o aluno escreveu a resposta certa mas com texto adicional (ex: deu o nome em inglês e português na mesma lacuna). O LearnWorlds exige correspondência exacta e rejeitou. Para cada caso:

- **Se o aluno merece crédito** → corrigir manualmente a nota no LearnWorlds
- **Se a resposta é genuinamente diferente** (o texto a mais muda o significado) → dispensar; a nota fica como está

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

# Gerar interpretação IA manualmente (depois de reconciliar)
python tools/interpret_run.py \
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
