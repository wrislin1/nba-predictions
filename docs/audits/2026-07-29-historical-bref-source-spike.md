# Historical Basketball Reference source spike

Generated offline from checksum-valid cached index/month pages.

## Discovery and boundary

Only `{league}_{season}_games-{month}.html` links are accepted; `games-playoffs.html` is excluded.
Rows after an exact `Playoffs` separator are postseason. Four verified duplicate `Playoffs` layouts classify rows before the first separator, except for two verified 1953 carryovers between separators. Exact `Tiebreaker` and `Protest; does not count toward record` rows are excluded as postseason or voided games. 1947 and 1980 use a documented first-playoff-game fallback when no separator exists.

## Schema and statistic keys

The schedule table must provide `date_game`, `visitor_team_name`, `visitor_pts`, `home_team_name`, `home_pts`, `box_score_text`, and `overtimes`. Rows with both score cells blank are not games.

## Observed seasons

### 1947 BAA

- Games: 331
- Dates: 1946-11-01 through 1947-03-31
- Teams: Boston Celtics, Chicago Stags, Cleveland Rebels, Detroit Falcons, New York Knicks, Philadelphia Warriors, Pittsburgh Ironmen, Providence Steamrollers, St. Louis Bombers, Toronto Huskies, Washington Capitols
- Box-score links: 331
- Venue reported: 331
- Attendance statuses: {'missing': 277, 'reported_positive': 54}
- Site statuses: {'home': 331}
- Overtime markers: {'': 311, '2OT': 2, 'OT': 18}
- Nonblank remarks: []
- Schedule pages: ['https://www.basketball-reference.com/leagues/BAA_1947_games-december.html', 'https://www.basketball-reference.com/leagues/BAA_1947_games-february.html', 'https://www.basketball-reference.com/leagues/BAA_1947_games-january.html', 'https://www.basketball-reference.com/leagues/BAA_1947_games-march.html', 'https://www.basketball-reference.com/leagues/BAA_1947_games-november.html']

### 1960 NBA

- Games: 300
- Dates: 1959-10-17 through 1960-03-10
- Teams: Boston Celtics, Cincinnati Royals, Detroit Pistons, Minneapolis Lakers, New York Knicks, Philadelphia Warriors, St. Louis Hawks, Syracuse Nationals
- Box-score links: 300
- Venue reported: 300
- Attendance statuses: {'missing': 225, 'reported_positive': 75}
- Site statuses: {'home': 232, 'unknown': 68}
- Overtime markers: {'': 289, '2OT': 2, 'OT': 9}
- Nonblank remarks: ['at Bethlehem, PA', 'at Boston, MA', 'at Camden, NJ', 'at College Park, MD', 'at Dayton, OH', 'at Detroit, MI', 'at Detroit, MI (University of Detroit Fieldhouse)', 'at Fort Wayne, IN', 'at Hershey, PA', 'at Indianapolis, IN', 'at Los Angeles, CA', 'at New York, NY', 'at Philadelphia, PA', 'at Providence, RI', 'at Rochester, NY', 'at Saint Paul, MN', 'at San Francisco, CA', 'at Seattle, WA', 'at St. Louis, MO', 'at Syracuse, NY']
- Schedule pages: ['https://www.basketball-reference.com/leagues/NBA_1960_games-december.html', 'https://www.basketball-reference.com/leagues/NBA_1960_games-february.html', 'https://www.basketball-reference.com/leagues/NBA_1960_games-january.html', 'https://www.basketball-reference.com/leagues/NBA_1960_games-march.html', 'https://www.basketball-reference.com/leagues/NBA_1960_games-november.html', 'https://www.basketball-reference.com/leagues/NBA_1960_games-october.html']

### 1983 NBA

- Games: 943
- Dates: 1982-10-29 through 1983-04-17
- Teams: Atlanta Hawks, Boston Celtics, Chicago Bulls, Cleveland Cavaliers, Dallas Mavericks, Denver Nuggets, Detroit Pistons, Golden State Warriors, Houston Rockets, Indiana Pacers, Kansas City Kings, Los Angeles Lakers, Milwaukee Bucks, New Jersey Nets, New York Knicks, Philadelphia 76ers, Phoenix Suns, Portland Trail Blazers, San Antonio Spurs, San Diego Clippers, Seattle SuperSonics, Utah Jazz, Washington Bullets
- Box-score links: 943
- Venue reported: 943
- Attendance statuses: {'missing': 19, 'reported_positive': 924}
- Site statuses: {'home': 936, 'unknown': 7}
- Overtime markers: {'': 900, '2OT': 6, 'OT': 37}
- Nonblank remarks: ['Game protested and completed on 1983-04-13', 'at Hartford, CT', 'at St. Louis, MO']
- Schedule pages: ['https://www.basketball-reference.com/leagues/NBA_1983_games-april.html', 'https://www.basketball-reference.com/leagues/NBA_1983_games-december.html', 'https://www.basketball-reference.com/leagues/NBA_1983_games-february.html', 'https://www.basketball-reference.com/leagues/NBA_1983_games-january.html', 'https://www.basketball-reference.com/leagues/NBA_1983_games-march.html', 'https://www.basketball-reference.com/leagues/NBA_1983_games-november.html', 'https://www.basketball-reference.com/leagues/NBA_1983_games-october.html']

## Missingness and normalization decisions

| Source value | Normalized value |
|---|---|
| blank attendance | null + `missing` |
| literal 0 attendance | 0 + `reported_zero` |
| blank venue | null |
| blank / OT / nOT | 0 / 1 / n overtime periods |
| valid / blank box href | slug ID / deterministic fallback ID |
| blank / explicit neutral / other remarks | `home` / `neutral` / `unknown` |
