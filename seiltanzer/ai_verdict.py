"""On-demand AI review of the current decision state.

The API key is server-side only. Rendering arrays are summarized, while every
decision metric used by the terminal is preserved in the prompt.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


SYSTEM_PROMPT = """Ты — риск-менеджер и второй пилот терминала Seiltanzer.
Анализируй текущую открытую сделку как статистическую реализацию стратегии, а
не как обещание результата. Нормальные стопы, тейки и безубытки — часть
распределения; не паникуй и не пиши общие предупреждения. Не придумывай данные.
Вероятность сделки берётся только из option-anchored first-passage модели, не
из простой пропорции стоп/тейк. Отличай live цену, delayed options, proxy,
эвристический OI/GEX и наблюдаемые метрики. Никогда не предлагай расширить стоп,
усреднить убыток или нарушить план. Учитывай конкретный сетап, его статистику,
фильтры, фазу счёта, лестницу 10% на рубежах и БУ после 1.5R.

Ответ по-русски, конкретно:
1) ВЕРДИКТ СЕЙЧАС — держать / держать с контролем / частично фиксировать /
перевести в БУ / готовиться принять стоп.
2) СОСТОЯНИЕ — цена в R, импульс, дистанции, качество фидов.
3) ПРЕИМУЩЕСТВО — options P, EV=0, edge и изменение edge с открытия.
4) КОНТЕКСТ — IV/RV, skew, term, gamma/OI, корреляции и фильтры.
5) ПЛАН — что делать сейчас и точные триггеры следующего действия.
6) ЧТО СЛОМАЛО БЫ СЦЕНАРИЙ — только измеримые условия.
Не выдавай категоричный прогноз и не повторяй дисклеймер."""


def _compact(value: Any, depth: int = 0) -> Any:
    """Keep semantic metrics, summarize only bulky rendering samples."""
    if depth > 7:
        return "…"
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in {"hist", "paths", "surface", "surfData", "rvData"}:
                if isinstance(item, list):
                    out[key + "_count"] = len(item)
                continue
            if key in {"strikes", "ivs", "bins", "rn_cone"} and isinstance(item, list):
                nums = [float(x) for x in item if isinstance(x, (int, float))]
                out[key + "_summary"] = {
                    "count": len(item),
                    "min": min(nums) if nums else None,
                    "max": max(nums) if nums else None,
                    "first": item[:3],
                    "last": item[-3:],
                }
                continue
            out[key] = _compact(item, depth + 1)
        return out
    if isinstance(value, list):
        if len(value) > 40:
            return {
                "count": len(value),
                "first": [_compact(x, depth + 1) for x in value[:5]],
                "last": [_compact(x, depth + 1) for x in value[-5:]],
            }
        return [_compact(x, depth + 1) for x in value]
    return value


def build_snapshot(engine) -> dict:
    tick = engine.tick_payload()
    trade = tick.get("trade")
    setup = None
    if trade:
        from .config import SETUPS
        cfg = SETUPS.get(int(trade.get("setup") or 0))
        if cfg:
            setup = {
                "num": cfg.num, "name": cfg.name, "instrument": cfg.instrument,
                "builtin_n": cfg.n, "builtin_wins": cfg.wins,
                "builtin_winrate": cfg.winrate, "target_rr": cfg.rr,
                "filters": list(cfg.filters),
                "journal_stats": engine.journal.setup_stats(
                    cfg.num, engine.settings.journal_min_trades).__dict__,
            }
    return {
        "captured_tick": _compact(tick),
        "active_setup": setup,
        "options_landscape": _compact(engine.ridge_payload()),
        "validation": _compact(engine.journal.validation_report()),
    }


def request_verdict(snapshot: dict) -> dict:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY не настроен на сервере")
    model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    body = json.dumps({
        "model": model,
        "temperature": 0.25,
        "max_tokens": 1400,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Снимок терминала в момент нажатия:\n"
             + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))},
        ],
    }, ensure_ascii=False).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://seiltanzer-terminal.local",
            "X-Title": "Seiltanzer Terminal",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode(errors="replace")
        raise RuntimeError(f"OpenRouter HTTP {exc.code}: {detail}") from exc
    content = result.get("choices", [{}])[0].get("message", {}).get("content")
    if not content:
        raise RuntimeError("OpenRouter вернул пустой ответ")
    return {"verdict": content, "model": result.get("model", model),
            "captured_ts": snapshot.get("captured_tick", {}).get("ts")}
