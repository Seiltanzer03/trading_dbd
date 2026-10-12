# ИИ-разбор: очередь рабочего потока

После PR455 delivery20c44d20 и readiness прошли; deploy38238964086 не принят:
functional smoke превысил транспортный предел15сек на /api/ai/verdict.
Trace ai-3904b4fde46f4e3dabfb показывает фактическийHTTP200 за16827ms.
Ожидание shared executor: family2194ms, provider3287ms, ensemble3150ms,
publication1864ms. Это отдельный наблюдённый дефект задержки, а не отказ цены
или незавершение research core (первый core285514ms, last_error=null).

Конечное исправление: один выделенный worker для последовательных стадий
ИИ-разбора. Рыночные fetch-задачи сохраняют прежний executor; trace/contextvars
и исходные ошибки сохраняются. Admission lock не отпускается до завершения
фактического synchronous future, даже если отменён HTTP await. Дополнительная
очередь не накапливается; новый запрос получает429 ai_request_in_progress.
Пределы smoke/HTTP, условия допуска цены/действий и рисковые gates не увеличены.

Проверки: исходная задержка reproduced RED с заблокированным default executor;
cancel/admission regression также RED. Финальный профиль58passed/1existingwarning
17.44s включает настоящий ASGI endpoint под полностью занятой фоновой очередью,
отказ до provider/publication при занятом worker и прежние trace/cancel/API tests.
Независимый review нашёл одинImportant: внутренние provider/journal handlers
перехватывали busy admission как обычную ошибку. Три API-regressions сначалаRED,
затемGREEN после явного re-raise; иных findings нет. Финальный полныйCI и
production smoke новогоSHA обязательны.
Ранее3836tests на PR456 проверяли только инвентаризацию; они не выдаются за
проверку этого добавленного исправления.
