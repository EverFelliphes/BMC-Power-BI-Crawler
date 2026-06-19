"""Execução do pipeline do desafio BMC Power BI Crawler.

Materializa o caso de uso específico do desafio (ver notebook
`notebooks/Desafio_GEP.ipynb`):

    Passo 1 - Setup do crawler (auth + schema)
    Passo 2 - Inspeção do schema descoberto
    Passo 3 - Query principal: produtos do desafio -> precios_2025.csv
    Passo 4 - Bônus: dataset completo paginado -> dataset_completo.csv
    Passo 5 - Resumo dos arquivos gerados

Toda decisão editorial (qual tabela, quais colunas, filtros e ordenação)
fica explícita aqui, não escondida nas classes do pipeline.

Logs são gravados em `logs/` (um arquivo por execução, com timestamp) e
também ecoados no console. Os CSVs vão para `output/`.
"""

import logging
import os
from datetime import datetime

import pandas as pd

from crawlers import BMC_GROUP_ID, BMC_REPORT_ID, PowerBICrawler
from powerbi import DSRParser

# ----------------------------------------------------------------------
# Constantes / decisões editoriais do desafio
# ----------------------------------------------------------------------
GROUP_ID = BMC_GROUP_ID
REPORT_ID = BMC_REPORT_ID
PRODUTOS_ALVO = ["Azucar Blanco", "Maiz Amarillo Nacional Seco"]
TARGET_TABLE = "Dim_cifras_productos"

# Diretórios de saída (relativos à raiz do projeto)
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT_DIR, "logs")
OUTPUT_DIR = os.path.join(ROOT_DIR, "output")

log = logging.getLogger("bmc_crawler")


def setup_logging() -> str:
    """Configura logging para console + arquivo em logs/.

    Cria a pasta `logs/` se não existir e grava um arquivo por execução,
    nomeado com timestamp. Retorna o caminho do arquivo de log.
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, f"crawler_{timestamp}.log")

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Evita handlers duplicados em re-execuções no mesmo processo
    root.handlers.clear()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    return log_path


def passo_1_setup() -> PowerBICrawler:
    log.info("=" * 60)
    log.info("PASSO 1 - Inicialização")
    log.info("=" * 60)

    crawler = PowerBICrawler(group_id=GROUP_ID, report_id=REPORT_ID)
    crawler.setup()
    return crawler


def passo_2_schema(crawler: PowerBICrawler) -> None:
    log.info("=" * 60)
    log.info("PASSO 2 - Schema descoberto")
    log.info("=" * 60)

    # Visão geral das tabelas e relacionamentos
    log.info("\n%s", crawler.schema.summary())
    # Detalhe da tabela que vamos usar
    log.info("%s", crawler.schema.describe_table(TARGET_TABLE))


def passo_3_query_desafio(crawler: PowerBICrawler) -> pd.DataFrame:
    log.info("=" * 60)
    log.info("PASSO 3 - Query: produtos do desafio (%s)", PRODUTOS_ALVO)
    log.info("=" * 60)

    qb = crawler.builder

    # Monta a query declarativamente
    selects_desafio = [
        qb.column("d", "Producto"),
        qb.sum("d", "Valor Registrado", "Valor"),
        qb.sum("d", "Facturas"),
        qb.sum("d", "Cantidades"),
        qb.sum("d", "Empresas Compradoras", "Compradoras"),
        qb.sum("d", "Empresas Vendedoras", "Vendedoras"),
        qb.column("d", "Año inicial"),
    ]

    body_desafio = qb.build(
        tables=[("d", TARGET_TABLE)],
        selects=selects_desafio,
        filters=[qb.where_in("d", "Producto", PRODUTOS_ALVO)],
        order_by=qb.order_by(selects_desafio[1]),  # Sum(Valor Registrado) DESC
    )

    # Executa e parseia
    response = crawler.execute(body_desafio)
    df_target = DSRParser.parse(response)

    log.info("\n=== Dados brutos extraídos ===\n%s", df_target.to_string())

    # Adaptação para o layout do PDF: Referencia, Data, Valor
    df_csv = pd.DataFrame({
        "Referencia": df_target["d.Producto"],
        "Data": "01/01/2025",
        "Valor": df_target["Sum(d.Valor Registrado)"],
    })
    log.info("\n=== CSV final no layout do desafio ===\n%s", df_csv.to_string())

    out_path = os.path.join(OUTPUT_DIR, "precios_2025.csv")
    df_csv.to_csv(out_path, index=False)
    log.info("OK CSV salvo: %s (%d linhas)", out_path, len(df_csv))
    return df_csv


def passo_4_dataset_completo(crawler: PowerBICrawler) -> pd.DataFrame:
    log.info("=" * 60)
    log.info("PASSO 4 - Dataset completo (paginado com throttle aleatório)")
    log.info("=" * 60)

    qb = crawler.builder

    def build_paginated_body(restart_tokens):
        """Closure que reusa a mesma query com diferentes RTs a cada página."""
        selects = [
            qb.column("d", "Producto"),
            qb.column("d", "Valor Registrado", "Valor"),
            qb.column("d", "Año inicial"),
        ]
        return qb.build(
            tables=[("d", TARGET_TABLE)],
            selects=selects,
            order_by=qb.order_by(selects[1]),
            window=500,
            restart_tokens=restart_tokens,
        )

    df_all = crawler.execute_paginated(
        build_body_fn=build_paginated_body,
        window=500,
        max_pages=5,
        delay_min=1.5,
        delay_max=3.5,
    )

    log.info("\n=== Dataset completo: %d linhas ===\n%s",
             len(df_all), df_all.head(10).to_string())

    out_path = os.path.join(OUTPUT_DIR, "dataset_completo.csv")
    df_all.to_csv(out_path, index=False)
    log.info("OK Dataset completo salvo: %s (%d linhas)", out_path, len(df_all))
    return df_all


def passo_5_resumo() -> None:
    log.info("=" * 60)
    log.info("PASSO 5 - Arquivos gerados")
    log.info("=" * 60)
    for arquivo in ("precios_2025.csv", "dataset_completo.csv"):
        caminho = os.path.join(OUTPUT_DIR, arquivo)
        if os.path.exists(caminho):
            log.info("  - %s", os.path.abspath(caminho))


def main() -> None:
    log_path = setup_logging()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log.info("Logs desta execução: %s", os.path.abspath(log_path))

    crawler = passo_1_setup()
    passo_2_schema(crawler)
    passo_3_query_desafio(crawler)
    passo_4_dataset_completo(crawler)
    passo_5_resumo()

    log.info("=" * 60)
    log.info("OK Pipeline completo")
    log.info("=" * 60)


if __name__ == "__main__":
    main()