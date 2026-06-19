"""Parser do formato DSR (Data Shape Result) do Power BI.

O endpoint `/query` retorna dados em formato DSR comprimido. Este parser
aplica os três mecanismos de compressão (dicionário de strings, bitmask de
repetição, schema posicional) e devolve um `pandas.DataFrame` plano.
"""

import logging

import pandas as pd

log = logging.getLogger(__name__)


class DSRParser:
    """Decodifica respostas DSR (Data Shape Result) do Power BI.

    O DSR usa três mecanismos de compressão combinados:
      1. Dicionário de strings (ValueDicts): valores categóricos repetidos
         aparecem como índices, resolvidos contra ValueDicts.D0/D1/...
      2. Bitmask de repetição (campo 'R'): bit i ligado significa que a
         coluna i herda o valor da linha anterior, e foi OMITIDA do
         array 'C' da linha atual.
      3. Schema posicional ('S'): aparece apenas na primeira linha de DM0
         e define a ordem e tipo das colunas.

    Esse parser implementa os três mecanismos e retorna um DataFrame plano.
    """

    @staticmethod
    def parse(response: dict) -> pd.DataFrame:
        """Decodifica response DSR completo em pd.DataFrame.

        Args:
            response: JSON cru retornado pelo endpoint /query

        Returns:
            DataFrame com colunas nomeadas conforme descriptor.Select, mas
            na ordem lógica do Select original (não a ordem física do DSR).
        """
        # Navegação até o coração do DSR
        result_data = response["results"][0]["result"]["data"]
        descriptor = result_data["descriptor"]
        ds = result_data["dsr"]["DS"][0]
        dm0 = ds["PH"][0]["DM0"]
        value_dicts = ds.get("ValueDicts", {})

        # Mapa do código do Select (G0/M0/M1/...) -> nome legível da coluna.
        # O descriptor.Select lista as colunas na ordem LÓGICA pedida.
        code_to_name = {s["Value"]: s["Name"] for s in descriptor["Select"]}
        logical_order = [s["Name"] for s in descriptor["Select"]]

        # A ordem FÍSICA das colunas no array "C" é definida pelo "S" da
        # primeira linha de DM0 — o Power BI agrupa GroupKeys antes das
        # medidas, portanto difere da ordem do descriptor.Select.
        # Cada entrada "S" traz o código em "N" e, quando há dicionário de
        # strings, o nome dele em "DN".
        if dm0 and "S" in dm0[0]:
            s_array = dm0[0]["S"]
            physical_names = [code_to_name.get(s["N"], s["N"]) for s in s_array]
            col_to_dict = {
                i: s["DN"] for i, s in enumerate(s_array) if "DN" in s
            }
        else:
            # Fallback: sem "S", assume ordem física == ordem do descriptor
            physical_names = logical_order
            col_to_dict = {}

        n_cols = len(physical_names)

        # Itera linhas aplicando bitmask de repetição
        rows = []
        previous_row = [None] * n_cols

        for entry in dm0:
            if not isinstance(entry, dict) or "C" not in entry:
                continue

            c_values = entry["C"]
            r_mask = entry.get("R", 0)

            # Reconstrói a linha completa (ordem física)
            full_row = DSRParser._expand_row(
                c_values, r_mask, n_cols, previous_row
            )

            # Resolve referências de dicionário
            resolved_row = DSRParser._resolve_dicts(
                full_row, col_to_dict, value_dicts
            )

            rows.append(resolved_row)
            previous_row = full_row  # repetições usam o valor *bruto*

        df = pd.DataFrame(rows, columns=physical_names)

        # Reordena para a ordem lógica do Select (mantém só colunas presentes,
        # útil caso o descriptor tenha nomes que não vieram no "S").
        ordered = [name for name in logical_order if name in df.columns]
        return df[ordered] if ordered else df

    @staticmethod
    def _expand_row(c_values: list, r_mask: int, n_cols: int,
                    previous_row: list) -> list:
        """Expande uma linha aplicando o bitmask de repetição.

        Regra: para cada bit i de r_mask que estiver ligado, a coluna i
        é herdada da previous_row e NÃO consome elemento de c_values.
        """
        full_row = []
        c_iter = iter(c_values)
        for i in range(n_cols):
            if r_mask & (1 << i):
                # Bit ligado: herda da linha anterior
                full_row.append(previous_row[i])
            else:
                try:
                    full_row.append(next(c_iter))
                except StopIteration:
                    # Defensivo: linha truncada
                    full_row.append(None)
        return full_row

    @staticmethod
    def _resolve_dicts(row: list, col_to_dict: dict,
                       value_dicts: dict) -> list:
        """Resolve índices de dicionário para strings reais."""
        resolved = list(row)
        for col_idx, dict_name in col_to_dict.items():
            if dict_name not in value_dicts:
                continue
            value = resolved[col_idx]
            # Apenas inteiros viram índice; strings já estão expandidas
            if isinstance(value, int):
                dict_values = value_dicts[dict_name]
                if 0 <= value < len(dict_values):
                    resolved[col_idx] = dict_values[value]
        return resolved