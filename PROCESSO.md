# Processo de auditoria de avaliações — end-to-end

[🇬🇧 English version](PROCESS.md)

---

## O que isto faz (para quem não é técnico)

Quando os alunos fazem um teste no LearnWorlds, as respostas ficam guardadas digitalmente na plataforma. Esta ferramenta faz três coisas automaticamente:

1. **Extrai** todas as respostas submetidas pelos alunos, o gabarito oficial configurado no LearnWorlds, e — se existir — o gabarito entregue pelo docente em Word.

2. **Compara** cada resposta de cada aluno com o gabarito, usando regras exactas e reproduzíveis. Não interpreta nem "adivinha" — só aplica as regras e assinala os casos que não encaixam.

3. **Produz um relatório** em Português que lista, por ordem de prioridade, os problemas encontrados: alunos que responderam correctamente mas ficaram com 0 pontos, respostas que o sistema aceitou como certas mas que o gabarito do docente contradiz, pontuações inconsistentes para a mesma resposta, entre outros.

**O sistema nunca decide nada.** Cabe sempre a um humano verificar os casos sinalizados e decidir se há algo a corrigir. O que a ferramenta faz é garantir que nenhum caso escapar despercebido.

> Âmbito: extração de dados + reconciliação determinística + sinalização de discrepâncias. Fica fora de âmbito qualquer juízo pedagógico ou correcção automática de notas.

---

## Arquitectura do pipeline

```text
┌─────────────────────┐   ┌──────────────────────┐   ┌──────────────────────────┐
│   LearnWorlds API   │   │  LearnWorlds UI       │   │   Guiões Word            │
│                     │   │  Export (XLSX)        │   │   (Docentes)             │
└──────────┬──────────┘   └──────────┬────────────┘   └────────────┬─────────────┘
           │  [Passo 1]              │  [Passo 1]                  │  [Passo 2]
           ▼                         ▼                              ▼
  submissions_export.csv   exam_config_as_is.csv             LLM (OpenAI)
  (o que cada aluno         (gabarito LW — opções                  │
   respondeu, pontos,        e respostas correctas,                ├─ Fase 1: perguntas com
   blockType)                tipos de pergunta)                    │  opções LW (TMC, TTF…)
                                                                   │  → manual_answer_key.csv
                                                                   │
                                                                   └─ Fase 2: perguntas SEM
                                                                      opções no LW
                                                                      (lacunas / correspondência)
                                                                      → inferred_answer_key.csv

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
               [Passo 3]  RECONCILIADOR  (sem API, sem LLM)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Merge em memória das três fontes:
    gabarito LW  +  gabarito docente (Fase 1)  +  respostas inferidas (Fase 2)
           │
           ├─ verifiable = "yes"             verificado pelo gabarito LW
           ├─ verifiable = "inferred"        verificado por inferência do Word
           ├─ verifiable = "unverifiable_mcma"  MCMA ambíguo → revisão manual
           └─ verifiable = "no_config_match" sem gabarito em nenhuma fonte

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                              OUTPUTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  reconciliation_report.csv    grade_reconciliation.csv    manual_review_queue.csv
  reconciliation_summary.md    question_index.csv
  audit_interpretation.md   ←  [Passo 4 — LLM]
```

---

## Pré-requisitos (configuração inicial — uma vez)

### 1. Python 3.10+

