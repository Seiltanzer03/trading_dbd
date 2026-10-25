# Приёмка представления весов, вкладов и исключений экспертов (Gemini Flash)

Дата: 25 октября 2026. Часовой пояс: Europe/Moscow.  
Репозиторий: `https://github.com/Seiltanzer03/trading_dbd`  
Роль: Дополнительный исполнитель. Интегратор: Codex.

---

## 1. Контрольная точка и ветка

- **Baseline main SHA:** `c5f14a3c0a2a1411460404c5152bd050fe0bed5a`
- **Baseline Git tree:** `1bf8640edfdb8740d4eddb6b0a4f05f01e0680c2`
- **Рабочая ветка:** `task/flash-presentation-acceptance-20261025`
- **Локальный HEAD SHA:** зафиксирован после коммита в ветке `task/flash-presentation-acceptance-20261025`
- **PR:** подготовлен локально, draft PR готов к отправке интегратору без merge/deploy со стороны Flash.

---

## 2. Проверенный scope и затронутые файлы

Проверены 10 сценариев достоверности представления весов, вкладов и причин исключения экспертов на уровне compact payload, консольного текстового отчёта и браузерного интерфейса управления.

Изменённые файлы (в пределах разрешённого scope):
1. `seiltanzer/web/js/management_ui.js` — исключено приведение некорректных нечисловых типов (`boolean`, `object`) к `0`/`1` в `finiteNumber`.
2. `tests/web/unified_edge_audit_smoke.mjs` — кроссплатформенное удаление `import` (совместимость с `\r?\n`) и регрессионный тест на недопустимость отображения `boolean` как `0.0%`.
3. `tests/test_unified_edge_audit.py` — зеркальный тест на уровне Python (`test_non_numeric_and_boolean_types_not_coerced_to_numbers`), проверяющий вывод `— → —` вместо `0.0% → 0.0%`.
4. `docs/reviews/2026-10-25-flash-presentation-acceptance.md` — настоящий отчёт.

Файлы расчётной логики (`seiltanzer/unified_edge_ensemble.py`, `seiltanzer/edge_family_working.py`, `seiltanzer/decision_research.py` и др.) **не изменялись**.

---

## 3. Таблица сценариев приёмки (Раздел 5 ТЗ)

