# Frozen benchmark image

`docker build --target benchmark` (see the Dockerfile) builds the solver image the powertwin-solver-benchmark repository
pins by digest: no fast estimate (surrogate package, serving module, ML dependencies), no demo data or logs, and the
weather files of every Teton County building's station baked into `app/urbanopt/weather_files`, so a run needs no
network.

`teton_weather.sha256`: the EnergyPlus TMY3 files (EPW, DDY, STAT) of the two stations the Teton stock resolves to,
Jackson Hole (17,118 buildings) and Cody (121), as downloaded from the EnergyPlus weather bucket by the runs that
produced the benchmark's results. The build refuses files that do not match.
