/**
 * Audio Helper – IIFE Wrapper (ensures window.audioManager is always defined)
 * No early return – always creates the singleton.
 */
(function() {
    // ─── AudioManager Class ────────────────────────────────────────
    class AudioManager {
        constructor() {
            this.audio = null;
            this.currentUrl = null;
            this.isPlaying = false;
            this.onPlayCallback = null;
            this.onEndCallback = null;
            this.loadTimeout = null;
            this.objectUrl = null;
            this._unlocked = false;
            this._unlockAudio = null;
            
            this.onSubscriptionRequired = null;
            this.statusElement = null;
            this.progressBar = null;
            this.currentTimeEl = null;
            this.durationEl = null;
        }

        setStatusElement(el) { this.statusElement = el; }
        setProgressBar(progressEl, currentTimeEl, durationEl) {
            this.progressBar = progressEl;
            this.currentTimeEl = currentTimeEl;
            this.durationEl = durationEl;
        }

        _updateStatus(msg) {
            if (this.statusElement) this.statusElement.textContent = msg;
        }

        _updateProgress() {
            if (!this.audio) return;
            if (this.progressBar) {
                const pct = (this.audio.currentTime / this.audio.duration) * 100 || 0;
                this.progressBar.style.width = Math.min(pct, 100) + '%';
            }
            if (this.currentTimeEl) {
                this.currentTimeEl.textContent = this._formatTime(this.audio.currentTime);
            }
            if (this.durationEl) {
                this.durationEl.textContent = this._formatTime(this.audio.duration);
            }
        }

        _formatTime(seconds) {
            if (!seconds || isNaN(seconds)) return '0:00';
            const mins = Math.floor(seconds / 60);
            const secs = Math.floor(seconds % 60);
            return mins + ':' + String(secs).padStart(2, '0');
        }

        _setButtonState(button, state, text) {
            if (!button) return;
            button.textContent = text || '🔊 Listen';
            button.disabled = (state === 'loading' || state === 'playing');
            button.classList.remove('playing', 'loading', 'error', 'done');
            if (state === 'playing') button.classList.add('playing');
            else if (state === 'loading') button.classList.add('loading');
            else if (state === 'error') button.classList.add('error');
            else if (state === 'done') button.classList.add('done');
        }

        _handleError(button, message) {
            console.error('Audio error:', message);
            this.isPlaying = false;
            this._cleanupAudio();
            if (this.loadTimeout) clearTimeout(this.loadTimeout);
            this._setButtonState(button, 'error', '❌ Error');
            this._updateStatus('Error: ' + message);
            setTimeout(() => {
                this._setButtonState(button, 'ready', '🔊 Listen');
                this._updateStatus('Ready');
            }, 3000);
        }

        _handleSubscriptionRequired(message) {
            console.warn('Subscription required:', message);
            this._updateStatus('⚠️ ' + message);
            if (typeof this.onSubscriptionRequired === 'function') {
                this.onSubscriptionRequired(message);
            } else {
                alert(message || 'Subscription required to access this audio.');
            }
        }

        _cleanupAudio() {
            if (this.audio) {
                try {
                    this.audio.pause();
                    this.audio.src = '';
                    this.audio.load();
                    this.audio.onended = null;
                    this.audio.onerror = null;
                    this.audio.ontimeupdate = null;
                } catch (e) {}
                this.audio = null;
            }
            if (this.objectUrl) {
                URL.revokeObjectURL(this.objectUrl);
                this.objectUrl = null;
            }
            this.isPlaying = false;
        }

        // ─── Autoplay Unlock ────────────────────────────────────────
        unlock() {
            if (this._unlocked) return;
            try {
                const silentBlob = new Blob(
                    [new Uint8Array([0x52, 0x49, 0x46, 0x46, 0x24, 0x00, 0x00, 0x00, 0x57, 0x41, 0x56, 0x45, 0x66, 0x6D, 0x74, 0x20, 0x10, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x44, 0xAC, 0x00, 0x00, 0x88, 0x58, 0x01, 0x00, 0x02, 0x00, 0x10, 0x00, 0x64, 0x61, 0x74, 0x61, 0x00, 0x00, 0x00, 0x00])],
                    { type: 'audio/wav' }
                );
                const url = URL.createObjectURL(silentBlob);
                this._unlockAudio = new Audio();
                this._unlockAudio.muted = true;
                this._unlockAudio.src = url;
                const playPromise = this._unlockAudio.play();
                if (playPromise !== undefined) {
                    playPromise
                        .then(() => {
                            this._unlocked = true;
                            this._unlockAudio.pause();
                            this._unlockAudio.src = '';
                            URL.revokeObjectURL(url);
                            this._unlockAudio = null;
                            console.log('🔓 Audio unlocked');
                        })
                        .catch(() => {});
                }
            } catch (e) {}
        }

        // ─── Play (Fetch + Blob) ──────────────────────────────────
        async play(audioUrl, button = null, onPlay = null, onEnd = null) {
            if (!this._unlocked) {
                this.unlock();
                await new Promise(resolve => setTimeout(resolve, 100));
            }

            this.stop();
            this._setButtonState(button, 'loading', '🔊 Loading...');
            this._updateStatus('Loading audio...');

            try {
                let fullUrl = audioUrl.startsWith('http') ? audioUrl : window.location.origin + audioUrl;
                const cacheBuster = (fullUrl.indexOf('?') === -1) ? '?_=' : '&_=';
                fullUrl += cacheBuster + Date.now();

                const response = await fetch(fullUrl);
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status} ${response.statusText}`);
                }

                const blob = await response.blob();
                const audioBlob = new Blob([blob], { type: blob.type || 'audio/mpeg' });

                this.objectUrl = URL.createObjectURL(audioBlob);

                const audio = new Audio();
                audio.preload = 'auto';
                audio.src = this.objectUrl;

                audio.onplay = () => {
                    this.isPlaying = true;
                    this._setButtonState(button, 'playing', '⏸️ Pause');
                    this._updateStatus('Playing...');
                    if (onPlay) onPlay();
                };

                audio.onended = () => {
                    this.isPlaying = false;
                    this._setButtonState(button, 'done', '🔊 Listen Again');
                    this._updateStatus('Finished');
                    if (onEnd) onEnd();
                    this._cleanupAudio();
                    if (this.progressBar) this.progressBar.style.width = '0%';
                };

                audio.onerror = (e) => {
                    let errorMsg = 'Playback failed (decode error)';
                    if (audio.error) {
                        switch (audio.error.code) {
                            case MediaError.MEDIA_ERR_ABORTED:
                                console.warn('Aborted (normal)');
                                return;
                            case MediaError.MEDIA_ERR_DECODE:
                                errorMsg = 'Audio decode error';
                                break;
                            case MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED:
                                errorMsg = 'Unsupported audio format';
                                break;
                            default:
                                errorMsg = 'Media error: ' + audio.error.message;
                        }
                    }
                    console.error('Audio error:', errorMsg);
                    this._handleError(button, errorMsg);
                };

                audio.ontimeupdate = () => {
                    if (this.audio) this._updateProgress();
                };

                this.audio = audio;
                audio.load();

                await new Promise((resolve, reject) => {
                    const timeout = setTimeout(() => reject(new Error('Load timeout (30s)')), 30000);
                    const onCanPlay = () => {
                        clearTimeout(timeout);
                        audio.removeEventListener('canplaythrough', onCanPlay);
                        resolve();
                    };
                    audio.addEventListener('canplaythrough', onCanPlay, { once: true });
                    audio.addEventListener('loadeddata', () => {
                        clearTimeout(timeout);
                        audio.removeEventListener('canplaythrough', onCanPlay);
                        resolve();
                    }, { once: true });
                });

                // 🔥 Play muted first, then unmute
                audio.muted = true;
                const playPromise = audio.play();
                if (playPromise !== undefined) {
                    await playPromise;
                }
                audio.muted = false;

                return true;

            } catch (error) {
                console.error('play() error:', error);
                this._handleError(button, error.message);
                throw error;
            }
        }

        stop() {
            this._cleanupAudio();
            if (this.progressBar) this.progressBar.style.width = '0%';
            this._updateStatus('Stopped');
            const btn = document.querySelector('.audio-btn-primary');
            if (btn) {
                btn.textContent = '▶️ Play Audio';
                btn.disabled = false;
                btn.classList.remove('playing', 'loading', 'error', 'done');
            }
        }

        pause() {
            if (this.audio && this.isPlaying) {
                this.audio.pause();
                this.isPlaying = false;
                this._updateStatus('Paused');
            }
        }

        resume() {
            if (this.audio && !this.isPlaying) {
                this.audio.play().catch(e => console.error('Resume failed:', e));
                this.isPlaying = true;
                this._updateStatus('Playing...');
            }
        }

        toggle(button) {
            if (this.isPlaying) {
                this.pause();
                if (button) button.textContent = '▶️ Resume';
            } else {
                this.resume();
                if (button) button.textContent = '🔊 Playing...';
            }
        }

        getDuration() { return this.audio ? this.audio.duration : 0; }
        seek(position) { if (this.audio) this.audio.currentTime = position; }
        isLoaded() { return this.audio && this.audio.readyState >= 2; }
    }

    // ─── Always create the singleton ──────────────────────────────
    window.audioManager = new AudioManager();

    // ─── Global helper functions ─────────────────────────────────
    window.playSpeakingQuestion = async function(part, questionNum, button) {
        if (!button) return;
        try {
            const question = button.dataset.question || '';
            if (!question) {
                button.textContent = '❌ No text';
                setTimeout(() => button.textContent = '🔊 Listen', 2000);
                return;
            }
            const response = await fetch(`/speaking/audio/generate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question, part, question_num: questionNum })
            });
            if (response.status === 402 || response.status === 403) {
                const data = await response.json();
                audioManager._handleSubscriptionRequired(data.error || 'Subscription required.');
                return;
            }
            const data = await response.json();
            if (data.success && data.audio_url) {
                await audioManager.play(data.audio_url, button);
            } else {
                throw new Error(data.error || 'Failed to generate audio');
            }
        } catch (error) {
            console.error(error);
            button.textContent = '❌ Error';
            button.disabled = false;
            setTimeout(() => button.textContent = '🔊 Listen', 3000);
        }
    };

    window.playListeningSection = async function(sectionNum, button) {
        if (!button) return;
        try {
            audioManager.unlock();
            const sectionData = window.listeningData?.sections?.[sectionNum - 1];
            if (!sectionData) throw new Error('Section data not available');
            const response = await fetch(`/listening/audio/section/${sectionNum}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ section_data: sectionData })
            });
            if (response.status === 402 || response.status === 403) {
                const data = await response.json();
                audioManager._handleSubscriptionRequired(data.error || 'Subscription required.');
                return;
            }
            const data = await response.json();
            if (data.success && data.merged_audio) {
                await audioManager.play(data.merged_audio, button);
            } else {
                throw new Error(data.error || 'Failed to load audio');
            }
        } catch (error) {
            console.error(error);
            button.textContent = '❌ Error';
            button.disabled = false;
            setTimeout(() => button.textContent = '▶️ Play Audio', 3000);
        }
    };

    window.playPTEReadAloud = async function(text, button) {
        if (!button) return;
        try {
            if (!text) throw new Error('Text required');
            const response = await fetch(`/pte/audio/read-aloud`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text })
            });
            if (response.status === 402 || response.status === 403) {
                const data = await response.json();
                audioManager._handleSubscriptionRequired(data.error || 'Subscription required.');
                return;
            }
            const data = await response.json();
            if (data.success && data.audio_url) {
                await audioManager.play(data.audio_url, button);
            } else {
                throw new Error(data.error || 'Failed to load audio');
            }
        } catch (error) {
            console.error(error);
            button.textContent = '❌ Error';
            button.disabled = false;
            setTimeout(() => button.textContent = '🔊 Listen', 3000);
        }
    };

    window.playUKVIQuestion = async function(questionNum, button) {
        if (!button) return;
        try {
            const question = button.dataset.question || '';
            if (!question) throw new Error('Question text required');
            const response = await fetch(`/ukvi/audio/question/${questionNum}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question })
            });
            if (response.status === 402 || response.status === 403) {
                const data = await response.json();
                audioManager._handleSubscriptionRequired(data.error || 'Subscription required.');
                return;
            }
            const data = await response.json();
            if (data.success && data.audio_url) {
                await audioManager.play(data.audio_url, button);
            } else {
                throw new Error(data.error || 'Failed to load audio');
            }
        } catch (error) {
            console.error(error);
            button.textContent = '❌ Error';
            button.disabled = false;
            setTimeout(() => button.textContent = '🔊 Listen', 3000);
        }
    };

    console.log('✅ AudioManager loaded successfully (NO early return)');
})();