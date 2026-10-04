/* ═══════════════════════════════════════════════════════════
   SeltaPrep — Service Worker Registration
   ═══════════════════════════════════════════════════════════ */

(function() {
    'use strict';

    if (!('serviceWorker' in navigator)) {
        console.log('[SW] Not supported');
        return;
    }

    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/static/sw.js', { scope: '/' })
            .then(registration => {
                console.log('[SW] ✅ Registered:', registration.scope);

                // Check for updates every 60 minutes
                setInterval(() => {
                    registration.update();
                }, 60 * 60 * 1000);

                // Handle updates
                registration.addEventListener('updatefound', () => {
                    const newWorker = registration.installing;
                    if (!newWorker) return;

                    newWorker.addEventListener('statechange', () => {
                        if (newWorker.state === 'installed' &&
                            navigator.serviceWorker.controller) {
                            console.log('[SW] New version available');
                            // Show update banner (optional)
                            showUpdateNotification();
                        }
                    });
                });
            })
            .catch(err => {
                console.warn('[SW] Registration failed:', err);
            });
    });

    // Reload when SW takes control
    let refreshing = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
        if (refreshing) return;
        refreshing = true;
        window.location.reload();
    });

    function showUpdateNotification() {
        const banner = document.createElement('div');
        banner.style.cssText = `
            position: fixed;
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: #4f46e5;
            color: white;
            padding: 12px 20px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            z-index: 99999;
            font-family: sans-serif;
            font-size: 14px;
            display: flex;
            align-items: center;
            gap: 12px;
        `;
        banner.innerHTML = `
            <span>🔄 New version available</span>
            <button id="sw-update-btn" style="
                background: white;
                color: #4f46e5;
                border: none;
                padding: 6px 14px;
                border-radius: 6px;
                font-weight: 600;
                cursor: pointer;
                font-size: 13px;
            ">Update</button>
            <button id="sw-dismiss-btn" style="
                background: transparent;
                color: white;
                border: none;
                cursor: pointer;
                font-size: 18px;
                padding: 0 4px;
            ">×</button>
        `;
        document.body.appendChild(banner);

        document.getElementById('sw-update-btn').onclick = () => {
            navigator.serviceWorker.getRegistration().then(reg => {
                if (reg && reg.waiting) {
                    reg.waiting.postMessage({ type: 'SKIP_WAITING' });
                }
            });
        };

        document.getElementById('sw-dismiss-btn').onclick = () => {
            banner.remove();
        };
    }

    // Expose cache-clear helper
    window.SeltaPrepClearCache = function() {
        if (!navigator.serviceWorker.controller) return;
        const channel = new MessageChannel();
        navigator.serviceWorker.controller.postMessage(
            { type: 'CLEAR_CACHE' },
            [channel.port2]
        );
        channel.port1.onmessage = () => {
            console.log('[SW] Cache cleared');
            window.location.reload();
        };
    };
})();
