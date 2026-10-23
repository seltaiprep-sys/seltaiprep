/**
 * UKVI Test Assistant
 * - Silence detection (Web Audio API)
 * - Auto-advance on prolonged silence
 * - Periodic autosave
 * - Heartbeat
 * - Auto-submit on timeout / idle
 */
(function () {
    'use strict';

    const DEFAULTS = {
        silenceThreshold: 0.02,
        silenceWarnAfter: 15,
        silenceAutoAdvanceAfter: 30,
        testMaxMinutes: 30,
        warnBeforeMax: 2,
        idleMaxMinutes: 10,
        progressSaveEvery: 30,
        heartbeatEvery: 30,
    };

    const S = {
        cfg: { ...DEFAULTS },
        sessionId: null,
        startTime: Date.now(),
        lastActivity: Date.now(),
        lastSaved: 0,
        silenceStart: null,
        silenceWarned: false,
        idleWarned: false,
        timeWarned: false,
        submitted: false,
        timers: [],
        analyser: null,
        dataArray: null,
        audioCtx: null,
        autoAdvanceCooldown: 0,
    };

    // ═══════════════════════════════════════════════
    function init(opts = {}) {
        Object.assign(S.cfg, opts.config || {});
        S.sessionId = opts.sessionId || window.ukviSessionId || null;
        console.log('[UKVI Assist] init', S.cfg, 'session:', S.sessionId);

        ['click', 'keydown', 'touchstart'].forEach(ev =>
            document.addEventListener(ev, () => { S.lastActivity = Date.now(); S.idleWarned = false; }, { passive: true })
        );

        startTimers();
        tryHookAudio();

        showToast('🛡️ Test assistant active (silence + auto-save on)', 'info', 4000);
        
        // TYPING_RESETS_SILENCE_V1 — any interaction resets silence timer
        ['keydown', 'input', 'paste', 'click', 'mousemove', 'touchstart']
            .forEach(ev => document.addEventListener(ev, () => {
                if (S.silenceStart) {
                    S.silenceStart = null;
                    S.silenceWarned = false;
                }
            }, { passive: true }));
    }

    function startTimers() {
        S.timers.push(setInterval(checkTestTime, 10000));
        S.timers.push(setInterval(checkIdle, 10000));
        S.timers.push(setInterval(checkSilence, 500));
        S.timers.push(setInterval(saveProgress, S.cfg.progressSaveEvery * 1000));
        S.timers.push(setInterval(sendHeartbeat, S.cfg.heartbeatEvery * 1000));
    }

    function stop() {
        S.timers.forEach(clearInterval);
        S.timers = [];
        if (S.audioCtx) try { S.audioCtx.close(); } catch (e) {}
    }

    // ─── Silence detection ───
    async function tryHookAudio() {
        try {
            if (window.audioRecorder && window.audioRecorder.stream) {
                return attachAudio(window.audioRecorder.stream);
            }
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            attachAudio(stream);
        } catch (e) {
            console.warn('[UKVI Assist] Audio hook failed:', e.message);
        }
    }

    function attachAudio(stream) {
        S.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const src = S.audioCtx.createMediaStreamSource(stream);
        S.analyser = S.audioCtx.createAnalyser();
        S.analyser.fftSize = 2048;
        S.analyser.smoothingTimeConstant = 0.3;
        S.dataArray = new Uint8Array(S.analyser.frequencyBinCount);
        src.connect(S.analyser);
        console.log('[UKVI Assist] Audio analyser attached');
    }

    function getVolume() {
        if (!S.analyser) return 1;
        S.analyser.getByteTimeDomainData(S.dataArray);
        let sum = 0;
        for (let i = 0; i < S.dataArray.length; i++) {
            const x = (S.dataArray[i] - 128) / 128;
            sum += x * x;
        }
        return Math.sqrt(sum / S.dataArray.length);
    }

    function checkSilence() {
        if (!S.analyser || S.submitted) return;
        // Only during recording
        const recording =
            (window.audioRecorder && typeof window.audioRecorder.isRecording === 'function' && window.audioRecorder.isRecording()) ||
            document.querySelector('[data-recording="true"]');
        if (!recording) {
            S.silenceStart = null;
            S.silenceWarned = false;
            return;
        }
        const v = getVolume();
        if (v >= S.cfg.silenceThreshold) {
            S.silenceStart = null;
            S.silenceWarned = false;
            return;
        }
        if (!S.silenceStart) S.silenceStart = Date.now();
        const sec = (Date.now() - S.silenceStart) / 1000;

        if (sec >= S.cfg.silenceWarnAfter && !S.silenceWarned) {
            S.silenceWarned = true;
            const remain = Math.max(1, Math.round(S.cfg.silenceAutoAdvanceAfter - sec));
            showToast(`🎤 Still there? Answer in ${remain}s or we move on.`, 'warning', 8000);
            beep(600, 200);
        }

        if (sec >= S.cfg.silenceAutoAdvanceAfter && Date.now() > S.autoAdvanceCooldown) {
            console.log('[UKVI Assist] Silence → auto-advance');
            S.autoAdvanceCooldown = Date.now() + 3000;
            S.silenceStart = null;
            S.silenceWarned = false;
            autoAdvance();
        }
    }

    function autoAdvance() {
        const candidates = [
            '#next-btn', '[data-action="next"]', '.btn-next',
            'button[onclick*="next"]', '#submit-answer',
        ];
        for (const sel of candidates) {
            const b = document.querySelector(sel);
            if (b && !b.disabled) { console.log('[UKVI Assist] Click', sel); b.click(); return; }
        }
        const btns = document.querySelectorAll('button');
        for (const b of btns) {
            const t = (b.textContent || '').trim().toLowerCase();
            if ((t === 'next' || t === 'continue' || t === 'skip') && !b.disabled) {
                b.click();
                return;
            }
        }
        console.warn('[UKVI Assist] No next button found');
    }

    // ─── Timeout ───
    function checkTestTime() {
        const elapsedMin = (Date.now() - S.startTime) / 60000;
        const remain = S.cfg.testMaxMinutes - elapsedMin;
        if (remain <= S.cfg.warnBeforeMax && !S.timeWarned) {
            S.timeWarned = true;
            showToast(`⏰ ${Math.round(remain)} min remaining`, 'warning', 10000);
            beep(880, 400);
        }
        if (remain <= 0) {
            console.log('[UKVI Assist] Time limit');
            autoSubmit('time_limit');
        }
    }

    function checkIdle() {
        const idleMin = (Date.now() - S.lastActivity) / 60000;
        if (idleMin >= S.cfg.idleMaxMinutes - 1 && !S.idleWarned) {
            S.idleWarned = true;
            showToast(`💤 Idle ${Math.round(idleMin)} min — auto-submit in 1 min`, 'warning', 10000);
        }
        if (idleMin >= S.cfg.idleMaxMinutes) {
            console.log('[UKVI Assist] Idle limit');
            autoSubmit('idle_timeout');
        }
    }

    async function autoSubmit(reason) {
        if (S.submitted) return;
        S.submitted = true;
        showToast(`🔄 Auto-submit (${reason})...`, 'info', 0);
        try {
            const r = await fetch('/api/ukvi/auto-submit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ session_id: S.sessionId, reason }),
            });
            const d = await r.json();
            if (d.redirect_url) { window.location.href = d.redirect_url; return; }
            if (d.success) {
                showToast('✅ Submitted. Reloading...', 'success', 3000);
                setTimeout(() => location.reload(), 2500);
            } else {
                showToast('❌ ' + (d.error || 'Failed'), 'error', 10000);
                S.submitted = false;
            }
        } catch (e) {
            showToast('❌ Network error', 'error', 10000);
            S.submitted = false;
        }
    }

    // ─── Save + heartbeat ───
    async function saveProgress() {
        if (!S.sessionId || S.submitted) return;
        if (Date.now() - S.lastSaved < 20000) return;
        try {
            await fetch('/api/ukvi/progress-save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({
                    session_id: S.sessionId,
                    snapshot: {
                        current_q: window.currentQuestionIndex || 0,
                        answers: window.answers || {},
                    },
                }),
            });
            S.lastSaved = Date.now();
        } catch (e) {}
    }

    async function sendHeartbeat() {
        if (!S.sessionId || S.submitted) return;
        try {
            await fetch('/api/ukvi/heartbeat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({
                    session_id: S.sessionId,
                    silent: S.silenceStart ? Math.round((Date.now() - S.silenceStart) / 1000) : 0,
                }),
            });
        } catch (e) {}
    }

    // ─── UI ───
    function showToast(msg, type = 'info', dur = 5000) {
        let c = document.getElementById('ukvi-toast');
        if (!c) {
            c = document.createElement('div');
            c.id = 'ukvi-toast';
            c.style.cssText = 'position:fixed;top:20px;right:20px;z-index:99999;display:flex;flex-direction:column;gap:10px;pointer-events:none;';
            document.body.appendChild(c);
        }
        const colors = { info: '#3b82f6', success: '#10b981', warning: '#f59e0b', error: '#ef4444' };
        const t = document.createElement('div');
        t.style.cssText = `background:${colors[type] || colors.info};color:white;padding:14px 20px;border-radius:8px;font-size:14px;font-weight:500;max-width:380px;box-shadow:0 4px 12px rgba(0,0,0,.15);pointer-events:auto;`;
        t.textContent = msg;
        c.appendChild(t);
        if (dur > 0) setTimeout(() => t.remove(), dur);
    }

    function beep(freq = 800, dur = 150) {
        try {
            const ctx = S.audioCtx || new (window.AudioContext || window.webkitAudioContext)();
            const o = ctx.createOscillator(), g = ctx.createGain();
            o.frequency.value = freq; g.gain.value = 0.12;
            o.connect(g); g.connect(ctx.destination);
            o.start(); setTimeout(() => o.stop(), dur);
        } catch (e) {}
    }

    window.addEventListener('beforeunload', (e) => {
        if (S.submitted) return;
        if ((Date.now() - S.startTime) > 60000) {
            e.preventDefault();
            e.returnValue = 'Test in progress. Really leave?';
            return e.returnValue;
        }
    });

    // Public
    window.UKVIAssist = { init, stop, setSessionId: (id) => { S.sessionId = id; } };
})();