# Playoff BREF Source Spike

Generated offline from checksum-valid cached playoff schedule pages.

| season | resource | path | sha256 | rows | first_date | last_date | boxscore_links | venue_present | attendance_present | max_overtime |
|---:|---|---|---|---:|---|---|---:|---:|---:|---:|
| 1984 | dedicated | raw/playoff_bref/NBA_1984_games.html | 50c414f0f9a241e036f77a2f8f627194c8da40c23ed5ce76f995933ab05ff4bf | 79 | 1984-04-17 | 1984-06-12 | 79 | 79 | 79 | 1 |
| 2002 | dedicated | raw/playoff_bref/NBA_2002_games.html | 6f0eddd6f34de480f5e89178ef632c8f0bbe07c18e93c26bc179aec62d99b977 | 71 | 2002-04-20 | 2002-06-12 | 71 | 71 | 71 | 2 |
| 2020 | dedicated | raw/playoff_bref/NBA_2020_games.html | f99c173f8fb724c1b282ee95dcd386213725ac278c09974f9c16e00de96e56a4 | 83 | 2020-08-17 | 2020-10-11 | 83 | 83 | 0 | 2 |
| 2020 | play_in_month | raw/playoff_bref/NBA_2020_games-august.html | b56ad42ce7ae6e4e8e68639fe6b4f443a8ba139399c619fa29ddeb5f79a4bed1 | 123 | 2020-08-01 | 2020-08-31 | 123 | 123 | 0 | 2 |
| 2026 | dedicated | raw/playoff_bref/NBA_2026_games.html | 2826d2e64597ca12793e17504baa33f13d149959abd8285494ba69c0088a458d | 85 | 2026-04-18 | 2026-06-13 | 85 | 85 | 85 | 2 |
| 2026 | play_in_month | raw/playoff_bref/NBA_2026_games-april.html | 486496f9f87c557e055184495624ad5be3585f3bb1e9642cc231a55cfbff1531 | 144 | 2026-04-01 | 2026-04-30 | 144 | 144 | 144 | 1 |

## Deterministic handling

Each verified body must contain exactly one supported schedule table; every completed row must have both scores and a valid box-score link. Blank venue or attendance values remain null and are counted above. The 2020 bubble sources report venues but no attendance, so all 2020 attendance counts are zero without imputation. Play-in month pages contain regular-season rows; only exact `Play-In Game` remarks are classified below.

## Classifier rows

