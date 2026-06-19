"""Pacote `powerbi` - camadas genéricas do protocolo Power BI Embedded.

Expõe as quatro classes reutilizáveis que compõem o pipeline:
    PowerBIAuth        - ciclo de vida dos tokens (EmbedToken/MWCToken)
    PowerBISchema      - parser tipado do conceptualschema
    PowerBIQueryBuilder- construtor declarativo de bodies semânticos
    DSRParser          - decoder do formato DSR em pandas.DataFrame
"""

from .auth import PowerBIAuth
from .schema import (
    DATA_TYPES,
    DEFAULT_AGG,
    Entity,
    PowerBISchema,
    Property,
    Relationship,
)
from .query_builder import PowerBIQueryBuilder
from .dsr_parser import DSRParser

__all__ = [
    "PowerBIAuth",
    "PowerBISchema",
    "PowerBIQueryBuilder",
    "DSRParser",
    "Entity",
    "Property",
    "Relationship",
    "DATA_TYPES",
    "DEFAULT_AGG",
]