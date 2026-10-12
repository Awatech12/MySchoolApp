/**
 * TopMark PWA Install Script
 *
 * Responsibilities:
 *   1. Register the service worker
 *   2. Capture and surface the beforeinstallprompt event
 *   3. Show a custom install banner (mobile + desktop)
 *   4. Handle iOS / Safari manual-install instructions
 *   5. Track install state so the banner never annoys repeat users
 *   6. Navigation progress bar (top bar on page transitions)
 *   7. Expose window.TopMarkPWA for in-page use
 */

(function () {
  'use strict';

  // ─── Config ────────────────────────────────────────────────────────────────
  const SW_PATH        = '/serviceworker.js';
  const SW_SCOPE       = '/';
  const DISMISSED_KEY  = 'tm_pwa_dismissed';   // localStorage key
  const INSTALLED_KEY  = 'tm_pwa_installed';
  const SNOOZE_DAYS    = 3;                      // days before re-showing after dismiss

  // ─── State ─────────────────────────────────────────────────────────────────
  let deferredPrompt   = null;   // BeforeInstallPromptEvent
  let bannerEl         = null;
  let spinnerEl        = null;
  let navBarEl         = null;   // top navigation progress bar
  let navBarTimer      = null;   // fake-progress interval

  // ═══════════════════════════════════════════════════════════════════════════
  // 1. SERVICE WORKER REGISTRATION
  // ═══════════════════════════════════════════════════════════════════════════
  function registerSW() {
    if (!('serviceWorker' in navigator)) return;

    window.addEventListener('load', () => {
      // Show the loading spinner while the SW boots for the first time
      if (!navigator.serviceWorker.controller) {
        showLoadingSpinner();
      }

      navigator.serviceWorker
        .register(SW_PATH, { scope: SW_SCOPE })
        .then((reg) => {
          console.log('[PWA] Service worker registered, scope:', reg.scope);

          // If a SW is already active (returning visitor), hide spinner immediately
          if (navigator.serviceWorker.controller) {
            hideLoadingSpinner();
          }

          // Check for updates every time the page loads
          reg.addEventListener('updatefound', () => {
            const newWorker = reg.installing;
            newWorker.addEventListener('statechange', () => {
              if (newWorker.state === 'activated') {
                // Fresh install complete — hide the spinner
                hideLoadingSpinner();
              }
              if (
                newWorker.state === 'installed' &&
                navigator.serviceWorker.controller
              ) {
                // A new SW is waiting — optionally notify the user
                hideLoadingSpinner();
                showUpdateToast();
              }
            });
          });

          // Fallback: hide spinner after 4 s regardless
          setTimeout(hideLoadingSpinner, 4000);
        })
        .catch((err) => {
          console.warn('[PWA] SW registration failed:', err);
          hideLoadingSpinner();
        });

      // When a new SW takes over, reload to get fresh assets
      let refreshing = false;
      navigator.serviceWorker.addEventListener('controllerchange', () => {
        if (!refreshing) {
          refreshing = true;
          window.location.reload();
        }
      });
    });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // LOADING SPINNER
  // ═══════════════════════════════════════════════════════════════════════════
  function showLoadingSpinner() {
    if (spinnerEl) return;
    injectSpinnerStyles();

    spinnerEl = document.createElement('div');
    spinnerEl.id = 'tm-pwa-spinner';
    spinnerEl.setAttribute('role', 'status');
    spinnerEl.setAttribute('aria-label', 'Loading TopMark…');
    spinnerEl.innerHTML = `
      <div class="tm-spinner-backdrop"></div>
      <div class="tm-spinner-card">
        <div class="tm-spinner-ring">
          <svg viewBox="0 0 44 44" xmlns="http://www.w3.org/2000/svg">
            <circle class="tm-spinner-track" cx="22" cy="22" r="18" fill="none" stroke-width="3.5"/>
            <circle class="tm-spinner-arc"   cx="22" cy="22" r="18" fill="none" stroke-width="3.5"
                    stroke-linecap="round" stroke-dasharray="90 113"/>
          </svg>
        </div>
        <div class="tm-spinner-logo">T</div>
        <p class="tm-spinner-label">Loading TopMark…</p>
      </div>`;

    document.body.appendChild(spinnerEl);

    // Trigger entrance animation on next frame
    requestAnimationFrame(() => {
      requestAnimationFrame(() => spinnerEl && spinnerEl.classList.add('tm-spinner-visible'));
    });
  }

  function hideLoadingSpinner() {
    if (!spinnerEl) return;
    spinnerEl.classList.remove('tm-spinner-visible');
    spinnerEl.classList.add('tm-spinner-hiding');
    const el = spinnerEl;
    spinnerEl = null;
    setTimeout(() => { if (el && el.parentNode) el.parentNode.removeChild(el); }, 420);
  }

  function injectSpinnerStyles() {
    if (document.getElementById('tm-pwa-spinner-styles')) return;
    const style = document.createElement('style');
    style.id = 'tm-pwa-spinner-styles';
    style.textContent = `
      @keyframes tm-spin {
        to { transform: rotate(360deg); }
      }
      @keyframes tm-spinner-fade-in {
        from { opacity: 0; }
        to   { opacity: 1; }
      }
      @keyframes tm-spinner-card-in {
        from { opacity: 0; transform: translateY(12px) scale(.96); }
        to   { opacity: 1; transform: translateY(0)    scale(1);   }
      }

      #tm-pwa-spinner {
        position: fixed;
        inset: 0;
        z-index: 999999;
        display: flex;
        align-items: center;
        justify-content: center;
        pointer-events: none;
        opacity: 0;
        transition: opacity .4s ease;
      }
      #tm-pwa-spinner.tm-spinner-visible {
        pointer-events: auto;
        opacity: 1;
      }
      #tm-pwa-spinner.tm-spinner-hiding {
        opacity: 0;
        pointer-events: none;
      }

      .tm-spinner-backdrop {
        position: absolute;
        inset: 0;
        background: rgba(232, 244, 253, 0.72);
        backdrop-filter: blur(6px);
        -webkit-backdrop-filter: blur(6px);
      }

      .tm-spinner-card {
        position: relative;
        background: #fff;
        border-radius: 24px;
        padding: 36px 40px 32px;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 14px;
        box-shadow: 0 12px 48px rgba(0,0,0,.13), 0 2px 8px rgba(0,0,0,.07);
        border: 1.5px solid #cce9fd;
        min-width: 160px;
        animation: tm-spinner-card-in .4s ease both;
      }

      .tm-spinner-ring {
        position: relative;
        width: 56px;
        height: 56px;
      }
      .tm-spinner-ring svg {
        width: 56px;
        height: 56px;
        animation: tm-spin 1s linear infinite;
        transform-origin: center;
      }
      .tm-spinner-track {
        stroke: #cce9fd;
      }
      .tm-spinner-arc {
        stroke: #0095f6;
        stroke-dashoffset: 0;
        animation: tm-spin .9s cubic-bezier(.4,0,.2,1) infinite;
      }

      .tm-spinner-logo {
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        width: 28px;
        height: 28px;
        background: #0095f6;
        border-radius: 7px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 13px;
        font-weight: 800;
        color: #fff;
        font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
        pointer-events: none;
      }

      .tm-spinner-label {
        font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
        font-size: 13px;
        font-weight: 600;
        color: #737373;
        margin: 0;
        letter-spacing: .01em;
        white-space: nowrap;
      }
    `;
    document.head.appendChild(style);
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // 2. CAPTURE INSTALL PROMPT
  // ═══════════════════════════════════════════════════════════════════════════
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;

    if (!wasRecentlyDismissed() && !isInstalled()) {
      // Small delay so the page feels settled before the banner appears
      setTimeout(showInstallBanner, 1800);
    }
  });

  window.addEventListener('appinstalled', () => {
    localStorage.setItem(INSTALLED_KEY, 'true');
    hideBanner();
    deferredPrompt = null;
    console.log('[PWA] App installed');
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 3. INSTALL BANNER
  // ═══════════════════════════════════════════════════════════════════════════
  function showInstallBanner() {
    if (bannerEl) return; // already shown

    const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
    const isStandalone =
      window.matchMedia('(display-mode: standalone)').matches ||
      window.navigator.standalone === true;

    if (isStandalone) return; // already installed / running as app

    injectBannerStyles();

    bannerEl = document.createElement('div');
    bannerEl.id = 'tm-pwa-banner';
    bannerEl.setAttribute('role', 'dialog');
    bannerEl.setAttribute('aria-label', 'Install TopMark app');

    if (isIOS) {
      bannerEl.innerHTML = `
        <div class="tm-pwa-icon">T</div>
        <div class="tm-pwa-text">
          <strong>Add TopMark to Home Screen</strong>
          <span>Tap <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M12 2l-1.5 1.5L12 5l1.5-1.5zM11 5v9h2V5zm-6.5 7.5L3 14l1.5 1.5L6 14zm13 0L16 14l1.5 1.5L19 14zM7 17v2h10v-2z"/><path d="M5 19v2h14v-2z"/></svg> then "Add to Home Screen"</span>
        </div>
        <button class="tm-pwa-close" aria-label="Dismiss">✕</button>`;
    } else {
      bannerEl.innerHTML = `
        <div class="tm-pwa-icon">T</div>
        <div class="tm-pwa-text">
          <strong>Install TopMark</strong>
          <span>Study offline, faster, like a real app</span>
        </div>
        <button class="tm-pwa-install" aria-label="Install app">Install</button>
        <button class="tm-pwa-close" aria-label="Dismiss">✕</button>`;
    }

    document.body.appendChild(bannerEl);

    // Animate in
    requestAnimationFrame(() => {
      requestAnimationFrame(() => bannerEl.classList.add('tm-pwa-visible'));
    });

    // Wire up buttons
    const closeBtn = bannerEl.querySelector('.tm-pwa-close');
    if (closeBtn) closeBtn.addEventListener('click', dismissBanner);

    const installBtn = bannerEl.querySelector('.tm-pwa-install');
    if (installBtn) installBtn.addEventListener('click', triggerInstall);
  }

  async function triggerInstall() {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    console.log('[PWA] Install outcome:', outcome);
    deferredPrompt = null;
    hideBanner();
  }

  function dismissBanner() {
    localStorage.setItem(DISMISSED_KEY, Date.now().toString());
    hideBanner();
  }

  function hideBanner() {
    if (!bannerEl) return;
    bannerEl.classList.remove('tm-pwa-visible');
    setTimeout(() => {
      if (bannerEl && bannerEl.parentNode) bannerEl.parentNode.removeChild(bannerEl);
      bannerEl = null;
    }, 380);
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // 4. UPDATE TOAST
  // ═══════════════════════════════════════════════════════════════════════════
  function showUpdateToast() {
    const toast = document.createElement('div');
    toast.id = 'tm-pwa-update-toast';
    toast.innerHTML = `
      <span>TopMark has been updated!</span>
      <button id="tm-pwa-reload-btn">Reload</button>`;
    toast.style.cssText = `
      position:fixed;bottom:80px;left:50%;transform:translateX(-50%);
      background:#262626;color:#fff;padding:12px 20px;border-radius:12px;
      display:flex;align-items:center;gap:14px;font-size:13px;font-weight:600;
      z-index:99999;box-shadow:0 4px 20px rgba(0,0,0,.25);
      font-family:'Plus Jakarta Sans',-apple-system,sans-serif;
      animation:tmPwaSlideUp .35s ease;`;

    document.body.appendChild(toast);

    document.getElementById('tm-pwa-reload-btn').addEventListener('click', () => {
      if (navigator.serviceWorker.controller) {
        navigator.serviceWorker.controller.postMessage('SKIP_WAITING');
      }
      toast.remove();
    });

    // Auto-hide after 8 s
    setTimeout(() => { if (toast.parentNode) toast.remove(); }, 8000);
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // 5. HELPERS
  // ═══════════════════════════════════════════════════════════════════════════
  function wasRecentlyDismissed() {
    const ts = parseInt(localStorage.getItem(DISMISSED_KEY) || '0', 10);
    if (!ts) return false;
    return Date.now() - ts < SNOOZE_DAYS * 24 * 60 * 60 * 1000;
  }

  function isInstalled() {
    return (
      localStorage.getItem(INSTALLED_KEY) === 'true' ||
      window.matchMedia('(display-mode: standalone)').matches ||
      window.navigator.standalone === true
    );
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // 6. BANNER STYLES (injected once)
  // ═══════════════════════════════════════════════════════════════════════════
  function injectBannerStyles() {
    if (document.getElementById('tm-pwa-styles')) return;
    const style = document.createElement('style');
    style.id = 'tm-pwa-styles';
    style.textContent = `
      @keyframes tmPwaSlideUp {
        from { opacity:0; transform:translateX(-50%) translateY(20px); }
        to   { opacity:1; transform:translateX(-50%) translateY(0); }
      }

      #tm-pwa-banner {
        position: fixed;
        bottom: 76px;           /* sits just above the bottom nav if present */
        left: 12px;
        right: 12px;
        max-width: 480px;
        margin: 0 auto;
        background: #fff;
        border: 1.5px solid #efefef;
        border-radius: 18px;
        padding: 14px 14px 14px 16px;
        display: flex;
        align-items: center;
        gap: 12px;
        box-shadow: 0 8px 32px rgba(0,0,0,.14);
        z-index: 99998;
        font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
        opacity: 0;
        transform: translateY(16px);
        transition: opacity .35s ease, transform .35s ease;
        pointer-events: none;
      }

      #tm-pwa-banner.tm-pwa-visible {
        opacity: 1;
        transform: translateY(0);
        pointer-events: auto;
      }

      .tm-pwa-icon {
        width: 40px; height: 40px;
        min-width: 40px;
        background: #0095f6;
        border-radius: 10px;
        display: flex; align-items: center; justify-content: center;
        font-size: 18px; font-weight: 800; color: #fff;
      }

      .tm-pwa-text {
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 2px;
      }

      .tm-pwa-text strong {
        font-size: 13px;
        font-weight: 700;
        color: #262626;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      .tm-pwa-text span {
        font-size: 12px;
        color: #737373;
        display: flex;
        align-items: center;
        gap: 3px;
        flex-wrap: wrap;
      }

      .tm-pwa-install {
        background: #0095f6;
        color: #fff;
        border: none;
        border-radius: 10px;
        padding: 8px 16px;
        font-size: 13px;
        font-weight: 700;
        cursor: pointer;
        white-space: nowrap;
        font-family: inherit;
        transition: background .15s;
      }
      .tm-pwa-install:hover { background: #0074cc; }

      .tm-pwa-close {
        background: none;
        border: none;
        cursor: pointer;
        color: #737373;
        font-size: 14px;
        padding: 4px 6px;
        border-radius: 6px;
        line-height: 1;
        transition: background .15s;
        flex-shrink: 0;
        font-family: inherit;
      }
      .tm-pwa-close:hover { background: #fafafa; color: #262626; }
    `;
    document.head.appendChild(style);
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // 7. NAVIGATION PROGRESS BAR
  // ═══════════════════════════════════════════════════════════════════════════

  /**
   * Thin top bar (NProgress-style) that shows on every internal link click
   * and finishes when the browser fires the pageshow/load event on arrival.
   *
   * Smart skips:
   *  - Same-page anchor (#) links
   *  - Cross-origin links
   *  - Links that open in a new tab
   *  - Download links
   *  - Middle / Ctrl / Cmd clicks (browser handles those)
   */
  function initNavBar() {
    injectNavBarStyles();

    // Listen for all clicks on <a> elements (event delegation)
    document.addEventListener('click', (e) => {
      const anchor = e.target.closest('a');
      if (!anchor) return;

      // Skip if modifier keys held (new tab / window behaviour)
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      // Skip middle-click
      if (e.button !== 0) return;
      // Skip target="_blank"
      if (anchor.target === '_blank') return;
      // Skip download links
      if (anchor.hasAttribute('download')) return;

      const href = anchor.getAttribute('href');
      if (!href) return;
      // Skip pure anchors
      if (href.startsWith('#')) return;
      // Skip javascript: and mailto: etc.
      if (/^[a-z][a-z\d+\-.]*:/i.test(href) && !href.startsWith('http')) return;

      // Skip cross-origin
      try {
        const dest = new URL(href, window.location.href);
        if (dest.origin !== window.location.origin) return;
        // Skip same page, different hash only
        if (dest.pathname === window.location.pathname && dest.hash) return;
      } catch {
        return;
      }

      navBarStart();
    });

    // Hide the bar as soon as the new page is ready
    window.addEventListener('pageshow', navBarDone);

    // Also catch AJAX-driven navigations that don't reload the page
    // (Django form submissions that redirect, HTMX page swaps, etc.)
    document.addEventListener('htmx:afterSwap',   navBarDone);
    document.addEventListener('htmx:responseError', navBarDone);
  }

  /** Start / restart the bar at ~5 % and fake-advance toward 85 % */
  function navBarStart() {
    // If already running, restart cleanly
    navBarDone(null, /* skipFade */ true);

    injectNavBarStyles();
    navBarEl = document.createElement('div');
    navBarEl.id = 'tm-nav-bar';
    navBarEl.innerHTML = '<div class="tm-nav-bar-fill" id="tm-nav-bar-fill"></div>';
    document.body.appendChild(navBarEl);

    // Kick off at 5 %, then trickle to ~85 %
    let pct = 5;
    const fill = navBarEl.querySelector('#tm-nav-bar-fill');
    fill.style.width = pct + '%';

    navBarTimer = setInterval(() => {
      // Logarithmic slow-down — never reaches 100 on its own
      pct += (85 - pct) * 0.08;
      fill.style.width = Math.min(pct, 85) + '%';
    }, 200);
  }

  /** Complete the bar: snap to 100 %, then fade out and remove */
  function navBarDone(_evt, skipFade) {
    if (navBarTimer) { clearInterval(navBarTimer); navBarTimer = null; }
    if (!navBarEl) return;

    const fill = navBarEl.querySelector('#tm-nav-bar-fill');
    if (fill) fill.style.width = '100%';

    const el = navBarEl;
    navBarEl = null;

    if (skipFade) {
      el.remove();
      return;
    }

    // Short pause at 100 %, then fade out
    setTimeout(() => {
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 350);
    }, 200);
  }

  function injectNavBarStyles() {
    if (document.getElementById('tm-nav-bar-styles')) return;
    const style = document.createElement('style');
    style.id = 'tm-nav-bar-styles';
    style.textContent = `
      #tm-nav-bar {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        height: 3px;
        z-index: 1000000;
        pointer-events: none;
        opacity: 1;
        transition: opacity .35s ease;
      }

      .tm-nav-bar-fill {
        height: 100%;
        width: 0%;
        background: linear-gradient(90deg, #0095f6, #33a9f8);
        border-radius: 0 2px 2px 0;
        transition: width .22s cubic-bezier(.4,0,.2,1);
        box-shadow: 0 0 8px rgba(0,149,246,.55);
        position: relative;
      }

      /* Animated glint at the leading edge */
      .tm-nav-bar-fill::after {
        content: '';
        position: absolute;
        right: 0;
        top: 0;
        width: 80px;
        height: 100%;
        background: linear-gradient(90deg, transparent, rgba(255,255,255,.45));
        border-radius: inherit;
        animation: tm-nav-glint 1.2s ease-in-out infinite;
      }

      @keyframes tm-nav-glint {
        0%   { opacity: 0; }
        50%  { opacity: 1; }
        100% { opacity: 0; }
      }
    `;
    document.head.appendChild(style);
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // 8. PUBLIC API
  // ═══════════════════════════════════════════════════════════════════════════
  window.TopMarkPWA = {
    /** Programmatically trigger the install prompt (e.g. from a Settings page) */
    install: triggerInstall,
    /** Show the banner manually */
    showBanner: showInstallBanner,
    /** Check if running as installed app */
    isInstalled,
    /** Manually show/hide the SW loading spinner */
    showSpinner: showLoadingSpinner,
    hideSpinner: hideLoadingSpinner,
    /** Manually control the navigation progress bar */
    navStart: navBarStart,
    navDone:  navBarDone,
  };

  // ─── Boot ──────────────────────────────────────────────────────────────────
  registerSW();
  initNavBar();

})();
