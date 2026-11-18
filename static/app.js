'use strict';
// Filters still work with the submit button when JavaScript is disabled.
document.querySelectorAll('[data-auto-submit]').forEach((field) => {
  field.addEventListener('change', () => field.form.requestSubmit());
});
document.querySelectorAll('form[method="post"]').forEach((form) => {
  form.addEventListener('submit', () => {
    form.querySelectorAll('button[type="submit"], button:not([type])').forEach((button) => {
      button.disabled = true;
    });
    form.setAttribute('aria-busy', 'true');
  });
});
window.addEventListener('pageshow', () => {
  document.querySelectorAll('form[aria-busy] button').forEach((button) => { button.disabled = false; });
  document.querySelectorAll('form[aria-busy]').forEach((form) => { form.removeAttribute('aria-busy'); });
});
