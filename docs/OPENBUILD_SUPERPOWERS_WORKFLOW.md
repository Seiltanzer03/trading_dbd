# Workflow разработки unified edge

Применяем к существующему ТЗ подход OpenBuild 2.4.1 и Superpowers:
инвентаризация → сверка требований → независимые задачи → последовательная
интеграция → проверки → review → разрешённый выпуск → production-подтверждение.

Источники:
- [OpenBuild 2.4.1](https://github.com/GeorgVahi/OpenBuild/tree/v2.4.1),
  README, правила parallel task lanes и TDD workflow.
- [Superpowers](https://github.com/obra/superpowers/tree/8ca22dba9a94f28898bbce59f2537ff4d87c747d),
  dispatching-parallel-agents, executing-plans, systematic-debugging,
  test-driven-development и verification-before-completion.

Это адаптация процесса к доступным средствам проекта. Запуск CLI coordinator,
его private lease receipts, cgroup containment и автоматическое переключение
моделей не заявляются. Пользовательская конфигурация моделей не изменяется.

## Источник требований и baseline

Основное ТЗ: `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md` — копия
предоставленного пользователем плана. Матрица выполнения и остатки:
`docs/UNIFIED_EDGE_DEVELOPMENT_ROADMAP.md`.

Production baseline: main `229e5dd5dd5f5b6af622c46c3be040898ee155c6`,
tree `15208c1f86cb7c55bc3ab1f25e7b3962297e433d`.
Локальная база: `5f21d94`, то же содержимое tree. Не путать различие истории
публикации с отличием исходного кода. Ветка: `feat/unified-edge-full-integration`.

## Независимые направления и интеграция

| Направление | Область | Критерий принятия |
| --- | --- | --- |
| Реестр экспертов | registry, ensemble, audit, UI | Явный общий бюджет 100%, новые эксперты и ablations видимы, risk gates неизменны |
| Точные бары | feed provenance, passive archive, math export/cache | Только фактически полученные completed bars с точным configured symbol; старые labels не переименовываются |
| Источники | bounded collector и source workflow | Два фактических book observations для sampled flow; timestamps, venue/proxy и expiry честны |
| Издержки и исполнения | pinned broker cost import, ACK telemetry | Неизвестные расходы остаются null; ACK time не выдумывается как broker fill time |
| Интеграция | API hook, snapshot, model import, docs | Единственный рабочий decision path; malformed economics не публикует старое решение как новое |

В новом направлении агент получает короткий самостоятельный brief, точный
scope, запрет Git/remote writes и проверяемый результат. Независимые писатели
используют отдельные worktrees/ветки и изолированные тестовые DB. В общем
worktree одновременно пишет только один владелец; reviewers остаются read-only.
Завершённые изменения принимает один integrator в согласованном порядке.
Не создаём лишние ветки для взаимозависимых изменений одного контракта.

В начальной параллельной работе изменения уже появились в общем worktree.
Они сохранены и проверяются; не пересоздаём их ради формального перехода на
другой runner. После остановки агентов запись в текущем tree выполняет root.

## Проверки и review

Для поведенческого дефекта сначала воспроизводим первичный отказ, затем
фиксируем regression criterion, исправляем владеющий слой и проверяем его.
Не скрываем ошибки timeout-увеличением, фиктивной authority или исключением
проверок. Документы проверяем структурно без искусственных тестов.

Профильные тесты выполняются на конкретном diff. После интеграции запускаем
полный обязательный Python/frontend/WebKit CI на финальном tree. Reviewer
читает diff и критерии ТЗ, сообщает проверяемые findings, не редактирует код.
Изменение после review/CI требует проверок изменённой области и нового exact
revision evidence. Тот же тяжёлый CI не перезапускаем без причины.

Если агент остановлен лимитом или инфраструктурой, его частичный результат
не считается завершённым. Сначала сохраняем diff, подтверждаем отсутствие
активного writer, затем integrator завершает допустимый scope. Не создаём
нового конкурирующего writer и не заявляем независимый review, если он не
получен.

## Выпуск и продолжение

Пользователь разрешил push, PR и merge с автодеплоем после полного green CI.
Эта authority сохраняется; повторное согласование обычного выпуска не нужно.
Перед merge проверяем текущую main, final PR head/tree и обязательные checks.
После merge проверяем точный SHA, deploy, readiness, functional smoke, public
HTTP и относящиеся к этому SHA публикации источников/математических моделей.

В roadmap записываем factual completion, тесты, SHA/PR и конкретные блокеры.
Техническая интеграция и эмпирическая эффективность — разные этапы.
Неполученный licensed consensus, executing-broker costs, exact crypto archive
или validated family model не становятся «готовыми» из-за green CI.