| № | Сценарий ТЗ | Проверенная функция / поле JSON | Статус | Уровень доказательства |
|---|---|---|---|---|
| 1 | **Различие номинальной доли, фактической доли, score и contribution.** Нулевой вклад при ненулевой доле допустим. Сумма долей не нормализуется искусственно до 100%. | `components[].nominal_weight` vs `components[].effective_weight`; `candidates[].score` (безразмерный); `candidates[].component_contributions[].contribution`. | **PASS** | `tests/test_unified_edge_audit.py` (`test_report_exposes_economic_tradeoff_without_turning_score_into_profit`), `tests/web/unified_edge_audit_smoke.mjs` (строки 79–85). Synthetic fixture / audit payload. |
| 2 | **Малый ненулевой вес виден (0.00046 → 0.046%).** Не округляется до 0.0% и не теряется. | `_pct(value)` в `unified_edge_audit.py`, `percent(value)` в `management_ui.js`. | **PASS** | `test_llm_quality_basis_and_small_positive_weights_survive_report` (`assert '0.046%' in rendered`), `unified_edge_audit_smoke.mjs` (`3.8%`). Synthetic fixture. |
| 3 | **Нет данных ≠ нулевому мнению.** `null`, missing, abstention, ноль и отрицательный вклад не смешиваются. Некорректные типы (`boolean`, `object`) не превращаются в числа. Полные placeholder-строки безопасно сокращаются. | `_num(value)` в Python; `finiteNumber(value)` в JS; `compact_unified_ensemble` (`null_working_family_rows_omitted`). | **PASS** (после фикса) | `test_compact_preserves_zero_opinions_nonzero_effects_and_additional_diagnostics`, `test_non_numeric_and_boolean_types_not_coerced_to_numbers`, регрессия в `unified_edge_audit_smoke.mjs`. RED → GREEN зафиксирован. |
| 4 | **Недоступность и зависимость не теряются.** Причины missing/stale/future/mapping/proxy/lineage/withheld сохраняются во всех таблицах. | `components[].reason`, `suppression_reasons`, `freshness_multiplier`, `dependence_multiplier`; `candidates[].ranking_reason`, `admission_reason`; `edge_families[].reason`. | **PASS** | `unified_edge_audit_smoke.mjs` (строки 80–86, 113, 157), `test_audit_keeps_all_actions_and_exclusions_without_expensive_paths`. Synthetic fixture. |
| 5 | **Исторический forecast и текущая working assessment разделены.** `forecast_available=false` сосуществует с `working_assessment_available=true` без дублирования current_llm. | `edge_families[].forecast_available` vs `edge_families[].working_assessment_available`; `working_effective_weight` (доля LLM); `market_predictions`. | **PASS** | `test_family_audit_distinguishes_observed_input_from_missing_forecast`, `unified_edge_audit_smoke.mjs` (строки 60–67, 87–90). Поля разделены в отдельных колонках и строках. |
| 6 | **HOLD-only честен.** Если все вмешательства отклонены, отображается явное предупреждение о том, что это единственное допустимое действие, а не доказанное преимущество удержания. | `selection_context()` (`HOLD_ONLY_ADMISSIBLE`), класс `ai-execution-warning` в `management_ui.js`. | **PASS** | `unified_edge_audit_smoke.mjs` (строки 106–121: позитивный и 3 негативных теста неполноты/усечения), `unified_edge_audit.py` (строка 293). |
| 7 | **Counterfactual scope сохранён.** Разделены `counterfactuals` (компоненты), `family_counterfactuals` (рабочие LLM-оценки семейств) и `market_family_counterfactuals` (обученные модели). | 3 независимые таблицы/списка: `excluded_component_id`, `excluded_family_id` (working), `excluded_family_id` (market). | **PASS** | `unified_edge_audit_smoke.mjs` (строки 59–63, 200–234), `unified_edge_audit.py` (строки 399–434). Ablation не пересчитывается, отображает замороженный JSON. |
| 8 | **Действия и экономика не искажаются.** Все кандидаты/параметры различимы; score безразмерен; Expected/CVaR/ΔR имеют размерность 'R'. Дисклеймер об отсутствии доказанной прибыли на месте. Факт выбора отделён от архива. | `candidates[].parameters`, `expected_net_r`, `cvar10_net_r`, `score`; `mountArchivedManagement` (`execution_allowed: false`). | **PASS** | `test_report_exposes_economic_tradeoff_without_turning_score_into_profit`, `unified_edge_audit_smoke.mjs` (строки 84, 85, 122–139). |
| 9 | **Чужой текст безопасно отображается.** HTML/скрипты в reason/instruction_ru выводятся как текст (`textContent`), не исполняются. | `management_ui.js` (`Element.textContent`, `Element.appendChild`, отсутствие `innerHTML`). | **PASS** | `unified_edge_audit_smoke.mjs` (строки 91, 103–105, 133–139: проверка `<img src=x onerror=alert(1)>` и `<script>unsafe</script>`). |
| 10 | **Вход не изменяется.** Compactor и renderer не дописывают веса, не меняют ranking/выбор/времена и не мутируют переданный JSON. | `compact_unified_ensemble` не изменяет исходный объект; `mountUnifiedEdgeEnsemble` работает read-only. | **PASS** | `test_compact_omits_96_null_opinion_placeholders_without_losing_actions_or_family_readiness` (`assert raw == before`). |

---

## 4. Исправленный дефект представления: некорректное приведение нечисловых типов в `finiteNumber`

### Проблема и воспроизведение
В `seiltanzer/web/js/management_ui.js` функция `finiteNumber(value)` была реализована как:
```javascript
function finiteNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
```
В стандарте JavaScript `Number(false) === 0`, `Number(true) === 1`, `Number([]) === 0`.  
Если во входном объекте компонент имел булево значение в числовом поле (например, `nominal_weight: false`, `effective_weight: false`, `quality: false`), `finiteNumber(false)` возвращал `0`. В результате `percent(false)` отображал `"0.0%"` (что эквивалентно явному нулевому весу), а `signed(false)` отображал `"+0.000"` (что эквивалентно реальному нулевому скору/вкладу), смешивая отсутствие данных с нулевым мнением (нарушение пункта 3 ТЗ).

