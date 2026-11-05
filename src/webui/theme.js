/* Runs in <head> so the saved appearance applies before the first paint. */
(() => {
  'use strict';
  const key = 'nuannuan-theme', root = document.documentElement;
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  let preference = null;
  try { preference = localStorage.getItem(key); } catch {}
  if (!['light', 'dark'].includes(preference)) preference = null;
  const icons = {
    light: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
    dark: '<path d="M20.7 13.2A9 9 0 0 1 10.8 3.3a9 9 0 1 0 9.9 9.9Z"/>'
  };
  function updateButton(button) {
    const dark = root.dataset.theme === 'dark', next = dark ? '浅色' : '深色';
    button.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[root.dataset.theme]}</svg><span>${dark ? '深色' : '浅色'}</span>`;
    button.setAttribute('aria-label', `切换到${next}模式`);
    button.setAttribute('aria-pressed', String(dark));
    button.title = `切换到${next}模式`;
  }
  function apply() {
    root.dataset.theme = preference || (system.matches ? 'dark' : 'light');
    root.style.colorScheme = root.dataset.theme;
    document.querySelectorAll('.theme-toggle').forEach(updateButton);
    window.dispatchEvent(new CustomEvent('nn-theme-change', {detail: {theme: root.dataset.theme}}));
  }
  function set(value) {
    preference = ['light', 'dark'].includes(value) ? value : null;
    try { if (preference) localStorage.setItem(key, preference); else localStorage.removeItem(key); } catch {}
    apply();
  }
  function createToggle() {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'theme-toggle';
    button.onclick = () => set(root.dataset.theme === 'dark' ? 'light' : 'dark');
    updateButton(button); return button;
  }
  window.AppTheme = {set, createToggle, get: () => root.dataset.theme};
  apply();
  system.addEventListener('change', () => { if (!preference) apply(); });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    preference = ['light', 'dark'].includes(event.newValue) ? event.newValue : null; apply();
  });
  document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('loginForm')) {
      const tools = document.createElement('div'); tools.className = 'login-appearance';
      tools.append(createToggle()); document.body.append(tools);
    }
  });
})();