bref-198404170BOS | 1984-04-17 | playoff | Washington Bullets 83 | Boston Celtics 91 |
bref-198404170DAL | 1984-04-17 | playoff | Seattle SuperSonics 86 | Dallas Mavericks 88 |
bref-198404170DET | 1984-04-17 | playoff | New York Knicks 94 | Detroit Pistons 93 |
bref-198404170MIL | 1984-04-17 | playoff | Atlanta Hawks 89 | Milwaukee Bucks 105 |
bref-198404170UTA | 1984-04-17 | playoff | Denver Nuggets 121 | Utah Jazz 123 |
bref-198404180LAL | 1984-04-18 | playoff | Kansas City Kings 105 | Los Angeles Lakers 116 |
bref-198404180PHI | 1984-04-18 | playoff | New Jersey Nets 116 | Philadelphia 76ers 101 |
bref-198404180POR | 1984-04-18 | playoff | Phoenix Suns 113 | Portland Trail Blazers 106 |
bref-198404190BOS | 1984-04-19 | playoff | Washington Bullets 85 | Boston Celtics 88 |
bref-198404190DAL | 1984-04-19 | playoff | Seattle SuperSonics 95 | Dallas Mavericks 92 |
bref-198404190DET | 1984-04-19 | playoff | New York Knicks 105 | Detroit Pistons 113 |
bref-198404190MIL | 1984-04-19 | playoff | Atlanta Hawks 87 | Milwaukee Bucks 101 |
bref-198404190UTA | 1984-04-19 | playoff | Denver Nuggets 132 | Utah Jazz 116 |
bref-198404200LAL | 1984-04-20 | playoff | Kansas City Kings 102 | Los Angeles Lakers 109 |
bref-198404200PHI | 1984-04-20 | playoff | New Jersey Nets 116 | Philadelphia 76ers 102 |
bref-198404200POR | 1984-04-20 | playoff | Phoenix Suns 116 | Portland Trail Blazers 122 |
bref-198404210ATL | 1984-04-21 | playoff | Milwaukee Bucks 94 | Atlanta Hawks 103 |
bref-198404210SEA | 1984-04-21 | playoff | Dallas Mavericks 94 | Seattle SuperSonics 104 | at Seattle, WA (Seattle Coliseum)
bref-198404210WSB | 1984-04-21 | playoff | Boston Celtics 108 | Washington Bullets 111 |
bref-198404220DEN | 1984-04-22 | playoff | Utah Jazz 117 | Denver Nuggets 121 |
bref-198404220KCK | 1984-04-22 | playoff | Los Angeles Lakers 108 | Kansas City Kings 102 |
bref-198404220NJN | 1984-04-22 | playoff | Philadelphia 76ers 108 | New Jersey Nets 100 |
bref-198404220NYK | 1984-04-22 | playoff | Detroit Pistons 113 | New York Knicks 120 |
bref-198404220PHO | 1984-04-22 | playoff | Portland Trail Blazers 103 | Phoenix Suns 106 |
bref-198404240ATL | 1984-04-24 | playoff | Milwaukee Bucks 97 | Atlanta Hawks 100 |
bref-198404240DEN | 1984-04-24 | playoff | Utah Jazz 129 | Denver Nuggets 124 |
bref-198404240NJN | 1984-04-24 | playoff | Philadelphia 76ers 110 | New Jersey Nets 102 |
bref-198404240PHO | 1984-04-24 | playoff | Portland Trail Blazers 113 | Phoenix Suns 110 |
bref-198404240SEA | 1984-04-24 | playoff | Dallas Mavericks 107 | Seattle SuperSonics 96 |
bref-198404240WSB | 1984-04-24 | playoff | Boston Celtics 99 | Washington Bullets 96 |
bref-198404250NYK | 1984-04-25 | playoff | Detroit Pistons 119 | New York Knicks 112 |
bref-198404260DAL | 1984-04-26 | playoff | Seattle SuperSonics 104 | Dallas Mavericks 105 | at University Park, TX
bref-198404260MIL | 1984-04-26 | playoff | Atlanta Hawks 89 | Milwaukee Bucks 118 |
bref-198404260PHI | 1984-04-26 | playoff | New Jersey Nets 101 | Philadelphia 76ers 98 |
bref-198404260POR | 1984-04-26 | playoff | Phoenix Suns 117 | Portland Trail Blazers 105 |
bref-198404260UTA | 1984-04-26 | playoff | Denver Nuggets 111 | Utah Jazz 127 |
bref-198404270DET | 1984-04-27 | playoff | New York Knicks 127 | Detroit Pistons 123 |
bref-198404280LAL | 1984-04-28 | playoff | Dallas Mavericks 91 | Los Angeles Lakers 134 |
bref-198404290BOS | 1984-04-29 | playoff | New York Knicks 92 | Boston Celtics 110 |
bref-198404290MIL | 1984-04-29 | playoff | New Jersey Nets 106 | Milwaukee Bucks 100 |
bref-198404290UTA | 1984-04-29 | playoff | Phoenix Suns 95 | Utah Jazz 105 |
bref-198405010LAL | 1984-05-01 | playoff | Dallas Mavericks 101 | Los Angeles Lakers 117 |
bref-198405010MIL | 1984-05-01 | playoff | New Jersey Nets 94 | Milwaukee Bucks 98 |
bref-198405020BOS | 1984-05-02 | playoff | New York Knicks 102 | Boston Celtics 116 |
bref-198405020UTA | 1984-05-02 | playoff | Phoenix Suns 102 | Utah Jazz 97 |
bref-198405030NJN | 1984-05-03 | playoff | Milwaukee Bucks 100 | New Jersey Nets 93 |
bref-198405040DAL | 1984-05-04 | playoff | Los Angeles Lakers 115 | Dallas Mavericks 125 |
bref-198405040NYK | 1984-05-04 | playoff | Boston Celtics 92 | New York Knicks 100 |
bref-198405040PHO | 1984-05-04 | playoff | Utah Jazz 94 | Phoenix Suns 106 |
bref-198405050NJN | 1984-05-05 | playoff | Milwaukee Bucks 99 | New Jersey Nets 106 |
bref-198405060DAL | 1984-05-06 | playoff | Los Angeles Lakers 122 | Dallas Mavericks 115 |
bref-198405060NYK | 1984-05-06 | playoff | Boston Celtics 113 | New York Knicks 118 |
bref-198405060PHO | 1984-05-06 | playoff | Utah Jazz 110 | Phoenix Suns 111 |
bref-198405080LAL | 1984-05-08 | playoff | Dallas Mavericks 99 | Los Angeles Lakers 115 |
bref-198405080MIL | 1984-05-08 | playoff | New Jersey Nets 82 | Milwaukee Bucks 94 |
bref-198405080UTA | 1984-05-08 | playoff | Phoenix Suns 106 | Utah Jazz 118 |
bref-198405090BOS | 1984-05-09 | playoff | New York Knicks 99 | Boston Celtics 121 |
bref-198405100NJN | 1984-05-10 | playoff | Milwaukee Bucks 98 | New Jersey Nets 97 |
bref-198405100PHO | 1984-05-10 | playoff | Utah Jazz 82 | Phoenix Suns 102 |
bref-198405110NYK | 1984-05-11 | playoff | Boston Celtics 104 | New York Knicks 106 |
bref-198405120LAL | 1984-05-12 | playoff | Phoenix Suns 94 | Los Angeles Lakers 110 |
bref-198405130BOS | 1984-05-13 | playoff | New York Knicks 104 | Boston Celtics 121 |
bref-198405150BOS | 1984-05-15 | playoff | Milwaukee Bucks 96 | Boston Celtics 119 |
bref-198405150LAL | 1984-05-15 | playoff | Phoenix Suns 102 | Los Angeles Lakers 118 |
bref-198405170BOS | 1984-05-17 | playoff | Milwaukee Bucks 110 | Boston Celtics 125 |
bref-198405180PHO | 1984-05-18 | playoff | Los Angeles Lakers 127 | Phoenix Suns 135 |
bref-198405190MIL | 1984-05-19 | playoff | Boston Celtics 109 | Milwaukee Bucks 100 |
bref-198405200PHO | 1984-05-20 | playoff | Los Angeles Lakers 126 | Phoenix Suns 115 |
bref-198405210MIL | 1984-05-21 | playoff | Boston Celtics 113 | Milwaukee Bucks 122 |
bref-198405230BOS | 1984-05-23 | playoff | Milwaukee Bucks 108 | Boston Celtics 115 |
bref-198405230LAL | 1984-05-23 | playoff | Phoenix Suns 126 | Los Angeles Lakers 121 |
bref-198405250PHO | 1984-05-25 | playoff | Los Angeles Lakers 99 | Phoenix Suns 97 |
bref-198405270BOS | 1984-05-27 | playoff | Los Angeles Lakers 115 | Boston Celtics 109 |
bref-198405310BOS | 1984-05-31 | playoff | Los Angeles Lakers 121 | Boston Celtics 124 |
bref-198406030LAL | 1984-06-03 | playoff | Boston Celtics 104 | Los Angeles Lakers 137 |
bref-198406060LAL | 1984-06-06 | playoff | Boston Celtics 129 | Los Angeles Lakers 125 |
bref-198406080BOS | 1984-06-08 | playoff | Los Angeles Lakers 103 | Boston Celtics 121 |
bref-198406100LAL | 1984-06-10 | playoff | Boston Celtics 108 | Los Angeles Lakers 119 |
bref-198406120BOS | 1984-06-12 | playoff | Los Angeles Lakers 102 | Boston Celtics 111 |
bref-200204200CHH | 2002-04-20 | playoff | Orlando Magic 79 | Charlotte Hornets 80 |
bref-200204200NJN | 2002-04-20 | playoff | Indiana Pacers 89 | New Jersey Nets 83 |
bref-200204200SAC | 2002-04-20 | playoff | Utah Jazz 86 | Sacramento Kings 89 |
bref-200204200SAS | 2002-04-20 | playoff | Seattle SuperSonics 89 | San Antonio Spurs 110 |
bref-200204210BOS | 2002-04-21 | playoff | Philadelphia 76ers 82 | Boston Celtics 92 |
bref-200204210DAL | 2002-04-21 | playoff | Minnesota Timberwolves 94 | Dallas Mavericks 101 |
bref-200204210DET | 2002-04-21 | playoff | Toronto Raptors 63 | Detroit Pistons 85 |
bref-200204210LAL | 2002-04-21 | playoff | Portland Trail Blazers 87 | Los Angeles Lakers 95 |
bref-200204220NJN | 2002-04-22 | playoff | Indiana Pacers 79 | New Jersey Nets 95 |
bref-200204220SAS | 2002-04-22 | playoff | Seattle SuperSonics 98 | San Antonio Spurs 90 |
bref-200204230CHH | 2002-04-23 | playoff | Orlando Magic 111 | Charlotte Hornets 103 |
bref-200204230SAC | 2002-04-23 | playoff | Utah Jazz 93 | Sacramento Kings 86 |
bref-200204240DAL | 2002-04-24 | playoff | Minnesota Timberwolves 110 | Dallas Mavericks 122 |
bref-200204240DET | 2002-04-24 | playoff | Toronto Raptors 91 | Detroit Pistons 96 |
bref-200204250BOS | 2002-04-25 | playoff | Philadelphia 76ers 85 | Boston Celtics 93 |
bref-200204250LAL | 2002-04-25 | playoff | Portland Trail Blazers 96 | Los Angeles Lakers 103 |
bref-200204260IND | 2002-04-26 | playoff | New Jersey Nets 85 | Indiana Pacers 84 |
bref-200204270ORL | 2002-04-27 | playoff | Charlotte Hornets 110 | Orlando Magic 100 |
bref-200204270SEA | 2002-04-27 | playoff | San Antonio Spurs 102 | Seattle SuperSonics 75 |
bref-200204270TOR | 2002-04-27 | playoff | Detroit Pistons 84 | Toronto Raptors 94 |
bref-200204270UTA | 2002-04-27 | playoff | Sacramento Kings 90 | Utah Jazz 87 |
bref-200204280MIN | 2002-04-28 | playoff | Dallas Mavericks 115 | Minnesota Timberwolves 102 |
bref-200204280PHI | 2002-04-28 | playoff | Boston Celtics 103 | Philadelphia 76ers 108 |
bref-200204280POR | 2002-04-28 | playoff | Los Angeles Lakers 92 | Portland Trail Blazers 91 |
bref-200204290TOR | 2002-04-29 | playoff | Detroit Pistons 83 | Toronto Raptors 89 |
bref-200204290UTA | 2002-04-29 | playoff | Sacramento Kings 91 | Utah Jazz 86 |
bref-200204300IND | 2002-04-30 | playoff | New Jersey Nets 74 | Indiana Pacers 97 |
bref-200204300ORL | 2002-04-30 | playoff | Charlotte Hornets 102 | Orlando Magic 85 |
bref-200205010PHI | 2002-05-01 | playoff | Boston Celtics 81 | Philadelphia 76ers 83 |
bref-200205010SEA | 2002-05-01 | playoff | San Antonio Spurs 79 | Seattle SuperSonics 91 |
bref-200205020DET | 2002-05-02 | playoff | Toronto Raptors 82 | Detroit Pistons 85 |
bref-200205020NJN | 2002-05-02 | playoff | Indiana Pacers 109 | New Jersey Nets 120 |
bref-200205030BOS | 2002-05-03 | playoff | Philadelphia 76ers 87 | Boston Celtics 120 |
bref-200205030SAS | 2002-05-03 | playoff | Seattle SuperSonics 78 | San Antonio Spurs 101 |
bref-200205040SAC | 2002-05-04 | playoff | Dallas Mavericks 91 | Sacramento Kings 108 |
bref-200205050DET | 2002-05-05 | playoff | Boston Celtics 84 | Detroit Pistons 96 |
bref-200205050LAL | 2002-05-05 | playoff | San Antonio Spurs 80 | Los Angeles Lakers 86 |
bref-200205050NJN | 2002-05-05 | playoff | Charlotte Hornets 93 | New Jersey Nets 99 |
bref-200205060SAC | 2002-05-06 | playoff | Dallas Mavericks 110 | Sacramento Kings 102 |
bref-200205070LAL | 2002-05-07 | playoff | San Antonio Spurs 88 | Los Angeles Lakers 85 |
bref-200205070NJN | 2002-05-07 | playoff | Charlotte Hornets 88 | New Jersey Nets 102 |
bref-200205080DET | 2002-05-08 | playoff | Boston Celtics 85 | Detroit Pistons 77 |
bref-200205090CHH | 2002-05-09 | playoff | New Jersey Nets 97 | Charlotte Hornets 115 |
bref-200205090DAL | 2002-05-09 | playoff | Sacramento Kings 125 | Dallas Mavericks 119 |
bref-200205100BOS | 2002-05-10 | playoff | Detroit Pistons 64 | Boston Celtics 66 |
bref-200205100SAS | 2002-05-10 | playoff | Los Angeles Lakers 99 | San Antonio Spurs 89 |
bref-200205110DAL | 2002-05-11 | playoff | Sacramento Kings 115 | Dallas Mavericks 113 |
bref-200205120BOS | 2002-05-12 | playoff | Detroit Pistons 79 | Boston Celtics 90 |
bref-200205120CHH | 2002-05-12 | playoff | New Jersey Nets 89 | Charlotte Hornets 79 |
bref-200205120SAS | 2002-05-12 | playoff | Los Angeles Lakers 87 | San Antonio Spurs 85 |
bref-200205130SAC | 2002-05-13 | playoff | Dallas Mavericks 101 | Sacramento Kings 114 |
bref-200205140DET | 2002-05-14 | playoff | Boston Celtics 90 | Detroit Pistons 81 |
bref-200205140LAL | 2002-05-14 | playoff | San Antonio Spurs 87 | Los Angeles Lakers 93 |
bref-200205150NJN | 2002-05-15 | playoff | Charlotte Hornets 95 | New Jersey Nets 103 |
bref-200205180SAC | 2002-05-18 | playoff | Los Angeles Lakers 106 | Sacramento Kings 99 |
bref-200205190NJN | 2002-05-19 | playoff | Boston Celtics 97 | New Jersey Nets 104 |
bref-200205200SAC | 2002-05-20 | playoff | Los Angeles Lakers 90 | Sacramento Kings 96 |
bref-200205210NJN | 2002-05-21 | playoff | Boston Celtics 93 | New Jersey Nets 86 |
bref-200205240LAL | 2002-05-24 | playoff | Sacramento Kings 103 | Los Angeles Lakers 90 |
bref-200205250BOS | 2002-05-25 | playoff | New Jersey Nets 90 | Boston Celtics 94 |
bref-200205260LAL | 2002-05-26 | playoff | Sacramento Kings 99 | Los Angeles Lakers 100 |
bref-200205270BOS | 2002-05-27 | playoff | New Jersey Nets 94 | Boston Celtics 92 |
bref-200205280SAC | 2002-05-28 | playoff | Los Angeles Lakers 91 | Sacramento Kings 92 |
bref-200205290NJN | 2002-05-29 | playoff | Boston Celtics 92 | New Jersey Nets 103 |
bref-200205310BOS | 2002-05-31 | playoff | New Jersey Nets 96 | Boston Celtics 88 |
bref-200205310LAL | 2002-05-31 | playoff | Sacramento Kings 102 | Los Angeles Lakers 106 |
bref-200206020SAC | 2002-06-02 | playoff | Los Angeles Lakers 112 | Sacramento Kings 106 |
bref-200206050LAL | 2002-06-05 | playoff | New Jersey Nets 94 | Los Angeles Lakers 99 |
bref-200206070LAL | 2002-06-07 | playoff | New Jersey Nets 83 | Los Angeles Lakers 106 |
bref-200206090NJN | 2002-06-09 | playoff | Los Angeles Lakers 106 | New Jersey Nets 103 |
bref-200206120NJN | 2002-06-12 | playoff | Los Angeles Lakers 113 | New Jersey Nets 107 |
bref-202008150POR | 2020-08-15 | play_in | Memphis Grizzlies 122 | Portland Trail Blazers 126 | Play-In Game
bref-202008170BOS | 2020-08-17 | playoff | Philadelphia 76ers 101 | Boston Celtics 109 |
bref-202008170DEN | 2020-08-17 | playoff | Utah Jazz 125 | Denver Nuggets 135 |
bref-202008170LAC | 2020-08-17 | playoff | Dallas Mavericks 110 | Los Angeles Clippers 118 |
bref-202008170TOR | 2020-08-17 | playoff | Brooklyn Nets 110 | Toronto Raptors 134 |
bref-202008180HOU | 2020-08-18 | playoff | Oklahoma City Thunder 108 | Houston Rockets 123 |
bref-202008180IND | 2020-08-18 | playoff | Miami Heat 113 | Indiana Pacers 101 |
bref-202008180LAL | 2020-08-18 | playoff | Portland Trail Blazers 100 | Los Angeles Lakers 93 |
bref-202008180MIL | 2020-08-18 | playoff | Orlando Magic 122 | Milwaukee Bucks 110 |
bref-202008190BOS | 2020-08-19 | playoff | Philadelphia 76ers 101 | Boston Celtics 128 |
bref-202008190DEN | 2020-08-19 | playoff | Utah Jazz 124 | Denver Nuggets 105 |
bref-202008190LAC | 2020-08-19 | playoff | Dallas Mavericks 127 | Los Angeles Clippers 114 |
bref-202008190TOR | 2020-08-19 | playoff | Brooklyn Nets 99 | Toronto Raptors 104 |
bref-202008200HOU | 2020-08-20 | playoff | Oklahoma City Thunder 98 | Houston Rockets 111 |
bref-202008200IND | 2020-08-20 | playoff | Miami Heat 109 | Indiana Pacers 100 |
bref-202008200LAL | 2020-08-20 | playoff | Portland Trail Blazers 88 | Los Angeles Lakers 111 |
bref-202008200MIL | 2020-08-20 | playoff | Orlando Magic 96 | Milwaukee Bucks 111 |
bref-202008210BRK | 2020-08-21 | playoff | Toronto Raptors 117 | Brooklyn Nets 92 |
bref-202008210DAL | 2020-08-21 | playoff | Los Angeles Clippers 130 | Dallas Mavericks 122 |
bref-202008210PHI | 2020-08-21 | playoff | Boston Celtics 102 | Philadelphia 76ers 94 |
bref-202008210UTA | 2020-08-21 | playoff | Denver Nuggets 87 | Utah Jazz 124 |
bref-202008220MIA | 2020-08-22 | playoff | Indiana Pacers 115 | Miami Heat 124 |
bref-202008220OKC | 2020-08-22 | playoff | Houston Rockets 107 | Oklahoma City Thunder 119 |
bref-202008220ORL | 2020-08-22 | playoff | Milwaukee Bucks 121 | Orlando Magic 107 |
bref-202008220POR | 2020-08-22 | playoff | Los Angeles Lakers 116 | Portland Trail Blazers 108 |
bref-202008230BRK | 2020-08-23 | playoff | Toronto Raptors 150 | Brooklyn Nets 122 |
bref-202008230DAL | 2020-08-23 | playoff | Los Angeles Clippers 133 | Dallas Mavericks 135 |
bref-202008230PHI | 2020-08-23 | playoff | Boston Celtics 110 | Philadelphia 76ers 106 |
bref-202008230UTA | 2020-08-23 | playoff | Denver Nuggets 127 | Utah Jazz 129 |
bref-202008240MIA | 2020-08-24 | playoff | Indiana Pacers 87 | Miami Heat 99 |
bref-202008240OKC | 2020-08-24 | playoff | Houston Rockets 114 | Oklahoma City Thunder 117 |
bref-202008240ORL | 2020-08-24 | playoff | Milwaukee Bucks 121 | Orlando Magic 106 |
bref-202008240POR | 2020-08-24 | playoff | Los Angeles Lakers 135 | Portland Trail Blazers 115 |
bref-202008250DEN | 2020-08-25 | playoff | Utah Jazz 107 | Denver Nuggets 117 |
bref-202008250LAC | 2020-08-25 | playoff | Dallas Mavericks 111 | Los Angeles Clippers 154 |
bref-202008290HOU | 2020-08-29 | playoff | Oklahoma City Thunder 80 | Houston Rockets 114 |
bref-202008290LAL | 2020-08-29 | playoff | Portland Trail Blazers 122 | Los Angeles Lakers 131 |
bref-202008290MIL | 2020-08-29 | playoff | Orlando Magic 104 | Milwaukee Bucks 118 |
bref-202008300DAL | 2020-08-30 | playoff | Los Angeles Clippers 111 | Dallas Mavericks 97 |
bref-202008300TOR | 2020-08-30 | playoff | Boston Celtics 112 | Toronto Raptors 94 |
bref-202008300UTA | 2020-08-30 | playoff | Denver Nuggets 119 | Utah Jazz 107 |
bref-202008310MIL | 2020-08-31 | playoff | Miami Heat 115 | Milwaukee Bucks 104 |
bref-202008310OKC | 2020-08-31 | playoff | Houston Rockets 100 | Oklahoma City Thunder 104 |
bref-202009010DEN | 2020-09-01 | playoff | Utah Jazz 78 | Denver Nuggets 80 |
bref-202009010TOR | 2020-09-01 | playoff | Boston Celtics 102 | Toronto Raptors 99 |
bref-202009020HOU | 2020-09-02 | playoff | Oklahoma City Thunder 102 | Houston Rockets 104 |
bref-202009020MIL | 2020-09-02 | playoff | Miami Heat 116 | Milwaukee Bucks 114 |
bref-202009030BOS | 2020-09-03 | playoff | Toronto Raptors 104 | Boston Celtics 103 |
bref-202009030LAC | 2020-09-03 | playoff | Denver Nuggets 97 | Los Angeles Clippers 120 |
bref-202009040LAL | 2020-09-04 | playoff | Houston Rockets 112 | Los Angeles Lakers 97 |
bref-202009040MIA | 2020-09-04 | playoff | Milwaukee Bucks 100 | Miami Heat 115 |
bref-202009050BOS | 2020-09-05 | playoff | Toronto Raptors 100 | Boston Celtics 93 |
bref-202009050LAC | 2020-09-05 | playoff | Denver Nuggets 110 | Los Angeles Clippers 101 |
bref-202009060LAL | 2020-09-06 | playoff | Houston Rockets 109 | Los Angeles Lakers 117 |
bref-202009060MIA | 2020-09-06 | playoff | Milwaukee Bucks 118 | Miami Heat 115 |
bref-202009070DEN | 2020-09-07 | playoff | Los Angeles Clippers 113 | Denver Nuggets 107 |
bref-202009070TOR | 2020-09-07 | playoff | Boston Celtics 111 | Toronto Raptors 89 |
bref-202009080HOU | 2020-09-08 | playoff | Los Angeles Lakers 112 | Houston Rockets 102 |
bref-202009080MIL | 2020-09-08 | playoff | Miami Heat 103 | Milwaukee Bucks 94 |
bref-202009090BOS | 2020-09-09 | playoff | Toronto Raptors 125 | Boston Celtics 122 |
bref-202009090DEN | 2020-09-09 | playoff | Los Angeles Clippers 96 | Denver Nuggets 85 |
bref-202009100HOU | 2020-09-10 | playoff | Los Angeles Lakers 110 | Houston Rockets 100 |
bref-202009110LAC | 2020-09-11 | playoff | Denver Nuggets 111 | Los Angeles Clippers 105 |
bref-202009110TOR | 2020-09-11 | playoff | Boston Celtics 92 | Toronto Raptors 87 |
bref-202009120LAL | 2020-09-12 | playoff | Houston Rockets 96 | Los Angeles Lakers 119 |
bref-202009130DEN | 2020-09-13 | playoff | Los Angeles Clippers 98 | Denver Nuggets 111 |
bref-202009150BOS | 2020-09-15 | playoff | Miami Heat 117 | Boston Celtics 114 |
bref-202009150LAC | 2020-09-15 | playoff | Denver Nuggets 104 | Los Angeles Clippers 89 |
bref-202009170BOS | 2020-09-17 | playoff | Miami Heat 106 | Boston Celtics 101 |
bref-202009180LAL | 2020-09-18 | playoff | Denver Nuggets 114 | Los Angeles Lakers 126 |
bref-202009190MIA | 2020-09-19 | playoff | Boston Celtics 117 | Miami Heat 106 |
bref-202009200LAL | 2020-09-20 | playoff | Denver Nuggets 103 | Los Angeles Lakers 105 |
bref-202009220DEN | 2020-09-22 | playoff | Los Angeles Lakers 106 | Denver Nuggets 114 |
bref-202009230MIA | 2020-09-23 | playoff | Boston Celtics 109 | Miami Heat 112 |
bref-202009240DEN | 2020-09-24 | playoff | Los Angeles Lakers 114 | Denver Nuggets 108 |
bref-202009250BOS | 2020-09-25 | playoff | Miami Heat 108 | Boston Celtics 121 |
bref-202009260LAL | 2020-09-26 | playoff | Denver Nuggets 107 | Los Angeles Lakers 117 |
bref-202009270MIA | 2020-09-27 | playoff | Boston Celtics 113 | Miami Heat 125 |
bref-202009300LAL | 2020-09-30 | playoff | Miami Heat 98 | Los Angeles Lakers 116 |
bref-202010020LAL | 2020-10-02 | playoff | Miami Heat 114 | Los Angeles Lakers 124 |
bref-202010040MIA | 2020-10-04 | playoff | Los Angeles Lakers 104 | Miami Heat 115 |
bref-202010060MIA | 2020-10-06 | playoff | Los Angeles Lakers 102 | Miami Heat 96 |
bref-202010090LAL | 2020-10-09 | playoff | Miami Heat 111 | Los Angeles Lakers 108 |
bref-202010110MIA | 2020-10-11 | playoff | Los Angeles Lakers 106 | Miami Heat 93 |
bref-202604140CHO | 2026-04-14 | play_in | Miami Heat 126 | Charlotte Hornets 127 | Play-In Game
bref-202604140PHO | 2026-04-14 | play_in | Portland Trail Blazers 114 | Phoenix Suns 110 | Play-In Game
bref-202604150LAC | 2026-04-15 | play_in | Golden State Warriors 126 | Los Angeles Clippers 121 | Play-In Game
bref-202604150PHI | 2026-04-15 | play_in | Orlando Magic 97 | Philadelphia 76ers 109 | Play-In Game
bref-202604170ORL | 2026-04-17 | play_in | Charlotte Hornets 90 | Orlando Magic 121 | Play-In Game
bref-202604170PHO | 2026-04-17 | play_in | Golden State Warriors 96 | Phoenix Suns 111 | Play-In Game
bref-202604180CLE | 2026-04-18 | playoff | Toronto Raptors 113 | Cleveland Cavaliers 126 |
bref-202604180DEN | 2026-04-18 | playoff | Minnesota Timberwolves 105 | Denver Nuggets 116 |
bref-202604180LAL | 2026-04-18 | playoff | Houston Rockets 98 | Los Angeles Lakers 107 |
bref-202604180NYK | 2026-04-18 | playoff | Atlanta Hawks 102 | New York Knicks 113 |
bref-202604190BOS | 2026-04-19 | playoff | Philadelphia 76ers 91 | Boston Celtics 123 |
bref-202604190DET | 2026-04-19 | playoff | Orlando Magic 112 | Detroit Pistons 101 |
bref-202604190OKC | 2026-04-19 | playoff | Phoenix Suns 84 | Oklahoma City Thunder 119 |
bref-202604190SAS | 2026-04-19 | playoff | Portland Trail Blazers 98 | San Antonio Spurs 111 |
bref-202604200CLE | 2026-04-20 | playoff | Toronto Raptors 105 | Cleveland Cavaliers 115 |
bref-202604200DEN | 2026-04-20 | playoff | Minnesota Timberwolves 119 | Denver Nuggets 114 |
bref-202604200NYK | 2026-04-20 | playoff | Atlanta Hawks 107 | New York Knicks 106 |
bref-202604210BOS | 2026-04-21 | playoff | Philadelphia 76ers 111 | Boston Celtics 97 |
bref-202604210LAL | 2026-04-21 | playoff | Houston Rockets 94 | Los Angeles Lakers 101 |
bref-202604210SAS | 2026-04-21 | playoff | Portland Trail Blazers 106 | San Antonio Spurs 103 |
bref-202604220DET | 2026-04-22 | playoff | Orlando Magic 83 | Detroit Pistons 98 |
bref-202604220OKC | 2026-04-22 | playoff | Phoenix Suns 107 | Oklahoma City Thunder 120 |
bref-202604230ATL | 2026-04-23 | playoff | New York Knicks 108 | Atlanta Hawks 109 |
bref-202604230MIN | 2026-04-23 | playoff | Denver Nuggets 96 | Minnesota Timberwolves 113 |
bref-202604230TOR | 2026-04-23 | playoff | Cleveland Cavaliers 104 | Toronto Raptors 126 |
bref-202604240HOU | 2026-04-24 | playoff | Los Angeles Lakers 112 | Houston Rockets 108 |
bref-202604240PHI | 2026-04-24 | playoff | Boston Celtics 108 | Philadelphia 76ers 100 |
bref-202604240POR | 2026-04-24 | playoff | San Antonio Spurs 120 | Portland Trail Blazers 108 |
bref-202604250ATL | 2026-04-25 | playoff | New York Knicks 114 | Atlanta Hawks 98 |
bref-202604250MIN | 2026-04-25 | playoff | Denver Nuggets 96 | Minnesota Timberwolves 112 |
bref-202604250ORL | 2026-04-25 | playoff | Detroit Pistons 105 | Orlando Magic 113 |
bref-202604250PHO | 2026-04-25 | playoff | Oklahoma City Thunder 121 | Phoenix Suns 109 |
bref-202604260HOU | 2026-04-26 | playoff | Los Angeles Lakers 96 | Houston Rockets 115 |
bref-202604260PHI | 2026-04-26 | playoff | Boston Celtics 128 | Philadelphia 76ers 96 |
bref-202604260POR | 2026-04-26 | playoff | San Antonio Spurs 114 | Portland Trail Blazers 93 |
bref-202604260TOR | 2026-04-26 | playoff | Cleveland Cavaliers 89 | Toronto Raptors 93 |
bref-202604270DEN | 2026-04-27 | playoff | Minnesota Timberwolves 113 | Denver Nuggets 125 |
bref-202604270ORL | 2026-04-27 | playoff | Detroit Pistons 88 | Orlando Magic 94 |
bref-202604270PHO | 2026-04-27 | playoff | Oklahoma City Thunder 131 | Phoenix Suns 122 |
bref-202604280BOS | 2026-04-28 | playoff | Philadelphia 76ers 113 | Boston Celtics 97 |
bref-202604280NYK | 2026-04-28 | playoff | Atlanta Hawks 97 | New York Knicks 126 |
bref-202604280SAS | 2026-04-28 | playoff | Portland Trail Blazers 95 | San Antonio Spurs 114 |
bref-202604290CLE | 2026-04-29 | playoff | Toronto Raptors 120 | Cleveland Cavaliers 125 |
bref-202604290DET | 2026-04-29 | playoff | Orlando Magic 109 | Detroit Pistons 116 |
bref-202604290LAL | 2026-04-29 | playoff | Houston Rockets 99 | Los Angeles Lakers 93 |
bref-202604300ATL | 2026-04-30 | playoff | New York Knicks 140 | Atlanta Hawks 89 |
bref-202604300MIN | 2026-04-30 | playoff | Denver Nuggets 98 | Minnesota Timberwolves 110 |
bref-202604300PHI | 2026-04-30 | playoff | Boston Celtics 93 | Philadelphia 76ers 106 |
bref-202605010HOU | 2026-05-01 | playoff | Los Angeles Lakers 98 | Houston Rockets 78 |
bref-202605010ORL | 2026-05-01 | playoff | Detroit Pistons 93 | Orlando Magic 79 |
bref-202605010TOR | 2026-05-01 | playoff | Cleveland Cavaliers 110 | Toronto Raptors 112 |
bref-202605020BOS | 2026-05-02 | playoff | Philadelphia 76ers 109 | Boston Celtics 100 |
bref-202605030CLE | 2026-05-03 | playoff | Toronto Raptors 102 | Cleveland Cavaliers 114 |
bref-202605030DET | 2026-05-03 | playoff | Orlando Magic 94 | Detroit Pistons 116 |
bref-202605040NYK | 2026-05-04 | playoff | Philadelphia 76ers 98 | New York Knicks 137 |
bref-202605040SAS | 2026-05-04 | playoff | Minnesota Timberwolves 104 | San Antonio Spurs 102 |
bref-202605050DET | 2026-05-05 | playoff | Cleveland Cavaliers 101 | Detroit Pistons 111 |
bref-202605050OKC | 2026-05-05 | playoff | Los Angeles Lakers 90 | Oklahoma City Thunder 108 |
bref-202605060NYK | 2026-05-06 | playoff | Philadelphia 76ers 102 | New York Knicks 108 |
bref-202605060SAS | 2026-05-06 | playoff | Minnesota Timberwolves 95 | San Antonio Spurs 133 |
bref-202605070DET | 2026-05-07 | playoff | Cleveland Cavaliers 97 | Detroit Pistons 107 |
bref-202605070OKC | 2026-05-07 | playoff | Los Angeles Lakers 107 | Oklahoma City Thunder 125 |
bref-202605080MIN | 2026-05-08 | playoff | San Antonio Spurs 115 | Minnesota Timberwolves 108 |
bref-202605080PHI | 2026-05-08 | playoff | New York Knicks 108 | Philadelphia 76ers 94 |
bref-202605090CLE | 2026-05-09 | playoff | Detroit Pistons 109 | Cleveland Cavaliers 116 |
bref-202605090LAL | 2026-05-09 | playoff | Oklahoma City Thunder 131 | Los Angeles Lakers 108 |
bref-202605100MIN | 2026-05-10 | playoff | San Antonio Spurs 109 | Minnesota Timberwolves 114 |
bref-202605100PHI | 2026-05-10 | playoff | New York Knicks 144 | Philadelphia 76ers 114 |
bref-202605110CLE | 2026-05-11 | playoff | Detroit Pistons 103 | Cleveland Cavaliers 112 |
bref-202605110LAL | 2026-05-11 | playoff | Oklahoma City Thunder 115 | Los Angeles Lakers 110 |
bref-202605120SAS | 2026-05-12 | playoff | Minnesota Timberwolves 97 | San Antonio Spurs 126 |
bref-202605130DET | 2026-05-13 | playoff | Cleveland Cavaliers 117 | Detroit Pistons 113 |
bref-202605150CLE | 2026-05-15 | playoff | Detroit Pistons 115 | Cleveland Cavaliers 94 |
bref-202605150MIN | 2026-05-15 | playoff | San Antonio Spurs 139 | Minnesota Timberwolves 109 |
bref-202605170DET | 2026-05-17 | playoff | Cleveland Cavaliers 125 | Detroit Pistons 94 |
bref-202605180OKC | 2026-05-18 | playoff | San Antonio Spurs 122 | Oklahoma City Thunder 115 |
bref-202605190NYK | 2026-05-19 | playoff | Cleveland Cavaliers 104 | New York Knicks 115 |
bref-202605200OKC | 2026-05-20 | playoff | San Antonio Spurs 113 | Oklahoma City Thunder 122 |
bref-202605210NYK | 2026-05-21 | playoff | Cleveland Cavaliers 93 | New York Knicks 109 |
bref-202605220SAS | 2026-05-22 | playoff | Oklahoma City Thunder 123 | San Antonio Spurs 108 |
bref-202605230CLE | 2026-05-23 | playoff | New York Knicks 121 | Cleveland Cavaliers 108 |
bref-202605240SAS | 2026-05-24 | playoff | Oklahoma City Thunder 82 | San Antonio Spurs 103 |
bref-202605250CLE | 2026-05-25 | playoff | New York Knicks 130 | Cleveland Cavaliers 93 |
bref-202605260OKC | 2026-05-26 | playoff | San Antonio Spurs 114 | Oklahoma City Thunder 127 |
bref-202605280SAS | 2026-05-28 | playoff | Oklahoma City Thunder 91 | San Antonio Spurs 118 |
bref-202605300OKC | 2026-05-30 | playoff | San Antonio Spurs 111 | Oklahoma City Thunder 103 |
bref-202606030SAS | 2026-06-03 | playoff | New York Knicks 105 | San Antonio Spurs 95 |
bref-202606050SAS | 2026-06-05 | playoff | New York Knicks 105 | San Antonio Spurs 104 |
bref-202606080NYK | 2026-06-08 | playoff | San Antonio Spurs 115 | New York Knicks 111 |
bref-202606100NYK | 2026-06-10 | playoff | San Antonio Spurs 106 | New York Knicks 107 |
bref-202606130SAS | 2026-06-13 | playoff | New York Knicks 94 | San Antonio Spurs 90 |
