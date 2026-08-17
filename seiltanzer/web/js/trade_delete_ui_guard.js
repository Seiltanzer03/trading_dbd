// User-delete UX guard. Backend remains the lifecycle authority.
// Loaded through management_ui.js so it can enhance the legacy journal modal
// without duplicating the large app.js orchestration module.

export function wrapDeleteHandler({ button, cancel, errorBox, modalBack, original }) {
  if (!button || typeof original !== 'function') return false;
  if (button.dataset.deleteGuard === '1') return true;
  button.dataset.deleteGuard = '1';

  button.onclick = async (event) => {
    if (button.dataset.deleteBusy === '1') return;
    button.dataset.deleteBusy = '1';
    button.disabled = true;
    if (cancel) cancel.disabled = true;
    const idleLabel = button.textContent;
    button.textContent = 'УДАЛЯЮ…';
    if (errorBox) errorBox.textContent = '';

    await original.call(button, event);

    // The legacy handler intentionally catches API errors itself.  It leaves
    // the modal visible and writes f-err on failure; successful deletion hides
    // modalBack and refreshes state. Re-enable only for the visible error case.
    const failed = Boolean(errorBox?.textContent?.trim());
    const modalStillVisible = !modalBack?.hidden;
    if (failed && modalStillVisible) {
      button.dataset.deleteBusy = '0';
      button.disabled = false;
      if (cancel) cancel.disabled = false;
      button.textContent = idleLabel;
    }
  };
  return true;
}

export function enhanceTradeDeleteModal(root = document) {
  const button = root.querySelector?.('#f-del');
  if (!button || button.dataset.deleteGuard === '1') return false;
  const modal = button.closest?.('#modal') || root.querySelector?.('#modal');
  const heading = modal?.querySelector?.('h3')?.textContent || '';
  if (!heading.startsWith('УДАЛИТЬ СДЕЛКУ')) return false;

  const paragraph = modal.querySelector('p');
  if (paragraph) {
    paragraph.textContent = paragraph.textContent.replace(
      'Удаление необратимо и повлияет на статистику сетапа.',
      'Сделка исчезнет из пользовательского журнала и активного сопровождения. ' +
      'Уже зафиксированные research/audit наблюдения сохранятся как неизменяемая история.'
    );
  }

  return wrapDeleteHandler({
    button,
    cancel: modal.querySelector('#f-cancel'),
    errorBox: modal.querySelector('#f-err'),
    modalBack: root.querySelector?.('#modal-back') || document.querySelector('#modal-back'),
    original: button.onclick,
  });
}

if (typeof document !== 'undefined' && typeof MutationObserver !== 'undefined') {
  const observer = new MutationObserver(() => enhanceTradeDeleteModal(document));
  observer.observe(document.documentElement, { childList: true, subtree: true });
  enhanceTradeDeleteModal(document);
}
