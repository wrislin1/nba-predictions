"""Throwaway spike (2026-11-03): draw the development-history pilot sample.
Frame: players with >=1 regular-season appearance in player_games, ending seasons 2019-2026.
Strata use systematic fields only (PlayerIndex country/college; ESPN college seasons via the hoopR crosswalk).
No injury information is consulted. Seed 20261103. Not validated repository code."""
import sys, json, pathlib, random, pandas as pd, pyarrow.parquet as pq
S, pg_dir, cbb, xw, out = (pathlib.Path(a) for a in sys.argv[1:6])
SEED, N_PER, N_BLIND = 20261103, 8, 12
COVERAGE_START = 2005  # first usable ESPN college season

frames = [pq.read_table(pg_dir / f"season={s}/player_games.parquet", columns=["season", "person_id", "player_name", "game_type"]).to_pandas() for s in range(2019, 2027)]
pg = pd.concat(frames); pg = pg[pg.game_type == "regular"]
pool = pg.groupby("person_id").agg(player_name=("player_name", "last"), nba_regular_games_2019_2026=("season", "size")).reset_index()
d = json.load(open(S / "playerindex_historical_2025-26.json")); rs = d["resultSets"][0]
pi = pd.DataFrame(rs["rowSet"], columns=rs["headers"])[["PERSON_ID", "COLLEGE", "COUNTRY", "DRAFT_YEAR", "DRAFT_ROUND", "DRAFT_NUMBER", "FROM_YEAR", "TO_YEAR"]].rename(columns=str.lower)
pool = pool.merge(pi, on="person_id", how="left")
pool["from_year"] = pd.to_numeric(pool.from_year, errors="coerce")

xs = pd.concat([pq.read_table(f, columns=["espn_athlete_id", "nba_player_id"]).to_pandas() for f in sorted(xw.glob("*.parquet"))]).dropna()
xs["nba_player_id"] = xs.nba_player_id.astype(str).str.replace(r"\.0$", "", regex=True).astype(int)
xs["espn_athlete_id"] = xs.espn_athlete_id.astype(str).str.replace(r"\.0$", "", regex=True)
pool["espn_athlete_id"] = pool.person_id.map(xs.groupby("nba_player_id")["espn_athlete_id"].agg(lambda s: s.mode().iloc[0]))

recs = []
for f in sorted(cbb.glob("player_box_20*.parquet")):
    cols = pq.read_schema(f).names; use = [c for c in ["season", "athlete_id", "minutes", "did_not_play", "game_id"] if c in cols]
    t = pq.read_table(f, columns=use).to_pandas()
    if "did_not_play" in t: t = t[~t.did_not_play.fillna(False).astype(bool)]
    t = t[t.minutes.notna()]
    t["athlete_id"] = t.athlete_id.astype(str).str.replace(r"\.0$", "", regex=True)
    recs.append(t.groupby(["athlete_id", "season"]).agg(games=("game_id", "nunique"), minutes=("minutes", "sum")).reset_index())
cb = pd.concat(recs)
agg = cb.groupby("athlete_id").agg(college_seasons_espn=("season", "nunique"), college_games_espn=("games", "sum"), college_minutes_espn=("minutes", "sum"), first_college_season=("season", "min"), last_college_season=("season", "max"))
# --- resolution step 1: crosswalk ESPN id (NBA-era id; matches college rows only for careers keyed after ESPN's re-keying) ---
pool = pool.merge(agg, left_on="espn_athlete_id", right_index=True, how="left")
pool["match_method"] = pool.college_seasons_espn.notna().map({True: "crosswalk_id", False: "unresolved"})
# --- resolution step 2: unique, plausible display-name match for players the id did not find ---
names = []
for f in sorted(cbb.glob("player_box_20*.parquet")):
    t = pq.read_table(f, columns=["season", "athlete_id", "athlete_display_name", "did_not_play", "minutes"]).to_pandas()
    t = t[~t.did_not_play.fillna(False).astype(bool) & t.minutes.notna()]
    t["athlete_id"] = t.athlete_id.astype(str).str.replace(r"\.0$", "", regex=True)
    names.append(t[["athlete_display_name", "athlete_id", "season"]].drop_duplicates())
nm = pd.concat(names).groupby(["athlete_display_name", "athlete_id"]).season.agg(["min", "max"]).reset_index()
d_ = json.load(open(S / "playerindex_historical_2025-26.json")); rs_ = d_["resultSets"][0]
nmap = pd.DataFrame(rs_["rowSet"], columns=rs_["headers"])[["PERSON_ID", "PLAYER_FIRST_NAME", "PLAYER_LAST_NAME"]]
pool["display_name"] = pool.person_id.map((nmap.PLAYER_FIRST_NAME.str.strip() + " " + nmap.PLAYER_LAST_NAME.str.strip()).set_axis(nmap.PERSON_ID))
def name_fallback(r):
    if r.match_method == "crosswalk_id" or pd.isna(r.from_year): return None
    c = nm[(nm.athlete_display_name == r.display_name) & (nm["max"] <= r.from_year + 1) & (nm["min"] >= r.from_year - 8)]
    ids = sorted(set(c.athlete_id)); return ids[0] if len(ids) == 1 else ("ambiguous" if len(ids) > 1 else None)
