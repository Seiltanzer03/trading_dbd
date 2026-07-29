# ПРОМТ: Аналитика уровня хедж-фонда + живые анимации для Seiltanzer Terminal

> Для AI-агента (Claude, Cursor, Windsurf и т.д.).
> Скопируй целиком и вставь в чат с агентом.

---

## ФИЛОСОФИЯ

Ты реализуешь **12 аналитических модулей** для торгового терминала Seiltanzer.
Каждый модуль должен ощущаться **живым** — как организм, который дышит данными.
Не статичные графики с обновлением раз в секунду, а **непрерывный поток**:
переливы цвета, пульсации, затухающие волны, перетекающие градиенты, частицы.

**Ориентиры визуального стиля** (не копировать, а вдохновляться):
- Bloomberg Terminal + неоновая эстетика киберпанка
- Панели управления из фильма «Прибытие» (Arrival) — органические формы данных
- Визуализации NASA для потоков плазмы — плавные цветовые переходы
- Музыкальные визуализаторы Winamp/Milkdrop — реакция на «ритм» рынка
- Приборные панели SpaceX — минимализм + точность + живость

**Принцип:** Терминал должен выглядеть так, что если трейдер покажет его коллеге,
тот скажет «что это за инструмент? хочу такой же». Это маркетинг через визуал.

---

## ТЕКУЩИЙ СТЕК И АРХИТЕКТУРА

### Что уже есть и работает

```
seiltanzer/
├── __init__.py              # v0.1.0
├── __main__.py              # CLI: --demo, --stream, --check, --host, --port, --data-dir
├── app.py                   # FastAPI + WS /ws (пушит tick_payload каждые 1-2с)
├── engine.py                # Движок: tick_payload(), ridge_payload(), verdict
├── config.py                # INSTRUMENTS (10 шт), SETUPS (1-16), VOL_INDEX_TICKERS
├── journal.py               # SQLite trades.db
├── check.py                 # --check
├── core/
│   ├── options.py           # implied_move, bl_density, gex_profile, skew, term_structure
│   ├── prob.py              # first_passage, MC, cone_surface
│   └── risk.py              # risk_matrix, atr_phase, efficiency
├── data/
│   ├── cache.py             # SQLite cache.db + chain_snapshots
│   ├── feeds.py             # MarketData (yfinance REST + Yahoo WS)
│   └── stream.py            # StreamHub → wss://streamer.finance.yahoo.com
└── web/
    ├── index.html           # Single-page dashboard
    ├── css/terminal.css     # Бумажно-терминальная тема, IBM Plex Mono
    ├── vendor/plotly-gl3d.min.js  # Plotly WebGL (для 3D cone)
    └── js/
        ├── app.js           # WS-клиент, рендер-цикл, модалки (53KB)
        ├── lattice.js       # Доска вероятности — Canvas 2D + шары Гальтона (60fps)
        ├── ridge.js         # Strike Landscape — Canvas 2D (изометрическая гряда)
        ├── cone.js          # 3D-конус — Plotly gl3d WebGL
        ├── levels.js        # Карта уровней — Canvas 2D + частицы к гамма-магниту
        ├── anim.js          # Анимации: approach() (экспоненциальный lerp), tweenNumber()
        └── util.js          # DOM-хелперы, форматтеры, tooltip, setupCanvas(hiDPI)
```

### Поток данных

```
Backend (Python):
  yfinance / Yahoo WS → feeds.py → engine.py → tick_payload() JSON
                                                    ↓
Frontend (JS):                              WebSocket /ws (каждые 1-2с)
                                                    ↓
  app.js → парсит JSON → вызывает render* для каждого блока
              ↓
  lattice.js / ridge.js / cone.js / levels.js → Canvas 2D / Plotly 3D
```

### Существующие анимации (anim.js)

```javascript
// Экспоненциальный lerp — текущее значение плавно «тянется» к целевому
function approach(current, target, speed = 0.08) {
    return current + (target - current) * speed;
}
// Используется для всех числовых значений: цена, P%, r-координата и т.д.
// 60fps requestAnimationFrame loop в каждом Canvas-компоненте
```

### Ключевые данные в tick_payload

```javascript
{
  ts, demo, instrument,
  feeds: { price: {value, status, ts}, chain: {...}, daily: {...}, vols: {vix, gvz, ...} },
  account: { balance, risk: {risk_pct, target_rr, mode, phase} },
  atr: { ratio, phase, k, rr_mult, atr_abs },
  sigma: { ratio, sigma_implied, sigma_baseline, applied, source },
  regime: { trend_sigma, vol_cluster, realized_vol, phase },
  trade: { id, setup, direction, entry, stop, take, max_r, ... },
  prob: { r, T, p, p_lo, p_hi, winrate, sigma_R, board_sigma_R, ... },
  mc: { p_take, p_stop, ev_hold, ev_ladder, hist, ... },
  ladder: { rungs, crossed, be_armed, max_r },
  market: { probs, edges, hit_ratio, edge, p_model, ... },
  gamma: { zone, net_at_price, strength, magnet, toward, flip, ... },
  cone: { surface, walls, market_terminal, ... },
  levels: { price, entry, stop, take, zones, vwap, implied_band, gex, ... },
  options_summary: { implied_move_frac, sigma_annual, session_band_abs, skew, term, ... },
  verdict: { label, tone, score, edge, factors, action },
  filters: [ {key, label, state, value, ...}, ... ],
  state: { r, T, to_take_r, to_stop_r, p, edge, headline, ... },
}
```

---

## ТЕХНОЛОГИИ И МЕТОДЫ АНИМАЦИИ

> [!IMPORTANT]
> Это **направления и идеи**, не жёсткие рамки. Ты волен выбрать ЛУЧШИЙ подход —
> может быть, ты знаешь библиотеку или технику, которая даст результат круче.
> Главное — результат должен быть визуально ПОТРЯСАЮЩИМ и плавным (60fps).

### Canvas 2D (основной инструмент)

Проект уже активно использует Canvas 2D с 60fps requestAnimationFrame.
Это основа для большинства модулей. Ключевые техники для живых анимаций:

**Методы «оживления» Canvas:**
- **Gradient Mesh Animation**: создавай сложные градиенты через несколько `createRadialGradient()` с анимированными центрами и радиусами. Центры градиентов медленно дрейфуют по синусоиде — создаёт эффект «дыхания»
- **Trail / Afterglow**: не очищай canvas полностью — рисуй полупрозрачный прямоугольник поверх (`fillStyle = 'rgba(0,0,0,0.03)'`) перед новым кадром → старые данные плавно угасают, оставляя «шлейф»
- **Perlin Noise Flow Fields**: генерируй поле шума (simplex noise) и двигай по нему частицы → органический «поток» данных. Библиотеки: `simplex-noise` (npm), или реализуй 2D simplex за 50 строк
- **Spring Physics**: вместо линейного lerp используй пружинную модель (mass-spring-damper) → значения «пружинят» при резком изменении, создавая ощущение инерции
- **Bloom / Glow Effect**: рисуй яркие элементы дважды — чётко и размыто (увеличенный `shadowBlur`). Или используй второй offscreen canvas с blur-фильтром, наложенный в `screen` blend mode
- **Particle Systems**: частицы с позицией, скоростью, временем жизни, цветом. Они рождаются, летят, угасают. Для данных: частица = тик цены, её траектория = движение к GEX-магниту

### WebGL / GPU-ускоренные эффекты

Для самых тяжёлых визуализаций (IV Surface, GEX Heatmap):

- **Plotly gl3d**: уже подключён (`vendor/plotly-gl3d.min.js`). Используй для 3D-поверхностей.
  Plotly поддерживает анимацию через `Plotly.animate()` с transition duration
- **Raw WebGL + GLSL шейдеры**: если нужна кастомная heatmap с анимацией — написать
  fragment shader, который интерполирует цвета на GPU. Невероятно быстро и красиво.
  Пример: передай данные как текстуру, шейдер делает плавный gradient mapping + time-based wave
- **Three.js**: если Plotly ограничивает — Three.js даёт полный контроль. Но это тяжёлая
  библиотека, используй ТОЛЬКО если Plotly и raw Canvas не хватит
- **OffscreenCanvas + Web Workers**: если анимация жрёт CPU — вынеси рендер в Worker

### CSS-анимации (для элементов UI)

Для бейджей, индикаторов, фоновых эффектов:

- **CSS Houdini / @property**: анимируй CSS custom properties (`--glow-hue`, `--pulse-scale`)
  для плавных переходов цвета фона
- **backdrop-filter**: `blur() saturate()` на полупрозрачных панелях → glassmorphism
- **@keyframes с steps**: пульсация статус-индикаторов (● LIVE, ● ЦЕПОЧКА)
- **mix-blend-mode**: `screen` или `overlay` для наложения свечения поверх Canvas
- **conic-gradient + animation**: для Session Clock — анимированный конический градиент

### Математические основы анимаций

**Не используй линейные переходы.** Всё в природе нелинейно:

```
// Вместо: value += (target - value) * 0.1;
// Используй одно из:

// 1. Пружина (damped harmonic oscillator):
velocity += (target - value) * stiffness - velocity * damping;
value += velocity;

// 2. Easing с отскоком:
function easeOutElastic(t) {
    return Math.pow(2, -10*t) * Math.sin((t - 0.075) * 2*PI / 0.3) + 1;
}

// 3. Smoothstep (для градиентов):
function smoothstep(edge0, edge1, x) {
    let t = Math.max(0, Math.min(1, (x - edge0) / (edge1 - edge0)));
    return t * t * (3 - 2 * t);
}

// 4. Noise-modulated oscillation (для «дыхания»):
value = base + amplitude * Math.sin(time * freq) * (0.5 + 0.5 * noise(time * 0.1));
```

---

## 12 МОДУЛЕЙ: ЧТО ДЕЛАТЬ И КАК АНИМИРОВАТЬ

### МОДУЛЬ 1: 🌡️ VRP Термометр (Volatility Risk Premium)

**Суть:** Разница implied vol − realized vol. Самый стабильный edge в опционах.

**Данные (Python, engine.py):**
```python
# Добавить в tick_payload:
def _vrp_payload(self) -> dict:
    iv = self.market.chain.get("metrics", {}).get("implied_move", {}).get("sigma_annual")
    rv = self.market.baseline_vol()
    if iv is None or rv is None:
        return {"available": False}
    vrp = iv - rv
    vrp_pct = vrp / rv if rv > 0 else 0
    # Историю VRP можно хранить в cache для тренда
    return {"available": True, "iv": iv, "rv": rv, "vrp": vrp,
            "vrp_pct": vrp_pct, "regime": "перегрев" if vrp > 0.05 else
            "недооценка" if vrp < -0.03 else "норма"}
```

**Анимация — «плазменный термометр»:**
- Вертикальная или горизонтальная шкала, заполненная **анимированным градиентом**
- Градиент медленно «течёт» вдоль шкалы (смещение phase через sin(time))
- При VRP > 0 (перегрев): тёплые цвета (оранжевый→красный), частицы «тепла» поднимаются вверх
- При VRP < 0 (недооценка): холодные (синий→фиолетовый), частицы «льда» опускаются
- При VRP ≈ 0: нейтральный серо-голубой, спокойное «дыхание»
- **Marker** текущего значения: пульсирующий кружок с glow-эффектом и afterglow-шлейфом
- **Числа** implied/realized: плавно tween'ятся при обновлении (spring physics)
- **Фон**: subtle noise-текстура, медленно мерцающая

**Идея для исследования:** посмотри как визуализаторы температуры работают в weather apps (Apple Weather, Dark Sky) — плавные цветовые переходы между зонами.

---

### МОДУЛЬ 2: 📐 IV Surface (Implied Volatility Surface)

**Суть:** 3D-поверхность волатильности: Strike × Days-to-Expiry × IV. Главный инструмент опционных деск.

**Данные (Python, feeds.py):**
```python
# Расширить refresh_chain: вместо 1 экспирации → 5-6 ближайших
# Для каждой: strikes, call_iv, put_iv → матрица IV[expiry][strike]
# Передать как iv_surface в tick_payload

def refresh_iv_surface(self) -> None:
    """Сетка IV для нескольких экспираций → поверхность."""
    proxy = self.instrument.options_proxy
    if not proxy: return
    import yfinance as yf
    t = yf.Ticker(proxy)
    expiries = list(t.options)[:6]
    # ... собрать матрицу IV[i_expiry, j_strike]
```

**Анимация — «живая топографическая карта»:**
- **Plotly gl3d surface** с `colorscale` → heatmap на поверхности
- При обновлении данных: `Plotly.animate()` с `transition: {duration: 800, easing: 'cubic-in-out'}`
  → поверхность плавно «перетекает» в новую форму
- **Ambient rotation**: медленное автоматическое вращение камеры (0.5°/сек вокруг оси Z)
  → поверхность «дышит», показывая себя с разных ракурсов
- **Линия текущей цены**: вертикальная плоскость-сечение, светящаяся, слегка пульсирующая
- **Маркеры stop/take**: на «полу» поверхности — светящиеся точки с вертикальными лучами
- **«Горячие точки»** (локальные максимумы IV): пульсирующее свечение на поверхности
- **Wireframe overlay**: тонкая сетка поверх, слегка мерцающая — создаёт ощущение «голограммы»

**Альтернативный подход:** если Plotly ограничивает анимацию — рассмотри **2D heatmap с перспективой** (Canvas 2D), как в ridge.js. Или raw WebGL с GLSL fragment shader для colormap.

---

### МОДУЛЬ 3: 🔥 GEX Evolution Heatmap

**Суть:** Тепловая карта эволюции гамма-экспозиции дилеров: как стены OI строятся и рушатся со временем.

**Данные:**
```python
# Уже есть: cache.chain_snapshots(proxy, limit=10) возвращает историю
# Каждый snapshot содержит gex.strikes, gex.net, oi_profile
# engine.py → новый метод gex_evolution_payload():
#   для каждого снапшота → net_gex[strike] → матрица [time × strike]
```

**Анимация — «тепловая лава»:**
- Canvas 2D heatmap: ось X = время (снапшоты), ось Y = страйки (пересчитанные в шкалу инструмента)
- Цвет: двухполярная colormap. Положительная гамма (пиннинг) = оттенки бирюзового/зелёного.
  Отрицательная (ускорение) = оттенки пурпурного/красного. Ноль = тёмный фон
- **Плавная интерполяция**: между ячейками — не жёсткие квадраты, а сглаженный градиент
  (bilinear interpolation или даже gaussian blur на данных перед рендером)
- **Новый столбец появляется**: не мгновенно, а «вливается» справа (анимация 500ms,
  существующие столбцы сдвигаются влево с easing)
- **«Дыхание» ячеек**: яркость каждой ячейки модулируется sin(time + i*0.3) с малой амплитудой
  → heatmap «переливается», как перламутр
- **Горизонтальная линия цены**: пульсирующая, с glow-шлейфом
- **Маркеры стоп/тейк**: горизонтальные штрихи с trail-эффектом
- **Изменения**: если |GEX| на страйке резко выросла → кратковременная «вспышка» (белый flare)

**Техника для исследования:** посмотри GitHub-проекты «WebGL heatmap», «Canvas fluid simulation»,
или подход из аудио-спектрограмм (scrolling spectrogram) — тот же паттерн: время × частота × амплитуда.

---

### МОДУЛЬ 4: 📊 Volume Profile

**Суть:** Горизонтальная гистограмма объёма по ценовым уровням. POC + Value Area.

**Данные:**
```python
# yfinance: Ticker(yahoo).history(period="5d", interval="5m") для ETF-тикеров
# Агрегация: для каждого 5m-бара → распределить объём по ценовому bin
# price_bins = np.linspace(low, high, 50)
# volume_profile[bin] += bar_volume * gaussian_weight(bar_close, bin_center)
```

**Анимация — «жидкая гистограмма»:**
- Каждый бар volume profile — не статичный прямоугольник, а **жидкая полоса**
  с волновым краем (правый край слегка колышется по синусоиде)
