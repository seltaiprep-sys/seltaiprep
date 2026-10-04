/* ═══════════════════════════════════════════════════════════
   SeltaPrep — Push Notifications Client
   ═══════════════════════════════════════════════════════════ */

(function() {
    'use strict';

    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
        console.log('[Push] Not supported');
        return;
    }

    // Convert base64 to Uint8Array
    function urlBase64ToUint8Array(base64String) {
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding)
            .replace(/-/g, '+')
            .replace(/_/g, '/');
        const rawData = window.atob(base64);
        const outputArray = new Uint8Array(rawData.length);
        for (let i = 0; i < rawData.length; ++i) {
            outputArray[i] = rawData.charCodeAt(i);
        }
        return outputArray;
    }

    async function getVapidPublicKey() {
        try {
            const resp = await fetch('/api/push/vapid-public-key');
            const data = await resp.json();
            return data.publicKey || null;
        } catch (e) {
            console.warn('[Push] Failed to get VAPID key:', e);
            return null;
        }
    }

    async function subscribeToPush() {
        // 1. Request permission
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') {
            console.log('[Push] Permission denied');
            return { success: false, error: 'Permission denied' };
        }

        // 2. Get SW registration
        const registration = await navigator.serviceWorker.ready;

        // 3. Get VAPID key
        const vapidKey = await getVapidPublicKey();
        if (!vapidKey) {
            return { success: false, error: 'VAPID key missing' };
        }

        // 4. Subscribe
        let subscription = await registration.pushManager.getSubscription();
        if (!subscription) {
            subscription = await registration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(vapidKey),
            });
        }

        // 5. Send to server
        const resp = await fetch('/api/push/subscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ subscription: subscription }),
        });

        const result = await resp.json();
        console.log('[Push] Subscribe result:', result);
        return result;
    }

    async function unsubscribeFromPush() {
        try {
            const registration = await navigator.serviceWorker.ready;
            const subscription = await registration.pushManager.getSubscription();
            if (!subscription) return { success: true };

            await fetch('/api/push/unsubscribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ endpoint: subscription.endpoint }),
            });

            await subscription.unsubscribe();
            console.log('[Push] Unsubscribed');
            return { success: true };
        } catch (e) {
            console.error('[Push] Unsubscribe error:', e);
            return { success: false, error: e.message };
        }
    }

    // Expose globally
    window.SeltaPrepPush = {
        subscribe: subscribeToPush,
        unsubscribe: unsubscribeFromPush,
        isSupported: () => 'Notification' in window && 'PushManager' in window,
    };

    console.log('[Push] Client loaded');
})();
