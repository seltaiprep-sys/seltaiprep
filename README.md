# SELTAI PREP

> **AI-powered exam preparation platform for IELTS, PTE, and UKVI** — with 3D avatar interviews, adaptive question pools, and multi-provider speech services.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.0+-green.svg)](https://flask.palletsprojects.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14+-blue.svg)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7+-red.svg)](https://redis.io/)
[![License](https://img.shields.io/badge/License-Proprietary-lightgrey.svg)](#license)

---

## 📖 Overview

SELTAI PREP is a comprehensive web platform built to help students prepare for **IELTS**, **PTE**, and **UKVI** English proficiency exams. It combines AI-generated content, real-time feedback, and immersive 3D avatar interviews to deliver a realistic exam experience — all in a single Flask application.

**Key differentiators:**

- 🎭 **3D Avatar Interviews** — UKVI practice with animated examiners (Three.js)
- 🤖 **AI Question Generation** — IELTS/PTE/UKVI questions generated via LLMs
- 🔄 **Adaptive Test Pools** — Shared question banks that scale dynamically
- 🗣️ **Multi-Provider TTS/STT** — Deepgram Aura-2 (primary), Edge TTS, gTTS
- ⚡ **Async Generation** — Background workers with Redis-backed queues
- 📊 **Admin Dashboards** — Real-time pool stats, job monitoring, user analytics
- 💳 **eSewa Payments** — Integrated Nepali payment gateway
- 🎯 **Subscription Management** — Per-module plans with free tier

---

## ✨ Features

### IELTS Module
- **Listening** — 4-section tests with multi-accent audio (British, American, Australian, etc.)
- **Reading** — 3 passages with varied question types, AI-evaluated
- **Writing** — Task 1 (charts/maps) + Task 2 (essays), band scores with feedback
- **Speaking** — 3-part interview with pronunciation/grammar/fluency scoring
- **Full Mock Test** — End-to-end 4-section simulation with single band score

### PTE Module
- **Reading** — Fill blanks, reorder paragraphs, multiple choice
- **Listening** — Summarize spoken text, fill blanks, highlight correct summary
- **Speaking & Writing** — Describe image, repeat sentence, essay writing
- **Audio Generation** — AI-generated audio for listening questions
- **Image Generation** — Bar charts, pie charts, maps, flowcharts

### UKVI Module
- **3D Avatar Interviewer** — Animated character with lip-sync (Three.js)
- **Profile-Based Questions** — Personalized to user's university/course/finances
- **Async Question Generation** — Pool-based caching for instant delivery
- **Live Captions** — Subtitle-style question display
- **Voice + Text Input** — Speech recognition with manual fallback
- **Credibility Scoring** — Predicts visa approval likelihood

### Admin Features
- **Pool Dashboard** — Real-time stats for all modules
- **Job Monitor** — Track background generation jobs
- **Payment Verification** — Approve manual eSewa payments
- **User Management** — Per-module user lists, subscriptions, results
- **Test Bank Seeding** — Bulk-generate pool content
- **Audio Cache Manager** — Clean up old TTS files

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.11+, Flask 3.0 |
| **Database** | PostgreSQL 14+ (SQLAlchemy ORM) |
| **Cache / Queue** | Redis 7+ (sessions, rate limits, job state) |
| **AI / LLM** | OpenAI-compatible APIs (DeepSeek, GPT) |
| **Speech** | Deepgram Aura-2 (TTS), Nova-2 (STT), Edge TTS, gTTS |
| **3D** | Three.js (WebGL), GLB models |
| **Frontend** | Vanilla JS, HTML5, CSS3 |
| **Payments** | eSewa (Nepal) |
| **Scheduler** | APScheduler (background jobs) |
| **Rate Limiting** | Flask-Limiter (Redis-backed) |
| **Server** | Gunicorn + gevent |

---

## 📁 Project Structure
