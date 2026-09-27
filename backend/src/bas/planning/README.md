# Расчетное ядро

`service.py` — точка входа `plan()`: воздушное пространство (`airspace.py`), свободное
пространство и пути (`geometry.py`), галсы (`coverage/`), распределение по вылетам,
модели полета (`flight_models/`), расписание и зарядка (`scheduling/`), эшелоны
(`echelons.py`), резервные площадки (`validation/`). Рядом — анализ парка (`analysis.py`),
оценка задания (`estimate.py`) и восход/закат (`sun.py`). Алгоритм целиком —
[ALGORITHM.md](../../../../docs/ALGORITHM.md), устройство сервиса —
[ARCHITECTURE.md](../../../../docs/ARCHITECTURE.md).