Кроме того, в тестовом harness `tests/web/unified_edge_audit_smoke.mjs` строка:
```javascript
.replace(/^import .*;\n/gm, '')
```
не учитывала символы возврата каретки (`\r\n`) на Windows-окружении, из-за чего ES-модуль не вырезал `import { mountG1SEvidencePanel } from './g1s_evidence.js';` и завершался ошибкой `ERR_UNSUPPORTED_RESOLVE_REQUEST` при запуске `node tests/web/unified_edge_audit_smoke.mjs`.

### Минимальное исправление
1. В `seiltanzer/web/js/management_ui.js`:
```javascript
function finiteNumber(value) {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean' || typeof value === 'object') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
```
2. В `tests/web/unified_edge_audit_smoke.mjs`:
- Регулярное выражение обновлено до `.replace(/^import .*;\r?\n/gm, '')`.
- Добавлена регрессионная проверка:
```javascript
const booleanTypes = new Element();
mountEdgeManagement(booleanTypes, {unified_edge_ensemble:{...audit,
  components:[{component_id:'current_llm',nominal_weight:false,effective_weight:false,quality:false}]}});
assert.doesNotMatch(booleanTypes.textContent, /0\.0% → 0\.0%/,
  'boolean types must not be coerced to 0.0% numbers');
```
3. В `tests/test_unified_edge_audit.py`:
- Добавлен зеркальный тест `test_non_numeric_and_boolean_types_not_coerced_to_numbers()` для верификации поведения Python-слоя (`_num`, `_pct`), возвращающего `— → —` вместо `0.0% → 0.0%`.

### Почему исправление относится только к представлению
- Не затрагивает расчёт весов, CVaR, нормализацию или выбор действия.
- Не меняет внешние контракты или схему JSON.
- Устраняет искажение нечисловых/отсутствующих значений в человекочитаемом UI.

---

## 5. Фактические результаты тестовых прогонов

Команды выполнены локально в окружении проекта из корня репозитория:

1. **Python pytest (профильный набор):**
```bash
.\.venv\Scripts\python.exe -m pytest -q tests/test_unified_edge_audit.py
```
**Результат:** `16 passed in 0.58s` (exit code: 0).

2. **Node.js smoke harness:**
```bash
node tests/web/unified_edge_audit_smoke.mjs
```
**Результат:** `Unified edge audit rendering smoke: PASS` (exit code: 0).

---

## 6. Что осталось неподтверждённым (границы приёмки)

- **Production live call:** реальный вызов OpenRouter LLM не выполнялся (paid provider calls = 0), открытые сделки на брокере не проверялись (рынок закрыт).
- **Свежие рыночные котировки:** текущая свежая live-котировка брокера не подтверждалась.
- **EDE run 40179294827:** отменён по лимиту 300 минут на этапе S3 upload; повторный запуск тяжёлой цепочки остаётся у интегратора (Codex).
- **Прибыльность моделей:** проверено исключительно отображение оценок, весов и причин в UI/отчёте; реальная торговая доходность сценариями не подтверждается.

---

## 7. Связанные наблюдения вне scope пакета

1. **Форматирование вероятности и пула в `unified_edge_audit.py` (строки 417–420):**  
   В строке 420 `Вес общего математического пула {_format(row.get('model_pool_effective_weight'))}` использует `_format` (выводит знаковое число `+0.040`) вместо `_pct` (вывод `4.0%`), в то время как JS-слой (`management_ui.js` строка 218) выводит `пул 4.0%`. Поскольку это консольный генератор строк `render_unified_ensemble_lines`, а не UI-рендер, расчётной ошибки нет; расхождение передаётся интегратору для возможной унификации.

---

## 8. Подтверждение отсутствия несанкционированных операций

- Production writes: 0
- Broker live orders: 0
- Paid provider / LLM calls: 0
- Full database exports / scans: 0
- Cloud / Yandex Object Storage mutations: 0
- GitHub Workflow dispatches: 0
