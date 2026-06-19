# Processo de Descoberta

**Desafio Técnico — BMC Power BI Crawler**
**Engenheiro de Dados Junior**

Este documento registra o caminho real que percorri para entender o problema
proposto pelo desafio, antes de escrever uma única linha do crawler. O foco
é a investigação: o que esperava encontrar, o que de fato encontrei, e como
cada constatação orientou as decisões técnicas que vieram depois.

---

## Ponto de partida

O PDF do desafio orienta:

- Acessar `http://bolsamercantil.com.co/analitica`
- Extrair o KPI **"Promedio año actual"**
- Operar sobre a tabela *"Consulte nuestros precios de referencia por
  categoría, producto, departamento y rangos de fecha"*, aba *Precios Indicativos*
- Iterar mês a mês ao longo de 2025
- Filtrar Departamento = "Nacional" e produtos `Azucar Blanco` e
  `Maiz Amarillo Nacional`

A expectativa, dada a clareza da descrição, era de uma página com filtros
visuais (datas, departamento, produto) e um cartão exibindo o KPI alvo.
Bastaria inspecionar o tráfego de rede para descobrir como o cartão é
preenchido e replicar isso via `requests`.

A realidade exigiu muito mais investigação.

---

## Primeira surpresa: o link do PDF não funciona

A primeira ação foi simples: abrir a URL fornecida no desafio.
`http://bolsamercantil.com.co/analitica` **não respondeu corretamente**.
Não houve resolução transparente para HTTPS nem redirecionamento útil. A
URL parece não corresponder mais a um endpoint estável do site da BMC.

Foi necessário **pesquisar manualmente na web** para localizar onde a
página de Analítica da BMC reside atualmente. O endereço atual é
`https://www.bolsamercantil.com.co/analitica`.

Esse foi o primeiro indício de que a fonte descrita no desafio passou por
mudanças desde a sua redação. Registrei essa observação cedo no processo
porque ela ajudou a enquadrar tudo o que veio depois.

---

## Inspeção da página atual

Ao carregar a página atual, esperava encontrar múltiplos relatórios ou
abas, com uma claramente nomeada "Precios Indicativos" conforme o desafio
indica.

O que encontrei:

- **Um único iframe Power BI Embedded** na página
- O título interno do report é `BMC_catálogo_datos` — **não** "Precios
  Indicativos"
- Dentro do report há **uma página com dois cards** de navegação:
  *"Productos para venta de data"* e *"Productos y variedad para la venta"*
- Nenhuma menção visual a "Promedio año actual", "Departamento" ou ao
  filtro mensal descrito no desafio

A divergência entre o que o PDF descreve e o que a página atual oferece é
ampla — não é uma simples renomeação. A estrutura inteira do report parece
ter sido reorganizada.

Antes de assumir que o report alvo simplesmente sumiu, decidi mapear
sistematicamente tudo o que estava disponível. Se eu fosse concluir
"a fonte mudou", precisaria de evidência forte.

---

## Capturando o tráfego do iframe

Abri o DevTools do navegador na aba Network e recarreguei a página. Filtrando
por chamadas vindas do iframe, três endpoints chamaram atenção:

| URL (resumida) | Verbo | Função aparente |
|---|---|---|
| `63p7r2qck2.execute-api.us-east-1.amazonaws.com/Prod/token/...` | GET | Retorna um token JSON |
| `wabi-south-central-us-redirect.analysis.windows.net/.../modelsAndExploration` | GET | Retorna o "bootstrap" do iframe |
| `[capacityUri]/query` | POST | Retorna dados de visuais |

A primeira URL é claramente um gateway AWS operado pela própria BMC — não
é da Microsoft. A presença desse gateway é o que torna o dashboard acessível
sem credenciais Microsoft: a BMC absorve a autenticação e devolve um token
de embed pronto.

Capturei e inspecionei o conteúdo desses três responses:

### Response do gateway AWS

Retorna um objeto JSON com um campo `Token` — uma string longa que parece
ser um JWT. Esse é o **EmbedToken**.

### Response do `modelsAndExploration`

