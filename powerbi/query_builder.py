"""Construtor declarativo de queries `SemanticQueryDataShapeCommand`.

Decompõe os bodies de query do Power BI Embedded em fragmentos primitivos
(coluna, agregação, filtro, ordenação) compostos pelo método `build()`.
O builder é genérico - não conhece o domínio do dashboard.
"""

import logging
from typing import Optional

from .schema import PowerBISchema

log = logging.getLogger(__name__)


class PowerBIQueryBuilder:
    """Constrói payloads `SemanticQueryDataShapeCommand` declarativamente.

    Cada método estático retorna um fragmento JSON reutilizável. O método
    `build()` compõe esses fragmentos no envelope completo pronto para POST.

    Os fragmentos seguem fielmente a estrutura observada nos bodies reais
    capturados via inspeção do tráfego do iframe.

    Suporta:
      - Múltiplas tabelas no From (com JOIN implícito via relacionamentos)
      - Agregações (Sum, Avg, CountNonNull, Min, Max) e colunas puras
      - Filtros WHERE (IN, equals)
      - Ordenação ASC/DESC
      - Paginação via RestartTokens
      - Visuais sem paginação (cards de KPI) via `window=None`
    """

    # Códigos de função de agregação observados nos bodies reais
    AGG_SUM = 0
    AGG_AVG = 1
    AGG_COUNT_NON_NULL = 2
    AGG_MIN = 3
    AGG_MAX = 4

    # Direções de ordenação
    ORDER_ASC = 1
    ORDER_DESC = 2

    def __init__(self, dataset_id: str, report_id: str,
                 schema: Optional[PowerBISchema] = None,
                 model_id: Optional[int] = None):
        self.dataset_id = dataset_id
        self.report_id = report_id
        self.schema = schema
        self.model_id = model_id or (schema.model_id if schema else None)

    # ----------------------- BLOCOS FROM ---------------------------

    @staticmethod
    def from_tables(tables: list) -> list:
        """Cláusula From a partir de lista de tuplas (alias, entity_name).

        Exemplos:
            from_tables([("d", "Dim_cifras_productos")])
            from_tables([("d", "Dim_cifras_producto&variedad"),
                         ("d1", "Dim_variedades_frecuencia")])
        """
        return [
            {"Name": alias, "Entity": entity, "Type": 0}
            for alias, entity in tables
        ]

    # ----------------------- BLOCOS SELECT -------------------------

    @staticmethod
    def column(alias: str, prop: str,
               display: Optional[str] = None) -> dict:
        """Coluna pura (sem agregação) - para group by / dimensão."""
        return {
            "Column": {
                "Expression": {"SourceRef": {"Source": alias}},
                "Property": prop,
            },
            "Name": f"{alias}.{prop}",
            "NativeReferenceName": display or prop,
        }

    @classmethod
    def sum(cls, alias: str, prop: str,
            display: Optional[str] = None) -> dict:
        return cls._aggregation(alias, prop, cls.AGG_SUM, "Sum", display)

    @classmethod
    def avg(cls, alias: str, prop: str,
            display: Optional[str] = None) -> dict:
        return cls._aggregation(alias, prop, cls.AGG_AVG, "Avg", display)

    @classmethod
    def count_non_null(cls, alias: str, prop: str,
                       display: Optional[str] = None) -> dict:
        return cls._aggregation(
            alias, prop, cls.AGG_COUNT_NON_NULL, "CountNonNull", display
        )

    @classmethod
    def min(cls, alias: str, prop: str,
            display: Optional[str] = None) -> dict:
        return cls._aggregation(alias, prop, cls.AGG_MIN, "Min", display)

    @classmethod
    def max(cls, alias: str, prop: str,
            display: Optional[str] = None) -> dict:
        return cls._aggregation(alias, prop, cls.AGG_MAX, "Max", display)

    @staticmethod
    def _aggregation(alias: str, prop: str, function: int,
                     fn_name: str, display: Optional[str]) -> dict:
        return {
            "Aggregation": {
                "Expression": {
                    "Column": {
                        "Expression": {"SourceRef": {"Source": alias}},
                        "Property": prop,
                    }
                },
                "Function": function,
            },
            "Name": f"{fn_name}({alias}.{prop})",
            "NativeReferenceName": display or prop,
        }

    # ----------------------- BLOCOS WHERE --------------------------

    @staticmethod
    def where_in(alias: str, prop: str, values: list) -> dict:
        """WHERE coluna IN (...). Valores são tratados como literais string."""
        return {
            "Condition": {
                "In": {
                    "Expressions": [{
                        "Column": {
                            "Expression": {"SourceRef": {"Source": alias}},
                            "Property": prop,
                        }
                    }],
                    "Values": [
                        [{"Literal": {"Value": f"'{v}'"}}]
                        for v in values
                    ],
                }
            }
        }

    @staticmethod
    def where_equals(alias: str, prop: str, value) -> dict:
        """WHERE coluna = valor literal. Trata string (com aspas) e int (com L)."""
        if isinstance(value, str):
            literal = f"'{value}'"
        else:
            literal = f"{value}L"
        return {
            "Condition": {
                "Comparison": {
                    "ComparisonKind": 0,
                    "Left": {
                        "Column": {
                            "Expression": {"SourceRef": {"Source": alias}},
                            "Property": prop,
                        }
                    },
                    "Right": {"Literal": {"Value": literal}},
                }
            }
        }

    # ----------------------- BLOCOS ORDER --------------------------

    @classmethod
    def order_by(cls, select_clause: dict,
                 direction: Optional[int] = None) -> dict:
        """OrderBy referenciando um item já adicionado ao Select.

        Por padrão ordena DESC. Aceita Column, Aggregation ou Measure.
        """
        direction = direction or cls.ORDER_DESC
        expr_keys = ("Column", "Aggregation", "Measure")
        return {
            "Direction": direction,
            "Expression": {
                k: v for k, v in select_clause.items() if k in expr_keys
            },
        }

    # ----------------------- BUILD ---------------------------------

    def build(self,
              tables: list,
              selects: list,
              filters: Optional[list] = None,
              order_by: Optional[dict] = None,
              window: Optional[int] = 500,
              restart_tokens: Optional[list] = None) -> dict:
        """Compõe o body final pronto para POST no capacity endpoint.

        Args:
            tables: lista de tuplas (alias, entity_name).
            selects: lista de blocos column()/sum()/avg()/etc.
            filters: lista opcional de blocos where_*.
            order_by: bloco opcional construído por order_by().
            window: limite de linhas por janela.
                - Inteiro (ex: 500): inclui DataReduction.Window. Usado em
                  visuais de tabela detalhada.
                - None: omite DataReduction completamente. Usado em visuais
                  de card de KPI que retornam linha única agregada.
            restart_tokens: tokens 'RT' da resposta anterior, para continuar
                a paginação a partir de onde parou. Ignorado se window=None.
        """
        # Validação antecipada contra o schema, se disponível
        if self.schema:
            for alias, entity_name in tables:
                self.schema.get_table(entity_name)

        # Cláusula central da query semântica
        inner = {
            "Version": 2,
            "From": self.from_tables(tables),
            "Select": selects,
        }
        if filters:
            inner["Where"] = filters
        if order_by:
            inner["OrderBy"] = [order_by]

        projections = list(range(len(selects)))

        # Binding base - sempre presente
        binding = {
            "Primary": {
                "Groupings": [{"Projections": projections}]
            },
            "Version": 1,
        }

        # DataReduction só é incluído quando há janela definida.
        # Visuais de card (KPI agregado em linha única) omitem essa seção;
        # visuais de tabela detalhada incluem com window=500 (padrão observado
        # nos bodies reais).
        if window is not None:
            primary_window = {"Count": window}
            if restart_tokens:
                primary_window["RestartTokens"] = restart_tokens
            binding["DataReduction"] = {
                "DataVolume": 3,
                "Primary": {"Window": primary_window},
            }

        return {
            "version": "1.0.0",
            "queries": [{
                "Query": {
                    "Commands": [{
                        "SemanticQueryDataShapeCommand": {
                            "Query": inner,
                            "Binding": binding,
                            "ExecutionMetricsKind": 1,
                        }
                    }]
                },
                "QueryId": "",
                "ApplicationContext": {
                    "DatasetId": self.dataset_id,
                    "Sources": [{
                        "ReportId": self.report_id,
                        "VisualId": "crawler",
                    }],
                },
            }],
            "cancelQueries": [],
            "modelId": self.model_id,
            "userPreferredLocale": "pt-BR",
            "allowLongRunningQueries": True,
        }