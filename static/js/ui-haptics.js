(function () {
  if (window.__seltHapticsLoaded) return;
  window.__seltHapticsLoaded = true;

  const SELECTORS = [
    'button',
    '.btn',
    'a.btn',
    '[role="button"]',
    'input[type="submit"]',
    'input[type="button"]',
    '[onclick]',
    '.tab-btn',
    '.nav-btn',
    '.q-btn',
    '.q-pill',
    '.option-label',
    '.option-item',
    '.module-card',
    '.skill-card',
    '.test-card'
  ].join(', ');

  const style = document.createElement('style');
  style.textContent = `
    button, .btn, [role="button"], input[type="submit"], input[type="button"], [onclick],
    .tab-btn, .nav-btn, .q-btn, .q-pill, .option-label, .option-item, .module-card, .skill-card, .test-card {
      position: relative;
      overflow: hidden;
      transition: transform 0.14s cubic-bezier(0.22, 1, 0.36, 1), box-shadow 0.18s ease, opacity 0.15s ease;
      -webkit-tap-highlight-color: transparent;
    }
    button:active:not(:disabled),
    .btn:active:not(:disabled),
    [role="button"]:active,
    input[type="submit"]:active:not(:disabled),
    input[type="button"]:active:not(:disabled),
    [onclick]:active,
    .tab-btn:active,
    .nav-btn:active,
    .q-btn:active,
    .q-pill:active,
    .option-label:active,
    .option-item:active,
    .module-card:active,
    .skill-card:active,
    .test-card:active {
      transform: scale(0.96) !important;
    }
    .selt-ripple {
      position: absolute;
      border-radius: 50%;
      transform: scale(0);
      animation: seltRippleAnim 0.52s cubic-bezier(0.22, 1, 0.36, 1);
      background: rgba(99, 102, 241, 0.25);
      pointer-events: none;
      z-index: 9999;
    }
    button .selt-ripple, .btn-primary .selt-ripple, .btn-success .selt-ripple, .btn-danger .selt-ripple {
      background: rgba(255, 255, 255, 0.38);
    }
    @keyframes seltRippleAnim {
      to {
        transform: scale(3.2);
        opacity: 0;
      }
    }
  `;
  document.head.appendChild(style);

  function triggerHaptic(el) {
    if (!navigator.vibrate) return;
    const isDanger = el.classList.contains('btn-danger') ||
                     /delete|remove|cancel|logout|reset/i.test((el.textContent || el.value || ''));
    try {
      navigator.vibrate(isDanger ? [18, 30, 22] : 14);
    } catch (_) {}
  }

  function spawnRipple(el, e) {
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const size = Math.max(rect.width, rect.height);
    const x = (e.clientX ? e.clientX - rect.left : rect.width / 2) - size / 2;
    const y = (e.clientY ? e.clientY - rect.top : rect.height / 2) - size / 2;
    const ripple = document.createElement('span');
    ripple.className = 'selt-ripple';
    ripple.style.width = ripple.style.height = size + 'px';
    ripple.style.left = x + 'px';
    ripple.style.top = y + 'px';
    el.appendChild(ripple);
    setTimeout(() => ripple.remove(), 550);
  }

  document.addEventListener('pointerdown', function (e) {
    const target = e.target.closest(SELECTORS);
    if (!target || target.disabled) return;
    triggerHaptic(target);
    spawnRipple(target, e);
  }, { passive: true });
})();
