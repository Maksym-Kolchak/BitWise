// pages.js — small helpers shared by several pages.

// "Copy" buttons: <button data-copy-from="input-id">Copy</button>
// copy the value of the input with that id.
document.querySelectorAll('[data-copy-from]').forEach(function (copyButton) {
  copyButton.addEventListener('click', function () {
    const linkInput = document.getElementById(copyButton.dataset.copyFrom);
    const oldText = copyButton.textContent;

    if (!navigator.clipboard) {
      window.prompt(t('Copy this link:'), new URL(linkInput.value, window.location.origin).href);
      return;
    }
    navigator.clipboard.writeText(new URL(linkInput.value, window.location.origin).href).then(function () {
      copyButton.textContent = t('Copied ✓');
      setTimeout(function () { copyButton.textContent = oldText; }, 1500);
    }).catch(function () {
      window.prompt(t('Copy this link:'), new URL(linkInput.value, window.location.origin).href);
    });
  });
});