Verifica com `python3 --version`. Instalar em [python.org](https://www.python.org) se necessário.

O ambiente virtual (`.venv`) e as dependências são instalados **automaticamente** na primeira vez. Não é necessário fazer nada manualmente.

### 2. Ficheiro `.env`

Copiar `.env.example` para `.env` (na raiz do projeto) e preencher:

```
LEARNWORLDS_API_URL=https://online.executiveducation.novasbe.pt/admin/api
LEARNWORLDS_SCHOOL_ID=<school id da Nova Fórum>
LEARNWORLDS_ACCESS_TOKEN=<token de acesso LearnWorlds — ver abaixo>
OPENAI_API_KEY=<chave da API OpenAI — necessária para gabaritos Word e interpretação>
OPENAI_MODEL=gpt-4o
```

> **Token LearnWorlds:** gerado manualmente no admin LW, tem validade limitada. Esta ferramenta **nunca** cria nem renova tokens. Token expirado → erro HTTP 401 claro. Colar o novo token no `.env`.

> **Segurança:** o `.env` tem credenciais sensíveis. Está no `.gitignore` e nunca deve ser partilhado nem enviado por email.

### 3. Ficheiro `assessment.cfg`

Ficheiro na raiz do projeto, já incluído no repositório. Actualizar antes de cada avaliação:

```
PROGRAM=pggf2          ← sigla do programa + edição (ex: pggf2, mba3)
LABEL=uc5-fintech      ← usado como nome da pasta de output
LABEL_DISPLAY=UC5: Fintech e Inovação Financeira   ← nome apresentado nos relatórios
ASSESSMENT_ID=000000000000000000000000             ← ID de 24 chars do URL do admin LW
EPOCA=Normal           ← Normal | Extraordinária
```

O `ASSESSMENT_ID` encontra-se no final do URL da atividade no admin LW:
`https://.../admin/assessments/**000000000000000000000000**/edit`

> O `assessment.cfg` não tem credenciais — pode ir para o git.

---

## Como correr — interface gráfica (recomendado)

Fazer duplo-clique em **`run_audit_gui.py`** (ou `python run_audit_gui.py`).

A janela tem duas abas:

---

### Aba "Nova Run"

Para correr uma auditoria completa de raiz.

Os campos do formulário vêm pré-preenchidos com `assessment.cfg`. Actualizar se necessário, depois seguir os 4 passos em sequência:

---

#### [1/4] Gabarito LW

Clicar em **"Selecionar Gabarito LW (XLSX)"** e escolher o ficheiro exportado da UI do LearnWorlds.

**Como exportar o XLSX:**
1. Admin LearnWorlds → curso → **Course Outline**
2. Clicar na atividade → **Edit questions**
3. Botão **Export → Export as .xls**

Depois de selecionar:
- O ficheiro é copiado para `input/<programa>/exam_configs/`
- Corre automaticamente: extração de submissões (API) + importação do gabarito (XLSX)

> ⚠️ Exportar o XLSX da **mesma versão** do teste que os alunos fizeram. Edições posteriores às submissões quebram o matching.

---

#### [2/4] Word docs *(opcional)*

Clicar em **"Selecionar Word docs"** e escolher os `.docx` dos docentes (Cmd+clique / Ctrl+clique para múltiplos).

Ou clicar em **"Ignorar Word docs"** para avançar sem este passo.

Se seleccionados, corre `tools/extract_answer_key.py` via OpenAI em duas fases automáticas:

**Fase 1 — Perguntas com gabarito LW** (TMC, TMCMA, TTF, TD, TST):

O LLM cruza o gabarito LW com o guião Word e produz `answer_key/manual_answer_key.csv`:

- `lw_correct_answer` vs `doc_correct_answer` — resposta no LW e no Word
- `answers_match` — `yes / no / lw_only / doc_only`
- `confidence` — `high / medium / low / unmatched`
- `needs_review` — `true` quando há discrepância ou confiança baixa

O LLM reconhece respostas marcadas por: negrito, estilo Word, asterisco, checkmark, cor, sublinhado, etiqueta explícita, tabela, riscado nas erradas.

As perguntas são enviadas ao LLM **por documento** — se o assessment tem 3 Word docs (15+15+15 perguntas), cada doc recebe apenas as suas perguntas. Evita que o LLM associe perguntas ao doc errado.

**Fase 2 — Perguntas sem gabarito LW** (lacunas, correspondência):

O LearnWorlds **não exporta** estas perguntas no XLSX. O LLM localiza-as no Word e infere as respostas correctas:

- **Lacunas:** etiquetas "Variações aceites (espaço N)", variantes separadas por `; `, lacunas por ` | `
- **Correspondência:** pares com cor idêntica ou etiquetas "Par N"

Produz `answer_key/inferred_answer_key.csv`. Respostas com high/medium confidence entram na reconciliação; low/unmatched vão para revisão manual.

> ⚠️ As respostas da Fase 2 são inferências automáticas. Verificar sempre antes de tomar decisões sobre notas.

---

#### [3/4] Reconciliar

Clicar em **"Reconciliar"**.

Corre o reconciliador determinístico (sem API, sem LLM). Junta as três fontes e produz os relatórios em `reconcile/`.

O que detecta:

| Flag | Significado |
|------|-------------|
| `answer_correct_per_doc_but_zero` | ⚠️ Aluno respondeu conforme a intenção do docente (Word) mas o LW deu 0 — **erro de parametrização no LW** |
| `answer_accepted_but_zero` | Resposta aceite pelo gabarito LW, mas 0 pontos |
| `answer_not_accepted_but_full` | Resposta não aceite pelo gabarito, mas nota máxima |
| `mcma_wrong_not_penalized` | MCMA: aluno seleccionou todas as opções correctas + opções erradas e recebeu nota máxima — o LW não penalizou as escolhas erradas |
| `answer_accepted_but_partial` | Crédito parcial configurado (informativo) |

A reconciliação também indica a **origem da verificação** (`answer_matched_source`):
- `lw` — verificado contra o gabarito LW
- `doc` — verificado contra o gabarito do docente (Word), quando o LW tinha configuração errada
- `inferred` — verificado contra inferência LLM (lacunas / correspondência)

Também reconcilia a `grade` oficial (campo da API) com `round(Σpoints/Σmax × 100)` calculado em `Decimal`.

A numeração canónica das perguntas deriva da **ordem de submissão** (não do XLSX nem do Word), garantindo que todas as fontes usam o mesmo índice.

---

#### [4/4] Interpretar

Clicar em **"Interpretar"**.

Chama a OpenAI e produz `audit_interpretation.md` na pasta da run.

O que inclui:
- **Resumo executivo** — visão geral em 2-3 parágrafos
- **✅ O que correu bem**
- **⚠️ Problemas a corrigir** — flags com nomes de alunos, respostas concretas, acção sugerida
- **ℹ️ Informação relevante** — limitações, contexto metodológico
- **Next steps** — prioridade (🔴🟡🔵) e acção a tomar

---

### Aba "Re-correr"

Para reconciliar e/ou interpretar uma run já existente — útil após corrigir manualmente um `manual_answer_key.csv` ou `inferred_answer_key.csv`.

1. Clicar em **"Selecionar pasta da run"** e escolher a pasta com timestamp
2. Clicar em **"Reconciliar"** e/ou **"Interpretar"**
3. **"Abrir no Finder"** — abre a pasta da run directamente

---

## Alternativa: launchers de terminal

Para quem preferir linha de comando, os launchers originais continuam disponíveis:

- **macOS:** duplo-clique em `run_audit.command` (correr `chmod +x run_audit.command` na primeira vez)
- **Windows:** duplo-clique em `run_audit.bat`

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
        answer_key/                       só se o passo Word foi corrido
          manual_answer_key.csv           gabarito docente (Fase 1 — perguntas com opções LW)
          inferred_answer_key.csv         respostas inferidas (Fase 2 — lacunas/correspondência)
        question_index.csv                índice unificado de todas as perguntas activas
        reconcile/                        só se a reconciliação foi corrida
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
            manual_review_queue.csv       perguntas que precisam de revisão humana
            manual_review_queue.xlsx
          reconciliation_summary.json
          reconciliation_summary.md       sumário legível com contagens e flags
        audit_interpretation.md           interpretação IA em Português
```

---

## Fontes de dados e o que contêm

| Ficheiro | O que contém | Origem |
|----------|--------------|--------|
| `submissions_export.csv` | 1 linha por bloco de resposta por aluno: `blockType`, `answer`, `points`, `blockMaxScore`, `user_id`, `username`, `email`, `grade` | API `GET /v2/assessments/{id}/responses` |
| `exam_config_as_is.csv` | Gabarito LW: respostas correctas, opções, feedback, `blockType`. **Não inclui** lacunas nem correspondência. | Export manual da UI |
| `manual_answer_key.csv` | Gabarito docente (Fase 1): resposta correcta por pergunta extraída do Word via LLM, com `confidence` e `answers_match`. | Word docs → OpenAI |
| `inferred_answer_key.csv` | Respostas inferidas (Fase 2): lacunas e correspondência não exportadas pelo LW. `confidence` indica fiabilidade. | Word docs → OpenAI |
| `question_index.csv` | Índice unificado de todas as perguntas activas, com número canónico, fonte (LW/inferred), e gabaritos de todas as fontes. | Processamento local |
| `reconciliation_report.csv` | 1 linha por (aluno × pergunta): `verifiable`, `is_correct`, `flag`, `answer_matched_source`. | Processamento local |
| `grade_reconciliation.csv` | Nota oficial (`grade` da API) vs nota recalculada por aluno. | Processamento local |
| `manual_review_queue.csv` | Perguntas sem gabarito, MCMA ambíguo, ou inferência irresolúvel. 1 linha por pergunta. | Processamento local |

---

## Revisão final (humano)

A reconciliação **assinala**, não decide. Após correr:

1. Abrir `audit_interpretation.md` — ponto de entrada principal; lista todos os problemas por ordem de gravidade
2. Para cada problema sinalizado: verificar no `reconciliation_report.csv` ou `manual_review_queue.csv`
3. Se o passo Word foi corrido: rever `manual_answer_key.csv` filtrando `needs_review=true`

### Confirmar respostas inferidas (lacunas e correspondência)

Perguntas de preenchimento de lacunas e correspondência não têm gabarito no LW — a resposta foi inferida automaticamente do Word.

1. Em `audit_interpretation.md`, ler a secção **"Perguntas inferidas"**
2. Para cada pergunta, comparar com o que o docente escreveu no Word
3. **Se correcta** → nenhuma acção
4. **Se faltar uma variante válida:**
   - Abrir `answer_key/inferred_answer_key.csv` na pasta da run
   - Adicionar a variante no campo `doc_correct_answer` (separar por `"; "` dentro da lacuna, `" | "` entre lacunas)
   - Re-correr pela aba "Re-correr" da GUI (ou `python -m reconcile.run_reconcile --run-dir <pasta>`)

### Alucinações do LLM no gabarito Word

O LLM que extrai o `manual_answer_key.csv` pode ocasionalmente extrair texto ligeiramente diferente do que está no Word (sinónimos, tradução, formatação). Quando `needs_review=true` por `answers_match=no`, verificar sempre se a divergência é real (gabaritos genuinamente diferentes) ou uma alucinação (o doc e o LW dizem o mesmo com palavras diferentes).

Se for alucinação: editar `doc_correct_answer` no CSV para o texto correcto e re-reconciliar.

---

## Uso avançado — CLI directa

Todos os scripts aceitam `--help`. Exemplos:

```bash
# Só submissões
python -m extractor.run_extract --assessment-id <id> --label "uc5"

# Gabarito LW
python -m extractor.run_exam_config --xlsx "/path/export.xlsx" --assessment-id <id> --label "uc5"

# Gabarito Word
python tools/extract_answer_key.py \
    --exam-config "output/pggf2/uc5/2026-06-20_120000/exam_config/exam_config_as_is.csv" \
    --docs "input/pggf2/word_docs/uc5_gabarito.docx"

# Reconciliar
python -m reconcile.run_reconcile \
    --run-dir "output/pggf2/uc5/2026-06-20_120000"

# Interpretar
python tools/interpret_run.py \
    --run-dir "output/pggf2/uc5/2026-06-20_120000"
```

---

## Notas técnicas

- **Token LW:** nunca criado nem renovado pela ferramenta. Token expirado → erro HTTP 401 claro.
- **`grade` é percentagem** — `round(Σpoints/Σmax × 100)`, não pontos brutos.
- **`derived_score_status`** no `submissions_export.csv` é calculado localmente (não é campo oficial da API).
- **Matching robusto:** API e export UI diferem em pontuação/espaços. A comparação usa `join_key()` (normalização alfanumérica) para evitar falsos positivos.
- **MCMA:** respostas concatenadas sem delimitador. O reconciliador reconstrói o conjunto por contenção das opções conhecidas; se ambíguo → `unverifiable_mcma`.
- **Doc override MCMA:** quando o gabarito Word diverge das opções LW em wording (não apenas formatting), o reconciliador não consegue mapear automaticamente e marca `unverifiable_mcma` para revisão humana.
- **Numeração canónica:** o número de cada pergunta é derivado da ordem de primeira aparição nas submissões, não do XLSX nem do Word. Garante consistência entre todas as fontes.
- **Doc-scoped extraction:** quando há múltiplos Word docs, cada doc recebe apenas as suas perguntas (determinadas pela posição nas submissões). Evita que o LLM associe perguntas a docs errados.
- Detalhe técnico de colunas e endpoints: [`extractor/README.md`](extractor/README.md)
