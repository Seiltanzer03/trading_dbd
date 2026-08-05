// Compatibility entrypoint for the experimental option-center overlay.
// The v2 implementation keeps the original cone/fan untouched and updates its
// appended Plotly traces in place instead of deleting/recreating them per tick.
export * from './option_center_overlay_v2.js';
