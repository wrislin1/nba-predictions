"""Season-aware source team names mapped to stable franchise IDs."""

from functools import lru_cache
from pathlib import Path

import pandas as pd


REFERENCE = Path(__file__).resolve().parents[2] / "data/reference/franchises.csv"


class UnknownTeamError(ValueError):
    pass


class AmbiguousTeamError(ValueError):
    pass


@lru_cache(maxsize=1)
def load_reference() -> pd.DataFrame:
    frame = pd.read_csv(REFERENCE, dtype={"alias": str, "franchise_id": str})
    frame["first_season"] = frame["first_season"].astype("Int64")
    frame["last_season"] = frame["last_season"].astype("Int64")
    return frame


FRANCHISE_IDS = frozenset(load_reference()["franchise_id"])


def resolve(name: str, season: int) -> str:
    ref = load_reference()
    rows = ref[
        (ref["alias"] == name.strip())
        & (ref["first_season"].isna() | (ref["first_season"] <= season))
        & (ref["last_season"].isna() | (ref["last_season"] >= season))
    ]
    if len(rows) == 1:
        return str(rows.iloc[0]["franchise_id"])
    if rows.empty:
        raise UnknownTeamError(f"no franchise for {name!r} in season {season}")
    raise AmbiguousTeamError(f"multiple franchise eras for {name!r} in {season}")