Esse foi o response mais revelador. Em uma única chamada autenticada com
o EmbedToken, ele entrega:

- `exploration.mwcToken` — o token que falta para fazer queries reais
- `exploration.capacityUri` — a URL do endpoint de query dedicado
- `models[0].id` e `models[0].dbName` — IDs internos do modelo semântico
- `exploration.report.displayName` — `BMC_catálogo_datos`
- `exploration.sections[]` — todas as páginas e visuais do report, com seus
  bodies de query pré-montados

A descoberta importante aqui foi entender que **o iframe se inicializa em
uma única chamada**. Não precisa de várias requisições, polling ou estado
mantido — uma chamada bem feita retorna tudo o que é preciso para começar
a executar queries.

### Response do endpoint `/query`

Retorna dados em um formato JSON denso, com campos `DM0`, `PH`, `DS`,
`ValueDicts`, `R` (bitmask), e outros que à primeira vista não fazem
sentido. Esse é o formato **DSR (Data Shape Result)**. Documentação
pública sobre ele é escassa.

---

## Verbos HTTP: a primeira pegadinha

Tentei replicar as chamadas usando `requests`. As duas primeiras
(EmbedToken e `modelsAndExploration`) foram via GET sem problema. Quando
fui buscar o `conceptualschema`, intuitivamente usei POST — porque pensei
"se vou buscar metadados ricos, deve ser POST com payload".

Recebi um `405 Method Not Allowed`.

O Power BI Embedded é estrito quanto a verbos: leituras de metadados são
**sempre** GET (com parâmetros na URL); apenas execução de query usa POST.
Confundir os dois resulta em erro imediato, o que ajuda a falhar rápido.
Anotei essa convenção e corrigi.

---

## Mapeamento do schema descoberto

Com o `modelsAndExploration` e o `conceptualschema` capturados, pude
montar a visão completa do modelo semântico:

**Três tabelas:**

| Tabela | Colunas-chave |
|---|---|
| `Dim_cifras_productos` | `Producto`, `Valor Registrado`, `Facturas`, `Registros`, `Cantidades`, `Empresas Compradoras`, `Empresas Vendedoras`, `Año inicial` |
| `Dim_cifras_producto&variedad` | `ID`, `Producto - Variedad - U. Medida - Empaque - Naturaleza`, mesmas métricas + `Grupo`, `desc_*` |
| `Dim_variedades_frecuencia` | `ID`, `Frecuencia`, `Año inicial`, mesmas descrições |

**Relacionamento:** as duas últimas tabelas estão ligadas 1:1 por `ID`. A
primeira é independente.

**Detalhes técnicos:**

- `Producto` é uma coluna texto com `DefaultAggregate: 1` (None, dimensão)
- `Valor Registrado` é numérico com `DefaultAggregate: 2` (Sum, medida)
- `Año inicial` é numérico — seus valores observados são números de 4 dígitos
  como `2010`, `2024`, indicando granularidade anual

---

## A constatação difícil

Comparando o schema disponível com o que o desafio pede:

| O que o PDF pede | O que o modelo tem |
|---|---|
| Coluna `Departamento` ("Nacional") | **Não existe em nenhuma das três tabelas** |
| Granularidade mensal de datas (`01/01/2025` a `31/01/2025`) | **Não há coluna de data. Só `Año inicial` (anual)** |
| KPI `Promedio año actual` | **Métrica não existe no schema** |
| Tabela "Precios Indicativos" | **Tabela com esse nome não existe** |

Não é uma divergência cosmética. A estrutura inteira do report que o
desafio descreve **não está mais publicamente acessível** no endereço
indicado.

Antes de assumir essa conclusão como definitiva, fiz uma checagem
sistemática.

---

## Validação da hipótese "o report mudou"

### Checagem 1: a página tem outros iframes?

Listei pela DOM da página atual todos os elementos `iframe`, `embed` e
`object`. Resultado: existe **um único iframe**, e ele aponta para o
report `2b6bf89d-a4b8-4959-8452-895edee3bc21`. Não há embed escondido.

### Checagem 2: o report atual tem páginas escondidas?

