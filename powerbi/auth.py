"""Camada de autenticação do Power BI Embedded.

Implementa o fluxo de autenticação de 2 passos (+1 opcional) descoberto via
inspeção do tráfego do iframe Power BI da BMC. Ver `docs/processo_descoberta.md`.
"""

import logging
from typing import Optional

import requests

log = logging.getLogger(__name__)


class PowerBIAuth:
    """Gerencia o fluxo de autenticação de 2 passos do Power BI Embedded da BMC.

    Fluxo:
        1. GET  AWS Gateway BMC         -> EmbedToken (token curto)
        2. GET  modelsAndExploration    -> MWCToken + capacityUri + bootstrap
        +. GET  conceptualschema        -> schema completo (opcional)

    O MWCToken é o que autoriza queries no cluster dedicado do Power BI.
    Verbos:
        - Leituras de metadados/tokens -> GET
        - Execução de query semântica  -> POST (feito no PowerBICrawler.execute)
    """

    AWS_TOKEN_URL = (
        "https://63p7r2qck2.execute-api.us-east-1.amazonaws.com/"
        "Prod/token/{group}/{report}"
    )
    EXPLORATION_URL = (
        "https://wabi-south-central-us-redirect.analysis.windows.net/"
        "explore/reports/{report}/modelsAndExploration"
        "?preferReadOnlySession=true&skipQueryData=true"
    )
    CONCEPTUAL_SCHEMA_URL = (
        "https://wabi-south-central-us-redirect.analysis.windows.net/"
        "explore/reports/{report}/conceptualschema"
        "?userPreferredLocale=pt-BR"
    )

    def __init__(self, group_id: str, report_id: str):
        self.group_id = group_id
        self.report_id = report_id
        self.session = requests.Session()

        self.embed_token: Optional[str] = None
        self.mwc_token: Optional[str] = None
        self.capacity_uri: Optional[str] = None
        self.bootstrap_payload: Optional[dict] = None
        self.conceptual_schema: Optional[dict] = None

    # ------------------------------------------------------------------
    # Passo 1 - EmbedToken
    # ------------------------------------------------------------------
    def fetch_embed_token(self) -> str:
        """GET ao gateway AWS da BMC para obter o EmbedToken inicial."""
        url = self.AWS_TOKEN_URL.format(
            group=self.group_id, report=self.report_id
        )
        log.info("-> [1/3] Solicitando EmbedToken ao gateway BMC (GET)")
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        self.embed_token = resp.json()["Token"]
        log.info("  OK EmbedToken obtido (%d caracteres)", len(self.embed_token))
        return self.embed_token

    # ------------------------------------------------------------------
    # Passo 2 - Bootstrap (MWCToken + capacityUri + metadados)
    # ------------------------------------------------------------------
    def bootstrap(self) -> dict:
        """GET modelsAndExploration.

        Esse endpoint é o coração do fluxo - numa única chamada autenticada
        com o EmbedToken, retorna MWCToken, capacityUri, metadados do modelo
        e bodies de cada visual do report.
        """
        if not self.embed_token:
            raise RuntimeError("Chame fetch_embed_token() antes de bootstrap()")

        url = self.EXPLORATION_URL.format(report=self.report_id)
        headers = {"Authorization": f"EmbedToken {self.embed_token}"}
        log.info("-> [2/3] Carregando modelsAndExploration (GET)")
        resp = self.session.get(url, headers=headers, timeout=30)
        resp.raise_for_status()

        payload = resp.json()
        self.mwc_token = payload["exploration"]["mwcToken"]
        self.capacity_uri = payload["exploration"]["capacityUri"]
        self.bootstrap_payload = payload

        report_meta = payload["exploration"]["report"]
        model_meta = payload["models"][0]
        log.info("  OK MWCToken obtido (%d caracteres)", len(self.mwc_token))
        log.info("  - Dataset:    %s", report_meta["displayName"])
        log.info("  - ModelId:    %d", model_meta["id"])
        log.info("  - DatasetId:  %s", model_meta["dbName"])
        log.info("  - Refresh:    %s", model_meta["LastRefreshTime"])
        return payload

    # ------------------------------------------------------------------
    # Passo opcional - Schema conceitual completo
    # ------------------------------------------------------------------
    def fetch_conceptual_schema(self) -> dict:
        """GET conceptualschema.

        Retorna o schema completo do modelo: todas as tabelas, suas
        colunas, tipos, format strings, relacionamentos e capacidades.
        Usado pelo PowerBISchema para validação tipada das queries.
        """
        if not self.embed_token:
            raise RuntimeError(
                "Chame fetch_embed_token() antes de fetch_conceptual_schema()"
            )

        url = self.CONCEPTUAL_SCHEMA_URL.format(report=self.report_id)
        headers = {"Authorization": f"EmbedToken {self.embed_token}"}
        log.info("-> [opt] Carregando conceptualschema (GET)")
        resp = self.session.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        self.conceptual_schema = resp.json()
        n_entities = len(self.conceptual_schema["schema"]["Entities"])
        log.info("  OK Schema com %d entidades carregado", n_entities)
        return self.conceptual_schema

    # ------------------------------------------------------------------
    # Estado e utilitários
    # ------------------------------------------------------------------
    @property
    def is_authenticated(self) -> bool:
        """True quando os dois tokens necessários estão em mãos."""
        return bool(self.embed_token and self.mwc_token)

    def query_headers(self) -> dict:
        """Headers padrão para chamadas POST ao capacity endpoint."""
        if not self.mwc_token:
            raise RuntimeError("MWCToken ausente. Chame bootstrap() antes.")
        return {
            "Authorization": f"MWCToken {self.mwc_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }