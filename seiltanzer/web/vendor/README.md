# Вендоренные библиотеки фронтенда

## `plotly-gl3d.min.js`

- **Что это:** частичный бандл [Plotly.js](https://github.com/plotly/plotly.js) —
  только модуль **gl3d** (`scatter3d`, `surface`, `mesh3d`), настоящий WebGL-3D.
- **Версия:** 3.7.0
- **Источник:** npm-пакет `plotly.js-gl3d-dist-min` (готовый минифицированный бандл).
- **Лицензия:** MIT (см. `PLOTLY-LICENSE`).
- **Зачем вендорим, а не CDN:** терминал должен работать офлайн / на изолированном
  VPS / в Codespaces без внешних запросов. Полный `plotly.js` не нужен — берём
  только 3D-модуль (≈1.7 МБ вместо ≈4.5 МБ).
- **Где используется:** панель `PROBABILITY CONE` (`web/js/cone.js`) —
  3D-поверхность плотности вероятности исхода сделки + стены-барьеры.
- **Как обновить:** `npm pack plotly.js-gl3d-dist-min`, распаковать,
  скопировать `package/plotly-gl3d.min.js` и `package/LICENSE` сюда.
