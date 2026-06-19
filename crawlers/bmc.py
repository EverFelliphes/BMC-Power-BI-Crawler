"""Orquestrador do crawler Power BI Embedded.

O `PowerBICrawler` combina `PowerBIAuth`, `PowerBISchema`, `PowerBIQueryBuilder`
e `DSRParser` em um pipeline coerente. Não conhece o domínio do dashboard -
apenas executa o protocolo (auth -> schema -> execute -> parse).

Constantes específicas do dashboard da BMC ficam no fim deste módulo, mas as
decisões editoriais (quais colunas, filtros, ordenação) vivem no `main.py`.
"""

import logging
import random
import time
from typing import Optional

import pandas as pd

from powerbi import (
    DSRParser,
    PowerBIAuth,
    PowerBIQueryBuilder,
    PowerBISchema,
)

log = logging.getLogger(__name__)


class PowerBICrawler:
    """Crawler genérico para reports Power BI Embedded públicos.

    Não conhece o domínio do dashboard - apenas executa o pipeline:
        1. Autentica (EmbedToken -> MWCToken via modelsAndExploration)
        2. Carrega schema do modelo
        3. Executa queries arbitrárias (com ou sem paginação)
        4. Devolve responses crus (parse fica a cargo do chamador)

    Para usar contra outro dashboard, basta instanciar com group_id
    e report_id diferentes - nenhuma alteração de código necessária.
    """

    def __init__(self, group_id: str, report_id: str):
        self.group_id = group_id
        self.report_id = report_id
        self.auth = PowerBIAuth(group_id, report_id)
        self.schema: Optional[PowerBISchema] = None
        self.builder: Optional[PowerBIQueryBuilder] = None

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    def setup(self) -> None:
        """Executa autenticação completa e carrega schema do modelo."""
        self.auth.fetch_embed_token()
        bootstrap = self.auth.bootstrap()
        self.auth.fetch_conceptual_schema()

        model_meta = bootstrap["models"][0]
        self.schema = PowerBISchema(
            conceptual_schema=self.auth.conceptual_schema,
            model_id=model_meta["id"],
            dataset_id=model_meta["dbName"],
        )
        self.builder = PowerBIQueryBuilder(
            dataset_id=self.schema.dataset_id,
            report_id=self.report_id,
            schema=self.schema,
            model_id=self.schema.model_id,
        )
        log.info("Setup concluído")

    @property
    def is_ready(self) -> bool:
        """True após setup() bem-sucedido."""
        return (self.auth.is_authenticated
                and self.schema is not None
                and self.builder is not None)

    # ------------------------------------------------------------------
    # Execução de queries
    # ------------------------------------------------------------------
    def execute(self, body: dict) -> dict:
        """POST contra o capacity endpoint. Devolve JSON cru."""
        if not self.is_ready:
            raise RuntimeError("Crawler não inicializado. Chame setup() antes.")

        url = f"{self.auth.capacity_uri}query"
        log.info("-> POST capacity endpoint")
        resp = self.auth.session.post(
            url,
            headers=self.auth.query_headers(),
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        log.info("  OK Resposta recebida (%d bytes)", len(resp.content))
        return resp.json()

    @staticmethod
    def _extract_restart_tokens(response: dict) -> Optional[list]:
        """Extrai o RT (RestartTokens) do response, se presente."""
        try:
            ds = response["results"][0]["result"]["data"]["dsr"]["DS"][0]
            rt = ds.get("RT")
            if rt and isinstance(rt, list) and len(rt) > 0:
                return rt
            return None
        except (KeyError, IndexError):
            return None

    def execute_paginated(
        self,
        build_body_fn,
        window: int = 500,
        max_pages: int = 10,
        delay_min: float = 1.5,
        delay_max: float = 3.5,
    ) -> pd.DataFrame:
        """Executa uma query paginada, juntando todas as páginas num só DF.

        Args:
            build_body_fn: função que recebe `restart_tokens` (lista ou None)
                e retorna o body completo. Permite reusar a mesma query
                com tokens diferentes a cada página.
            window: tamanho de cada página (linhas).
            max_pages: limite de segurança para evitar loop infinito.
            delay_min: tempo mínimo (segundos) entre páginas.
            delay_max: tempo máximo (segundos) entre páginas.
        """
        if delay_max < delay_min:
            raise ValueError("delay_max deve ser >= delay_min")

        all_dfs = []
        restart_tokens = None

        for page_num in range(1, max_pages + 1):
            log.info("-" * 50)
            log.info("Página %d (window=%d, RT=%s)",
                     page_num, window,
                     "presente" if restart_tokens else "início")

            body = build_body_fn(restart_tokens)
            response = self.execute(body)

            df_page = DSRParser.parse(response)
            log.info("  - %d linhas nesta página", len(df_page))

            if len(df_page) == 0:
                log.info("  - Página vazia - encerrando")
                break

            all_dfs.append(df_page)

            restart_tokens = self._extract_restart_tokens(response)
            if restart_tokens is None:
                log.info("  - Sem RT no response - última página")
                break

            if page_num < max_pages:
                delay = random.uniform(delay_min, delay_max)
                log.info("  - Aguardando %.2fs antes da próxima página "
                         "(intervalo [%.1f, %.1f])",
                         delay, delay_min, delay_max)
                time.sleep(delay)

        if not all_dfs:
            return pd.DataFrame()

        combined = pd.concat(all_dfs, ignore_index=True)
        log.info("=" * 50)
        log.info("Paginação concluída: %d páginas, %d linhas totais",
                 len(all_dfs), len(combined))
        return combined


# ----------------------------------------------------------------------
# Constantes do dashboard alvo (BMC - Bolsa Mercantil de Colombia)
# Extraídas da URL do iframe Power BI embarcado na página /analitica.
# Trocar esses dois IDs aponta o crawler a outro report Embedded.
# ----------------------------------------------------------------------
BMC_GROUP_ID = "11411183-c06e-4690-9537-67a40c1df2ca"
BMC_REPORT_ID = "2b6bf89d-a4b8-4959-8452-895edee3bc21"