O response do `modelsAndExploration` traz a lista completa de `sections`
do report, com flag de visibilidade. Cinco páginas existem; uma delas
(`Portada`) está marcada como `visibility: 0` (oculta). Inspecionei
mesmo assim — contém apenas elementos decorativos, nenhum visual com dados
relevantes para o desafio.

### Checagem 3: outras páginas do site BMC têm o report alvo?

Verifiquei `/informacion-de-mercado`, `/estudios-economicos` e `/mercop`.
Nenhuma contém embed Power BI relacionado a preços indicativos.

### Checagem 4: o report alvo poderia estar atrás de login?

Considerei a possibilidade. Descartei porque:

- O PDF não menciona credenciais
- O cadastro na BMC requer vínculo comercial (corretora/comissionista),
  incompatível com escopo de desafio técnico
- O KPI envolve dados de commodities tradicionalmente públicos

### Checagem 5: existe pista temporal de quando o report atual foi publicado?

Sim. O `LastRefreshTime` do dataset, presente no response do
`modelsAndExploration`, é `2025-06-09`. Há também um textbox interno no
dashboard com a marcação "V 2025.06.09". Ambos sugerem que o report atual
foi publicado ou atualizado em junho de 2025.

### Checagem 6: o próprio PDF dá pistas?

Sim. O documento carrega o aviso textual:

> "o valor 'Promedio año actual' pode não aparecer no cartão dependendo do
> período configurado"

Esse aviso sugere que mesmo no momento da redação do desafio, o cartão
era instável. Combinado com o link quebrado e a reorganização datada de
junho/2025, a hipótese de que a fonte passou por mudanças significativas
entre a redação do desafio e o momento da execução fica fortemente
sustentada.

---

## Decisões técnicas tomadas a partir das constatações

### Decisão 1: implementar o fluxo completo de auth, mesmo sem o report alvo

A engenharia reversa do protocolo Power BI Embedded **é o conteúdo
principal avaliado pelo desafio**, conforme o próprio PDF declara:

> "O teste avalia sua capacidade de inspecionar o tráfego de rede,
> identificar endpoints, headers e realizar a engenharia reversa do payload."

Implementar esse fluxo de ponta a ponta, mesmo contra um report
parcialmente equivalente, demonstra exatamente a competência que o
exercício avalia. Decidi codificar o pipeline completo (EmbedToken →
MWCToken → query) com fidelidade aos bodies reais capturados.

### Decisão 2: usar a tabela mais próxima do espírito do desafio

`Dim_cifras_productos` é a tabela disponível mais alinhada ao caso de uso:

- Tem `Producto` como dimensão direta (os filtros do desafio são por nome)
- Tem `Valor Registrado` como métrica monetária agregável
- Não requer JOIN com outras tabelas para o uso básico

As outras duas tabelas existem no modelo e podem ser exploradas, mas a
entrega principal opera sobre `Dim_cifras_productos`.

### Decisão 3: parser DSR implementado manualmente

O formato DSR não é documentado em recursos públicos amplamente acessíveis.
Não encontrei uma biblioteca Python amplamente adotada e mantida para esse
parser. Implementar isso manualmente foi necessário e — também — comunica
domínio do protocolo, em vez de dependência de magia externa.

Os três mecanismos do DSR (dicionário de strings em `ValueDicts`, bitmask
de repetição em `R`, schema posicional em `S`) foram entendidos a partir
de inspeção sistemática de responses reais capturados durante a navegação
no dashboard.

### Decisão 4: builder genérico, casos de uso na execução

O construtor de queries (`PowerBIQueryBuilder`) não conhece o domínio BMC.
Ele só sabe montar bodies semânticos válidos a partir de primitivas
declarativas (`column`, `sum`, `where_in`, `order_by`). Toda decisão
editorial sobre o caso de uso do desafio — qual tabela atacar, quais
colunas selecionar, qual filtro aplicar — fica explícita na célula de
execução do notebook, onde é auditável.

### Decisão 5: parametrização que sobrevive a mudança de fonte