- При обновлении данных: бары плавно растут/сжимаются (spring physics)
- **POC** (Point of Control): самый длинный бар — пульсирует ярче, с glow
- **Value Area** (70% объёма): подсвечена полупрозрачным цветным overlay
- **«Тонкие зоны»** (мало объёма): бары почти невидимы, но при наведении
  tooltip: «тонкая зона — цена пройдёт быстро»
- Градиент цвета вдоль гистограммы: от тёмного (мало объёма) к яркому (много)
- **Частицы объёма**: маленькие точки медленно дрейфуют от тонких зон к толстым (визуализация «притяжения объёма»)

**Интеграция:** встроить слева от Levels Map как вертикальную панель.

---

### МОДУЛЬ 5: 🔗 Cross-Asset Correlation Matrix

**Суть:** Матрица корреляций между инструментами (NAS, SP500, XAU, EUR, VIX, DXY...).

**Данные:**
```python
# yfinance: дневные закрытия за 30 дней для ~8 тикеров
# numpy: np.corrcoef(returns_matrix) → correlation_matrix[8×8]
# Добавить в tick_payload (обновлять раз в 30 мин — дневные данные)
# + добавить тикеры: "DX-Y.NYB" (DXY), "^TNX" (10Y yield)
```

**Анимация — «нейронная сеть связей»:**

Два варианта визуализации (выбери лучший или комбинируй):

**Вариант A: Animated Heatmap Matrix**
- Ячейки матрицы — сглаженные квадраты с цветом от синего (-1) через чёрный (0) к оранжевому (+1)
- При изменении корреляции: цвет плавно перетекает (CSS transition или Canvas lerp)
- **Аномальные** ячейки (корреляция сильно отклонилась от 30-дневного медиана):
  мерцают, пульсируют, привлекая внимание
- Диагональ (самокорреляция =1) — яркая пульсирующая линия

**Вариант B: Force-Directed Graph**
- Каждый инструмент — светящийся узел (node)
- Связи между ними — линии, толщина = |корреляция|, цвет = знак
- Узлы притягиваются/отталкиваются на основе корреляции (force simulation)
- Постоянное медленное движение → «живой организм» связей
- Если корреляция ломается — связь «рвётся» с анимацией (вспышка + затухание)

**Для исследования:** D3.js force simulation, или чистый Canvas с Verlet integration.

---

### МОДУЛЬ 6: 🎭 Put/Call Ratio + OI Flow

**Суть:** P/C ratio из OI цепочки + тренд.

**Данные:**
```python
# Уже в chain: sum(call_oi) / sum(put_oi)
# Хранить историю в cache для тренда (последние 10 снапшотов)
```

**Анимация — «маятник настроения»:**
- Горизонтальная шкала: слева «ЖАДНОСТЬ» (P/C < 0.6), справа «СТРАХ» (P/C > 1.2)
- **Маятник/стрелка**: плавно качается к текущему значению (spring physics с overshoot)
- Фон шкалы: градиент зелёный→серый→красный, медленно переливающийся
- **Trail**: полупрозрачный шлейф за маятником (последние 5 значений)
- При экстремальных значениях: фон «вспыхивает» соответствующим цветом
- Числовое значение: tween с spring-эффектом

---

### МОДУЛЬ 7: 🌊 Implied Move Fan (веер ожиданий)

**Суть:** Расширяющийся конус implied move по нескольким экспирациям.

**Данные:**
```python
# Расширить _fetch_term до 5 экспираций
# Для каждой: ATM straddle → implied move → ±band
# Передать как: [{days: 2, band: 150}, {days: 9, band: 380}, ...]
```

**Анимация — «северное сияние»:**
- На Levels Map: полупрозрачные зоны расширяются вправо (ось X = время)
- Каждая зона (экспирация) — отдельный слой с своим цветом
- Ближайшая: яркая, дальняя: бледная
- **Эффект «северного сияния»**: границы зон слегка волнисты (Perlin noise по краю),
  медленно колышутся → ощущение живого, органического коридора
- **Цена входит в зону**: зона «подсвечивается» ярче (интерактивность с данными)
- **Stop/Take маркеры**: горизонтальные линии, видно попадают ли в зону ожиданий
- При обновлении данных: зоны плавно расширяются/сужаются (анимация 600ms)

---

### МОДУЛЬ 8: 🧬 Regime DNA (радар режима)

