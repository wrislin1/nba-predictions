"""Season-aware source team names mapped to stable franchise IDs."""

from functools import lru_cache
from io import BytesIO
from importlib import resources
from typing import NamedTuple

import pandas as pd


REFERENCE = resources.files("nba_data.reference").joinpath(
    "franchises.csv"
)


class UnknownTeamError(ValueError):
    pass


class AmbiguousTeamError(ValueError):
    pass


def load_reference_payload(payload: bytes) -> pd.DataFrame:
    """Parse a franchise reference from immutable captured bytes."""
    frame = pd.read_csv(
        BytesIO(payload),
        dtype={"alias": str, "franchise_id": str},
    )
    frame["first_season"] = frame["first_season"].astype("Int64")
    frame["last_season"] = frame["last_season"].astype("Int64")
    return frame


class FranchiseEra(NamedTuple):
    alias: str
    franchise_id: str
    first_season: int | None
    last_season: int | None


class FranchiseResolver(NamedTuple):
    """An immutable season-aware resolver detached from the module cache."""

    eras: tuple[FranchiseEra, ...]

    @classmethod
    def from_payload(cls, payload: bytes) -> "FranchiseResolver":
        frame = load_reference_payload(payload)
        return cls(tuple(
            FranchiseEra(
                alias=str(row.alias),
                franchise_id=str(row.franchise_id),
                first_season=(
                    None if pd.isna(row.first_season)
                    else int(row.first_season)
                ),
                last_season=(
                    None if pd.isna(row.last_season)
                    else int(row.last_season)
                ),
            )
            for row in frame.itertuples(index=False)
        ))

    def resolve(self, name: str, season: int) -> str:
        alias = name.strip()
        matches = tuple(
            era for era in self.eras
            if era.alias == alias
            and (era.first_season is None or era.first_season <= season)
            and (era.last_season is None or era.last_season >= season)
        )
        if len(matches) == 1:
            return matches[0].franchise_id
        if not matches:
            raise UnknownTeamError(
                f"no franchise for {name!r} in season {season}"
            )
        raise AmbiguousTeamError(
            f"multiple franchise eras for {name!r} in {season}"
        )


@lru_cache(maxsize=1)
def load_reference() -> pd.DataFrame:
    with resources.as_file(REFERENCE) as path:
        return load_reference_payload(path.read_bytes())


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