`GROUP_ID` e `REPORT_ID` são constantes no topo da execução. O crawler
foi projetado para ser apontado a outro dashboard Power BI Embedded com
fluxo de autenticação similar trocando apenas esses dois valores. Se o
report descrito no PDF for republicado em outro endereço, a adaptação é
de configuração, não de refatoração.

### Decisão 6: adaptação documentada do CSV final

O layout `Referencia, Data, Valor` do desafio é mantido exatamente. As
adaptações de conteúdo foram:

- **Granularidade temporal**: o modelo opera em granularidade anual, não
  mensal. O CSV traz `01/01/2025` como marcador do ano de referência, em
  vez de 12 linhas mensais por produto
- **Métrica**: a coluna `Valor` traz `Sum(Valor Registrado)` — a métrica
  monetária agregada mais próxima do KPI "Promedio año actual" original,
  já que esse último não existe no schema atual

Replicar artificialmente 12 linhas idênticas (uma por mês) com o mesmo
valor anual seria tecnicamente incorreto. Foi descartado por priorizar
honestidade técnica sobre estética de entrega.

---

## O que ficou no notebook entregue

O notebook materializa todas as decisões acima em uma sequência narrativa
de 5 classes + 5 passos de execução.

### Cinco classes (arquitetura em camadas)

| Classe | Responsabilidade |
|---|---|
| `PowerBIAuth` | Gerencia o ciclo de vida de EmbedToken e MWCToken |
| `PowerBISchema` | Parseia o `conceptualschema` em estruturas tipadas (`Entity`, `Property`, `Relationship`) |
| `PowerBIQueryBuilder` | Constrói payloads `SemanticQueryDataShapeCommand` declarativamente |
| `DSRParser` | Decodifica o formato DSR (3 mecanismos) em `pandas.DataFrame` |
| `PowerBICrawler` | Orquestra o pipeline (auth → schema → execute → parse), sem casos de uso específicos |

A separação por eixo de mudança — auth muda por motivos diferentes de
schema, que muda por motivos diferentes do parser — significa que uma
alteração em uma área não cascateia nas outras.

### Cinco passos de execução

| Passo | O que faz |
|---|---|
| 1 | Setup do crawler (autentica + carrega schema) |
| 2 | Inspeção do schema descoberto (impresso em formato legível) |
| 3 | Query principal: produtos do desafio, com agregação |
| 4 | Bônus: dataset completo via paginação com throttle aleatório |
| 5 | Download dos CSVs gerados |

Cada passo é uma célula markdown (contextualiza) seguida de uma célula de
código (executa). O notebook se lê do topo ao fim como um relatório
técnico, com a lógica de cada decisão visível no momento em que é
aplicada.

### Recursos técnicos demonstrados

- **Fluxo de autenticação Power BI Embedded mapeado e implementado**:
  três chamadas (AWS Gateway, modelsAndExploration, query), com verbos
  HTTP corretos
- **Parser DSR custom**: aplica os três mecanismos do formato proprietário
- **Schema tipado com validação**: o builder valida nomes de tabela antes
  de gerar o body, evitando erros 400 silenciosos do servidor
- **Builder declarativo**: bodies de centenas de linhas de JSON aninhado
  viram poucas linhas legíveis de Python
- **Paginação via RestartTokens**: implementa o mecanismo nativo do Power
  BI para datasets grandes
- **Throttle aleatório entre requisições**: intervalo sorteado
  uniformemente em vez de fixo
- **Stack mínima**: apenas `requests` e `pandas`

---

## Encerramento

A divergência entre o desafio (que descrevia um report "Precios
Indicativos" com KPI mensal e filtro por departamento) e a fonte
atualmente disponível (que expõe apenas o "Catálogo de Datos" com
granularidade anual e sem departamento) é um cenário realista em
manutenção de crawlers: **fontes mudam**. O que diferencia uma boa
entrega nesse cenário é a capacidade de investigar com método, documentar
com evidência, adaptar sem perder rigor, e estruturar o código para que
uma futura mudança de fonte seja uma operação de configuração — não de
refatoração.

Foi com essa orientação que estruturei a solução.
