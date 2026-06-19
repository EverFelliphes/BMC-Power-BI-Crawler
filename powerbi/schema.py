"""Camada de schema do Power BI.

Parseia o `conceptualschema` cru (árvore de dicionários não-tipada) em
estruturas tipadas (`Entity`, `Property`, `Relationship`) que expõem
operações de domínio. Usado pelo `PowerBIQueryBuilder` para validação
antecipada de nomes de tabela antes de gerar o body da query.
"""

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# Códigos de DataType do Power BI / DSR
DATA_TYPES = {
    1: "string",
    3: "double",
    4: "int64",
    5: "boolean",
    6: "datetime",
    7: "decimal",
}

# DefaultAggregate: 1=None (dimensão), 2=Sum (medida)
DEFAULT_AGG = {1: "none", 2: "sum"}


@dataclass
class Property:
    name: str
    data_type: int
    data_type_name: str
    is_measure: bool
    format_string: Optional[str] = None
    edm_name: Optional[str] = None

    def __repr__(self) -> str:
        kind = "M" if self.is_measure else "D"
        return f"<{kind} {self.name}:{self.data_type_name}>"


@dataclass
class Relationship:
    source_entity: str
    target_entity: str
    name: str
    active: bool
    cross_filter_direction: int


@dataclass
class Entity:
    name: str
    edm_name: Optional[str]
    properties: dict

    def get_property(self, name: str) -> Property:
        if name not in self.properties:
            raise KeyError(
                f"Propriedade '{name}' não existe em '{self.name}'. "
                f"Disponíveis: {list(self.properties)}"
            )
        return self.properties[name]

    def measures(self) -> list:
        return [p.name for p in self.properties.values() if p.is_measure]

    def dimensions(self) -> list:
        return [p.name for p in self.properties.values() if not p.is_measure]


class PowerBISchema:
    """Parseia o conceptualschema do Power BI e expõe acesso tipado."""

    def __init__(self, conceptual_schema: dict, model_id: int = None,
                 dataset_id: str = None):
        self._raw = conceptual_schema
        self.model_id = model_id or conceptual_schema.get("modelId")
        self.dataset_id = dataset_id

        self.entities: dict = {}
        self.relationships: list = []
        self._parse()

    def _parse(self) -> None:
        for entity_data in self._raw["schema"]["Entities"]:
            entity = self._parse_entity(entity_data)
            self.entities[entity.name] = entity

            for nav in entity_data.get("NavigationProperties", []):
                self.relationships.append(Relationship(
                    source_entity=entity.name,
                    target_entity=nav["TargetEntity"],
                    name=nav["Name"],
                    active=nav.get("Active", True),
                    cross_filter_direction=nav.get("CrossFilterDirection", 0)
                ))

    @staticmethod
    def _parse_entity(entity_data: dict) -> Entity:
        properties = {}
        for prop in entity_data.get("Properties", []):
            data_type = prop["DataType"]
            default_agg = prop.get("Column", {}).get("DefaultAggregate", 1)

            properties[prop["Name"]] = Property(
                name=prop["Name"],
                data_type=data_type,
                data_type_name=DATA_TYPES.get(data_type, f"unknown_{data_type}"),
                is_measure=(default_agg == 2),
                format_string=prop.get("FormatString"),
                edm_name=prop.get("EdmName"),
            )

        return Entity(
            name=entity_data["Name"],
            edm_name=entity_data.get("EdmName"),
            properties=properties,
        )

    def get_table(self, name: str) -> Entity:
        if name not in self.entities:
            raise KeyError(
                f"Tabela '{name}' não existe. "
                f"Disponíveis: {list(self.entities)}"
            )
        return self.entities[name]

    def summary(self) -> str:
        """Resumo legível do schema, útil para o README e logs."""
        lines = [f"Schema (model_id={self.model_id}):"]
        for entity in self.entities.values():
            lines.append(f"  - {entity.name}")
            lines.append(f"      dimensões: {entity.dimensions()}")
            lines.append(f"      medidas:   {entity.measures()}")
        if self.relationships:
            lines.append("Relacionamentos:")
            for rel in self.relationships:
                arrow = "<->" if rel.cross_filter_direction == 1 else "->"
                lines.append(f"  {rel.source_entity} {arrow} {rel.target_entity}")
        return "\n".join(lines)

    def describe_table(self, name: str) -> str:
        """Resumo legível e detalhado de uma tabela específica."""
        entity = self.get_table(name)
        lines = [f"\nTabela: {entity.name}"]
        if entity.edm_name:
            lines.append(f"   EDM: {entity.edm_name}")
        lines.append(f"   {len(entity.properties)} propriedades:")
        for prop in entity.properties.values():
            badge = "medida" if prop.is_measure else "dimensão"
            fmt = f" [{prop.format_string}]" if prop.format_string else ""
            lines.append(
                f"     - {prop.name:50s} {prop.data_type_name:8s} {badge}{fmt}"
            )
        # Relacionamentos da tabela
        rels = [r for r in self.relationships
                if r.source_entity == name or r.target_entity == name]
        if rels:
            lines.append("   Relacionamentos:")
            for r in rels:
                arrow = "<->" if r.cross_filter_direction == 1 else "->"
                if r.source_entity == name:
                    lines.append(f"     {name} {arrow} {r.target_entity}")
                else:
                    lines.append(f"     {r.source_entity} {arrow} {name}")
        return "\n".join(lines)