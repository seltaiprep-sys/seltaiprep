if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/static/sw.js', { updateViaCache: 'none' })
            .then(reg => { if (reg && reg.update) reg.update(); })
            .catch(() => {});
    });
}