fb = pool.apply(name_fallback, axis=1)
ok = fb.notna() & fb.ne("ambiguous")
pool.loc[ok, "espn_athlete_id"] = fb[ok]; pool.loc[ok, "match_method"] = "name_unique_plausible"; pool.loc[fb.eq("ambiguous"), "match_method"] = "name_ambiguous"
for col in agg.columns: pool.loc[ok, col] = pool.loc[ok, "espn_athlete_id"].map(agg[col])
pool["college_seasons_espn"] = pool.college_seasons_espn.fillna(0).astype(int)
print("resolution:", pool.match_method.value_counts().to_dict())

def hs_or_none(c):
    c = (c or "").strip().lower()
    return c in ("", "none", "no college") or " hs" in c or "high school" in c or "(hs)" in c or c.endswith(" hs")
us = pool.country.eq("USA"); cs = pool.college_seasons_espn; hs = pool.college.map(hs_or_none)
trunc = pool.first_college_season.le(COVERAGE_START)   # college career may predate coverage
pool["stratum"] = "X_unresolved"
pool.loc[us & (cs >= 2) & ~trunc, "stratum"] = "A_us_college_multi"
pool.loc[us & (cs == 1) & ~trunc, "stratum"] = "B_us_college_one"
pool.loc[us & (cs == 0) & hs, "stratum"] = "C_us_no_college"
pool.loc[~us & (cs == 0), "stratum"] = "D_intl_no_us_college"
pool.loc[~us & (cs >= 1) & ~trunc, "stratum"] = "E_intl_us_college"
median_from = int(pool.from_year.median()); pool["era"] = pool.from_year.map(lambda y: "early" if pd.notna(y) and y <= median_from else "late")

print("pool:", len(pool), "| index match:", pool.country.notna().mean().round(3), "| crosswalk coverage:", pool.espn_athlete_id.notna().mean().round(3))
print("US players with >=1 ESPN college season:", round((us & (cs >= 1)).sum() / us.sum(), 3), "| era split year (from_year <=):", median_from)
print(pool.stratum.value_counts().to_string())
print("\nX_unresolved sample:\n", pool[pool.stratum == "X_unresolved"][["player_name", "country", "college", "from_year", "college_seasons_espn", "first_college_season"]].sort_values("from_year").head(12).to_string(index=False))
print("\nC_us_no_college (all):\n", pool[pool.stratum == "C_us_no_college"][["player_name", "college", "from_year", "draft_year"]].sort_values("from_year").to_string(index=False))

rng = random.Random(SEED); chosen = []
for st in ["A_us_college_multi", "B_us_college_one", "C_us_no_college", "D_intl_no_us_college", "E_intl_us_college"]:
    cand = pool[pool.stratum == st].sort_values("person_id"); picks = []
    for era in ("early", "late"):
        c = cand[cand.era == era].person_id.tolist(); picks += rng.sample(c, min(N_PER // 2, len(c)))
    rest = [p for p in cand.person_id.tolist() if p not in picks]; picks += rng.sample(rest, min(N_PER - len(picks), len(rest)))
    chosen += picks
samp = pool[pool.person_id.isin(chosen)].copy()
blind = set(rng.sample(sorted(chosen), min(N_BLIND, len(chosen)))); samp["blind_subset"] = samp.person_id.isin(blind)
samp = samp.sort_values(["stratum", "era", "person_id"])
cols = ["stratum", "era", "person_id", "player_name", "country", "college", "match_method", "draft_year", "draft_round", "draft_number", "from_year", "to_year", "nba_regular_games_2019_2026", "espn_athlete_id", "college_seasons_espn", "college_games_espn", "college_minutes_espn", "first_college_season", "last_college_season", "blind_subset"]
out.mkdir(parents=True, exist_ok=True); samp[cols].to_csv(out / "pilot_sample.csv", index=False)
pool.groupby(["stratum", "era", "match_method"]).size().rename("players").reset_index().to_csv(out / "pool_strata_summary.csv", index=False)
pool.sort_values("person_id").to_csv(out / "pool_resolution.csv", index=False)  # full frame with strata and match method (untracked; for addenda and audits)
print("\nSAMPLE (n=%d, blind=%d):" % (len(samp), samp.blind_subset.sum()))
print(samp[["stratum", "era", "person_id", "player_name", "country", "college", "from_year", "college_seasons_espn", "match_method", "blind_subset"]].to_string(index=False))
