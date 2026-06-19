# BMC Power BI Crawler

Crawler que extrai dados de commodities do dashboard **Power BI Embedded** público
da **Bolsa Mercantil de Colombia (BMC)**, reproduzindo o comportamento do iframe
via engenharia reversa do tráfego de rede — usando **apenas Python + `requests`**,
sem qualquer automação de browser (Selenium/Playwright).

O fluxo completo cabe em três chamadas HTTP:

| Etapa | Endpoint | Verbo | Retorna |
|---|---|---|---|
| 1 | AWS Gateway BMC | GET | `EmbedToken` (abre a sessão) |
| 2 | `modelsAndExploration` | GET | `MWCToken` + `capacityUri` + schema |
| 3 | `{capacityUri}/query` | POST | Dados em formato DSR proprietário |

> A lógica e o passo-a-passo da investigação estão no notebook
> [`notebooks/Desafio_GEP.ipynb`](notebooks/Desafio_GEP.ipynb) e em
> [`docs/processo_descoberta.md`](docs/processo_descoberta.md). O código deste
> repositório é a versão modularizada daquele notebook.

---

## Arquitetura

A solução é organizada em **5 classes**, cada uma com uma responsabilidade única:

| Classe | Módulo | Responsabilidade |
|---|---|---|
| `PowerBIAuth` | [powerbi/auth.py](powerbi/auth.py) | Ciclo de vida dos tokens (EmbedToken → MWCToken) |
| `PowerBISchema` | [powerbi/schema.py](powerbi/schema.py) | Parser tipado do `conceptualschema` |
| `PowerBIQueryBuilder` | [powerbi/query_builder.py](powerbi/query_builder.py) | Construtor declarativo de bodies `SemanticQueryDataShapeCommand` |
| `DSRParser` | [powerbi/dsr_parser.py](powerbi/dsr_parser.py) | Decoder do formato DSR em `pandas.DataFrame` |
| `PowerBICrawler` | [crawlers/bmc.py](crawlers/bmc.py) | Orquestra o pipeline (auth → schema → execute → parse) |

O ponto de entrada é [main.py](main.py), que materializa o caso de uso específico
do desafio (decisões editoriais: tabela, colunas, filtros, ordenação).

```
scrapper/
├── main.py                 # pipeline executável (passos 1–5)
├── requirements.txt
├── powerbi/                # classes genéricas do protocolo Power BI
│   ├── auth.py
│   ├── schema.py
│   ├── query_builder.py
│   └── dsr_parser.py
├── crawlers/
│   └── bmc.py              # orquestrador + IDs do dashboard BMC
├── notebooks/
│   └── Desafio_GEP.ipynb   # versão narrativa original
├── docs/
│   └── processo_descoberta.md
├── logs/                   # criada na execução (1 arquivo por run)
└── output/                 # criada na execução (CSVs gerados)
```

---

## Como executar

### 1. Instalar o Python

É necessário **Python 3.9 ou superior**. Verifique se já existe:

```bash
python --version
```

Se não estiver instalado, baixe em <https://www.python.org/downloads/>.

- **Windows**: ao instalar, marque a opção **"Add Python to PATH"**.
- **macOS / Linux**: normalmente já vem instalado, ou use `brew install python` /
  o gerenciador de pacotes da distro.

### 2. Criar e ativar o ambiente virtual (venv)

A partir da **raiz do projeto** (a pasta `scrapper`):

```bash
# criar o ambiente
python -m venv .venv
```

Ativar o ambiente:

```powershell
# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1
```

```cmd
:: Windows (cmd)
.\.venv\Scripts\activate.bat
```

```bash
# macOS / Linux
source .venv/bin/activate
```

> **PowerShell bloqueando o script de ativação?** Rode uma vez:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`
> e ative novamente.

Com o venv ativo, o prompt do terminal passa a mostrar `(.venv)` no início.

### 3. Instalar as dependências

```bash
pip install -r requirements.txt
```

São apenas duas: `requests` (cliente HTTP) e `pandas` (manipulação tabular).

### 4. Executar o crawler

```bash
python main.py
```

A execução roda o pipeline completo de ponta a ponta:

| Passo | O que faz |
|---|---|
| 1 | Autentica e carrega o schema do modelo |
| 2 | Imprime o schema descoberto (tabelas, colunas, relacionamentos) |
| 3 | Query principal dos produtos do desafio → `output/precios_2025.csv` |
| 4 | Dataset completo via paginação com throttle → `output/dataset_completo.csv` |
| 5 | Resume os arquivos gerados |

> O crawler depende do dashboard público da BMC estar **acessível e online**
> no momento da execução. É necessária conexão com a internet.

---

## Saídas

### Logs

A cada execução é criada (se não existir) a pasta **`logs/`** e gravado um arquivo
nomeado com timestamp, por exemplo:

```
logs/crawler_20260619_143025.log
```

Todo o progresso de cada etapa é gravado nesse arquivo **e** ecoado no console
simultaneamente.

### CSVs

Os arquivos de dados vão para a pasta **`output/`** (criada na execução):

| Arquivo | Conteúdo |
|---|---|
| `output/precios_2025.csv` | Entrega principal (2 produtos, layout `Referencia, Data, Valor`) |
| `output/dataset_completo.csv` | Dataset completo paginado, sem agregação |

---

## Apontar para outro dashboard

O crawler é genérico. Para usá-lo contra outro report Power BI Embedded com
fluxo de autenticação similar, basta trocar os IDs em
[crawlers/bmc.py](crawlers/bmc.py) (`BMC_GROUP_ID` / `BMC_REPORT_ID`) — ou
instanciar `PowerBICrawler(group_id=..., report_id=...)` diretamente. Nenhuma
alteração nas classes do pacote `powerbi` é necessária.

---

## Observações sobre a fonte (adaptações documentadas)

O link original do PDF (`http://bolsamercantil.com.co/analitica`) não funciona
mais, e o report atualmente público tem schema diferente do descrito no desafio:
**não há coluna `Departamento` nem granularidade mensal**, e a métrica
"Promedio año actual" não existe no modelo. As adaptações feitas (uso de
`Año inicial` como referência temporal anual e `Sum(Valor Registrado)` como
métrica) estão registradas em detalhe em
[`docs/processo_descoberta.md`](docs/processo_descoberta.md).