**Суть:** 5-осевой radar chart рыночного режима (тренд, вола, инерция, хвосты, кластер).

**Данные:**
```python
# Всё из daily bars (уже есть):
# trend: z-score последней доходности
# vol: realized vol / median vol
# inertia: автокорреляция returns (lag-1)
# tails: kurtosis returns
# cluster: ATR(5)/ATR(20) (уже есть как atr_ratio)
```

**Анимация — «ДНК-спираль»:**
- Пятиугольник-радар с осями
- **Текущий режим**: закрашенный полигон, полупрозрачный, с glow
- **«Нормальный» режим**: тонкий пунктирный контур для сравнения
- Полигон **дышит**: вершины слегка пульсируют (noise-modulated)
- При смене режима: полигон плавно перетекает из одной формы в другую (vertex morph)
- **Оси**: тонкие лучи от центра, концы слегка мерцают
- **Фоновые кольца** (0.25, 0.5, 0.75, 1.0): тонкие, с subtle glow
- **Цвет полигона**: зависит от «опасности» режима — зелёный (спокойно)→жёлтый (осторожно)→красный (шок)

---

### МОДУЛЬ 9: 🏗️ OI Architecture (мульти-экспирация OI)

**Суть:** 2D heatmap: Strike × Expiry, цвет = Open Interest.

**Данные:**
```python
# yfinance: option_chain для 5-6 экспираций → OI матрица
# Обновлять раз в 10 мин (chain_poll_sec)
```

**Анимация — «архитектурный чертёж»:**
- Canvas 2D heatmap с плавной интерполяцией
- **Строительная метафора**: новые «кирпичи» OI — появляются с fade-in
  (500ms), исчезающие — с fade-out
- **Стены** (высокий OI): «светятся» пропорционально концентрации
- **Пустые зоны**: тёмные, с тонким grid-паттерном
- **Текущая цена**: горизонтальная линия с пульсирующим glow
- **Миграция OI**: если OI переехал с одного страйка на другой — анимированная
  «стрелка» или particle stream между старым и новым положением

---

### МОДУЛЬ 10: 📈 Edge Tracker Dashboard

**Суть:** Equity curve + scatter plot (edge vs result) + efficiency по сетапам.

**Данные:**
```python
# Всё из trades.db (journal.py)
# equity_curve: кумулятивная сумма result_r по закрытым сделкам
# scatter: (edge_at_open, result_r) для каждой сделки
# efficiency: из setup_stats()
```

**Анимация — «пульс торговли»:**
- **Equity Curve**: линия рисуется как «живой пульс» (как ЭКГ)
  - Последняя точка — пульсирующий маркер
  - Линия имеет glow-эффект (shadow blur)
  - Область под кривой — полупрозрачная заливка с градиентом
  - При добавлении новой сделки: линия плавно «удлиняется» вправо
- **Scatter Plot**: точки появляются с эффектом «капли в воду» (ripple)
  - Каждая точка имеет маленький trail (круги расходятся и затухают)
  - Положительные — зелёные glow, отрицательные — красные
  - При hover: точка увеличивается, показывает tooltip с деталями сделки
- **Setup Efficiency Bars**: горизонтальные бары с liquid fill animation

---

### МОДУЛЬ 11: 🎯 Smart Entry Zones (кластеризация уровней)

**Суть:** Overlay на Levels Map: подсветка зон, где 3+ уровней совпадают.

**Данные:**
```python
# Собрать все уровни: vwap, gex_zero_flip, oi_walls (call/put),
# implied_band edges, FVG zones из trade.zones
# Кластеризация: для каждой ценовой точки — посчитать сколько уровней
# попадают в окрестность ±0.2% от неё
# Передать: [{center: 21380, strength: 4, levels: ["GEX flip", "VWAP", ...]}, ...]
```

**Анимация — «силовое поле»:**
- На Levels Map: зона кластера подсвечена полупрозрачным «силовым полем»
- Интенсивность свечения = количество совпадающих уровней
- **Пульсация**: зона медленно пульсирует (дышит), яркость модулируется sin(time)
- **Частицы**: маленькие точки медленно «стягиваются» к центру кластера
  (гравитационная анимация) → визуально показывает «притяжение»
