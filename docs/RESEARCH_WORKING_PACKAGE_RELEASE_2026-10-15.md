# Выпуск рабочего исследовательского пакета, 2026-10-15

Отдельное дополнение к исходному ТЗ и handoff. Исходные документы сохранены.
Пакет из docs/RESEARCH_WORKING_PACKAGE_SPEC_2026-10-15.md технически выпущен.

## Ревизия и принятие

- [PR #471](https://github.com/Seiltanzer03/trading_dbd/pull/471).
- PR head: 2475fa8d374bdbc1f2649b81a81691386acfc9f3.
- Production/main: **724ce141e8ab7f0813d48c487d896e64fc249255**.
- Доставленный tree: 4daf3244bed01ae4fe76b747a76f4636f6eb1a61.
- [Полный PR CI](https://github.com/Seiltanzer03/trading_dbd/actions/runs/38677015182) и
  [main CI](https://github.com/Seiltanzer03/trading_dbd/actions/runs/38677468523):
  **4063 passed, 5 skipped, 13 subtests**, WebKit green, отдельный real pinned SQLite test passed.
- Независимое финальное review: Critical/Important — нет; 264 профильных теста.
  Собственный профиль интеграции — 202 passed. Причинное время и неполная
  схема альтернатив исправлены с воспроизведением RED → GREEN.
- [Exact-SHA deploy](https://github.com/Seiltanzer03/trading_dbd/actions/runs/38677722474):
  delivery, readiness, smoke, public HTTP и семь commit statuses success.
  Реальный ИИ POST: **HTTP 200, 8385 ms**; публичный терминал HTTP 200.
- [G1M local acceptance](https://github.com/Seiltanzer03/trading_dbd/actions/runs/38679380941):
  первый запрос 3280 ms превысил прежний лимит 3000 ms. Три диагностических
  чтения дали 1430/1915/1052 ms. После восстановления штатного pause-gate
  того же deploy/SHA выполнен один retry: **957 ms, success**. Лимит неизменен.
  Lease восстановлен через canonical helper; post-research pause проверена
  заново до retry. Пользовательские позиции и исполнения не менялись.

## Что работает

В существующий механизм ручного решения подключены явно обозначенные
предварительные мнения: momentum/давление по завершённым свечам и условная
сессионная гипотеза. Используются настроенный инструмент, реальные source
clocks, причинный cutoff и ограниченный набор уже имеющихся данных.

Для отсутствующих входов доступны отличающиеся по смыслу альтернативы:

| Вход | Альтернатива | Сохранённое ограничение |
| --- | --- | --- |
| Стакан | Давление тела свечи относительно диапазона | Не depth, OFI или брокерный tape |
| Consensus до новости | Официальное текущее значение относительно предыдущего CPI/NFP | Не сохранённое ожидание рынка и не восстановленный surprise |
| Своп | Сценарии 0/5/15% годовых, начисление за 1/3 дня | Не тариф/верхний предел брокера; проверенная экономика не перезаписана |

Предварительный policy factor .25 не является измеренной точностью.
Общий ценовой источник учитывается зависимо; контекст не попадает в observed
training. Приоритет реальных источников и обученных мнений сохранён.
Веса влияют на существующий общий selector; риск, издержки и допустимость
действий остаются обязательными, подтверждение исполнения остаётся ручным.

Исторические family heads выбирают DIRECT / INVERSE / BASELINE_SHRINK только
на ранних purged validation blocks. Final test используется после выбора.
Финально отрицательная модель не допускается автоматически. Legacy heads
сохраняют DIRECT; transform включён в fingerprint доставленного head.

Постоянное исправление возобновляемой загрузки Yandex интегрировано в canonical
restore. Manifest/hash/SQLite/source epoch/authority verification сохранены.

## Фактические публикации и предел завершения

[Exact-SHA math run](https://github.com/Seiltanzer03/trading_dbd/actions/runs/38679346138)
success: **25 fitted models, 17 working-supported**, опубликован
mathematical_edge_latest.json для 724ce14 (04:04:44 UTC, 404000 bytes).
Это модели по инструментам/семействам, а не 17 семейств и не доказательство net profit.
Sources 38679348366 и FOMC 38679350628 также success.

G1M attribution сохраняет effective N=8 по 165 разрешённым окнам и maturity
INSUFFICIENT. Автоматическое обучение весов, promotion и торговые приказы
из этого результата не разрешаются.

По состоянию **04:12 UTC** штатный следующий EDE inventory
[38680095947](https://github.com/Seiltanzer03/trading_dbd/actions/runs/38680095947)
ещё выполнялся. Его новый discovery/ActiveEdge результат здесь не заявляется.
Последнее измерение root free: 922353664 bytes (04:07 UTC), ниже отдельного
research reserve 1 GiB; следующий offload должен пройти свои реальные проверки.
Резерв не ослаблялся, authoritative DB/WAL/история не удалялись.
Новый scheduler и дополнительные провайдеры не добавлялись.

Граница данного пакета закрыта принятой технической версией и перечисленными
реальными публикациями. Восемь прогнозных семейств не объявляются полностью
эмпирически подтверждёнными; исходный глобальный план и evidence limits сохранены.
