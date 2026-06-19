# Processo de auditoria de avaliações — end-to-end

Guião completo para produzir, a partir do LearnWorlds, os ficheiros CSV limpos que
alimentam a validação externa (conta ChatGPT da empresa).

> **Âmbito:** este projeto é **só extração** (API → CSV / export UI → CSV).
> **Não** valida respostas, **não** compara CSVs, **não** recalcula notas. Esses
> passos pertencem à fase de validação, que é **separada** e corre noutro sítio.

---

## Visão geral — três fontes de dados

| # | Ficheiro | O que contém | Origem |
|---|----------|--------------|--------|
| 1 | `submissions_export.csv` | O que cada aluno respondeu (1 linha por bloco: `points`, `blockMaxScore`, `answer`, `description`; aluno: `user_id`, `username`, `email`) | **API** `GET /v2/assessments/{id}/responses` (+ `GET /v2/users/{id}` p/ username) |
| 2 | `course_grades.csv` | A nota oficial registada por aluno por assessment (`user_id`, `username`, `email`, `grade`) | **API** `GET /v2/courses/{slug}/grades` (+ users) |
| 3 | `exam_config_as_is.csv` | O **gabarito**: respostas corretas configuradas, opções, feedback | **Export manual da UI** (a API não o expõe) |

As três juntam-se por **`user` (email/user_id)** + **texto da pergunta** (`description`).
Não há id de pergunta partilhado no export da UI — por isso o join é por texto.

---

## Pré-requisitos (uma vez)

1. **Python 3.10+** com OpenSSL moderno (a API exige TLS recente).
2. Criar o ambiente e instalar dependências, a partir da pasta do projeto:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate            # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. **`.env`** preenchido (copiar de `.env.example`). Para os passos de API precisas de:
   - `LEARNWORLDS_API_URL`, `LEARNWORLDS_SCHOOL_ID`, `LEARNWORLDS_ACCESS_TOKEN` (token **válido**)
   - `ASSESSMENT_ID` (para o passo 1) e/ou `COURSE_ID` (para o passo 2)

> O `.env` tem credenciais sensíveis e os outputs têm dados pessoais de alunos.
> **Não partilhar fora da equipa LMS, não criar links públicos, não enviar por email.**
> (Ambos já estão no `.gitignore`.)

---

## O processo, passo a passo

### Passo 1 — Submissões (API)

```bash
python -m extractor.run_extract --assessment-id <assessment_id> \
    --label "titulo-da-atividade"
# ou, usando o ASSESSMENT_ID do .env:
python -m extractor.run_extract --label "titulo-da-atividade"
```
Produz, em `output/<titulo-da-atividade>/` (sem `--label`, usa o id do assessment):
- `raw_response_<ts>.json` (resposta crua, todas as páginas)
- `submissions_export_<ts>.csv` **e** `submissions_export_<ts>.xlsx` (1 linha por resposta)
- `extraction_report_<ts>.json`

> **`--label`** nomeia a pasta de output pelo **título da atividade**. Usa o **mesmo
> `--label`** no Passo 3 para os outputs do mesmo teste ficarem na mesma pasta.

### Passo 2 — Notas oficiais (API)

```bash
python -m extractor.run_grades --course-id <course_slug>
```
Produz, em `output/course_<course_slug>/` (ou `--label`): `raw_grades_<ts>.json`,
`course_grades_<ts>.csv` **e** `.xlsx`, `extraction_report_<ts>.json`.

> Bónus: o report deste passo lista os `assessment_unit_ids` (`assessmentV2`) do curso
> — útil para saber que assessments correr no Passo 1.

### Passo 3 — Gabarito (export manual da UI + import)

A configuração do teste (respostas corretas) **não está na API**. Exporta-a da UI:

1. No admin LearnWorlds, abre o **assessment** → exporta as **perguntas** para **XLSX**
   (a folha chama-se `Questions`, com colunas `Type, Question, CorrectAns, Answer1…`).
2. Importa esse ficheiro:
   ```bash
   python -m extractor.run_exam_config \
       --xlsx "/caminho/para/o_export.xlsx" \
       --assessment-id <assessment_id> \
       --course-id <course_slug> \
       --label "titulo-da-atividade"
   ```
Produz, em `output/<titulo-da-atividade>/` (por defeito, o título do ficheiro;
ou `--label`): `raw_exam_config_<ts>.json`, `exam_config_as_is_<ts>.csv` **e** `.xlsx`,
`extraction_report_<ts>.json`.

> Este passo **não** faz chamadas à API (só lê o XLSX).

### Passo 4 — Entrega à validação (fora deste projeto)

Entrega os três CSVs (`submissions_export`, `course_grades`, `exam_config_as_is`) à
fase de validação. É lá que se cruza nota oficial × pontos extraídos × gabarito para
detetar perguntas mal parametrizadas ou avaliações inesperadas. **Este projeto não
faz essa comparação.**

---

## Onde ficam os outputs

Tudo em `output/`, sempre com timestamp (CSV **e** XLSX), **nunca sobrescrito**:

```
output/
  <titulo-da-atividade>/   submissions_export(.csv/.xlsx), exam_config_as_is(.csv/.xlsx), raw_*, report
  course_<course_slug>/    course_grades(.csv/.xlsx), raw_grades, report
```

A pasta é nomeada pelo **título** (via `--label`, ou o título do export no Passo 3).
Sem `--label`, o Passo 1 usa o id do assessment.

---

## Notas importantes

- **Token:** a ferramenta nunca cria nem renova tokens. Se o token estiver expirado/
  inválido, os passos de API param com um erro claro (HTTP 401/403). Mete um token
  válido no `.env` manualmente.
- **`derived_score_status`** (no `submissions_export.csv`) é **calculado localmente**
  a partir de `points`/`blockMaxScore` — não é um campo oficial da API.
- **Overflow do export da UI:** perguntas com >5 opções (ex.: preenchimento com muitas
  variantes aceites) transbordam para colunas sem cabeçalho; o importer trata isto e
  preserva tudo em `row_raw`.
- Detalhe técnico de colunas e endpoints: ver [`extractor/README.md`](extractor/README.md).