- **Радужный контур**: граница зоны переливается спектральным градиентом
  (hue rotation по периметру)
- При изменении уровней: зона плавно расширяется/сужается/перемещается

---

### МОДУЛЬ 12: 🕐 Session Clock + Liquidity Bands

**Суть:** Визуальные часы торговой сессии с фазами ликвидности.

**Данные:** Чистый фронтенд, только UTC время.

**Анимация — «орбитальные часы»:**
- Круговой циферблат (Canvas 2D)
- **Фазы сессии**: цветные дуги (Азия = синий, Лондон = зелёный, NYSE = оранжевый, Overlap = яркий)
- **Текущая позиция**: светящаяся точка, медленно движущаяся по окружности
- **Trail**: за точкой — затухающий шлейф прошедшего времени
- **Пульс ликвидности**: толщина дуги модулируется типичным объёмом
  (толще = больше ликвидности). Overlay pulsates subtly
- **AMD-фазы**: внутреннее кольцо показывает типичную фазу (Accumulation→Manipulation→Distribution)
  цветом и иконкой
- **Countdown**: до ключевого события (NY open, London close, т.д.) — числовой tween

---

## ОБЩИЕ ПРАВИЛА АНИМАЦИИ

1. **60fps или ничего.** Если анимация дёргается — лучше её убрать. Используй
   `requestAnimationFrame`, не `setInterval`. Профилируй в Chrome DevTools → Performance.

2. **GPU compositing.** Используй `will-change: transform` и `transform: translate3d(0,0,0)`
   для CSS-анимированных элементов. Canvas уже GPU-accelerated.

3. **Subtle > Flashy.** Анимации должны быть **спокойными и непрерывными**, не кричащими.
   Они создают ощущение «живого» терминала, а не дискотеки. Амплитуда пульсаций: 3-8%.

4. **Data-driven.** Интенсивность анимации = интенсивность данных. Высокая волатильность →
   частицы быстрее, цвета ярче, пульсации чаще. Спокойный рынок → всё тише.

5. **Performance budget.** Общий CPU на все Canvas: не более 30% одного ядра.
   Offscreen canvas для blur, requestIdleCallback для некритичных вычислений.

6. **Graceful degradation.** Если браузер не тянет (мобильный) — отключай частицы,
   уменьшай FPS до 30, убирай blur. `matchMedia('(prefers-reduced-motion: reduce)')`.

7. **Цветовая палитра.** Придерживайся существующей темы (IBM Plex Mono, бумажно-терминальный стиль), но добавь **акцентные свечения**. Не кислотный неон — а благородные:
   бирюзовый (#00CED1), янтарный (#FFB347), лавандовый (#B794F6), коралловый (#FF6B6B).

---

## ЗАПРЕЩЕНО

- НЕ ломать существующие блоки (Lattice, Ridge, Cone, Levels)
- НЕ менять `core/` (options.py, prob.py, risk.py) — математика source-agnostic
- НЕ добавлять тяжёлые npm-зависимости (Three.js допустим ТОЛЬКО если raw Canvas не хватит)
- НЕ использовать React/Vue/Angular — проект на vanilla JS ES Modules
- НЕ делать анимации которые ОТВЛЕКАЮТ от данных (анимация усиливает данные, не заменяет)
- НЕ использовать платные API или API с ключами

## ПОРЯДОК РЕАЛИЗАЦИИ

1. VRP Термометр (самый лёгкий, тестирует анимационный pipeline)
2. P/C Ratio (маленький, но с красивым маятником)
3. Smart Entry Zones (overlay на уже готовый Levels Map)
4. Session Clock (чистый фронтенд, отработка Canvas-анимаций)
5. GEX Evolution (первый серьёзный heatmap с анимацией)
6. Edge Tracker (графики из journal данных)
7. Implied Move Fan (расширение Levels Map)
8. Regime DNA (radar chart)
9. Volume Profile (новый фид + визуализация)
10. Correlation Matrix (доп. тикеры + heatmap/graph)
11. OI Architecture (мульти-экспирация)
12. IV Surface (самый сложный — 3D)
