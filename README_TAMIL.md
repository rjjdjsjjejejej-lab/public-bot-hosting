# BotCloud — Multi-user Bot Hosting Starter

இந்த project-ல் account registration/login, ஒவ்வொரு account-க்கும் தனிப்பட்ட bot பட்டியல், ZIP upload, Docker container start/stop, logs, delete ஆகியவை உள்ளன.

## முக்கியமானது
**இதை ZIP-ஐ இலவச static hosting website-ல் upload செய்தால் bot hosting வேலை செய்யாது.** Flask + persistent storage + Docker உள்ள VPS/server தேவை. சாதாரண PaaS providers Docker socket access தராமல் இருக்கலாம். Public launch செய்வதற்கு முன் security review அவசியம்.

## Local development
1. Python 3.12 மற்றும் Docker நிறுவவும்.
2. `pip install -r requirements.txt`
3. `BOT_RUNNER_IMAGE=python:3.12-slim` image-ஐ pull செய்யவும்.
4. நீளமான secret key உருவாக்கவும்:
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`
5. Environment variables அமைக்கவும்:
   - `SECRET_KEY` = மேலே உருவாக்கிய random value
   - `MAX_BOTS_PER_USER=3`
   - `MAX_UPLOAD_MB=20`
6. `python app.py`
7. Local browser-ல் `http://127.0.0.1:5000` திறக்கவும்.

## Public deployment checklist
- Dedicated VPS/server; Docker daemon-ஐ web app-க்கு முழு root access-ஆக வழங்க வேண்டாம். Production-ல் குறைந்த privilege கொண்ட container runner/service பயன்படுத்தவும்.
- HTTPS reverse proxy, secure cookies, CSRF protection, login rate limiting, abuse reporting, logs/monitoring, backups, user verification, storage/CPU quotas.
- Per-container egress policy மற்றும் network segmentation அமைக்கவும். Telegram API-க்கு internet தேவை என்பதால் `--network none` பயன்படுத்தப்படவில்லை.
- Bot containers non-root, read-only rootfs, no capabilities, memory/CPU/PID limits உடன் தொடங்கப்படுகின்றன; இருந்தாலும் malicious code-க்கு இது முழு பாதுகாப்பு உத்தரவாதமல்ல.
- இந்த starter arbitrary `requirements.txt` install செய்யாது. Package installation-க்கு தனி hardened build pipeline உருவாக்கவும்.
- Secret values-ஐ source code-ல் வைக்காதீர்கள்; bot token-களை logs-ல் print செய்யாதீர்கள்.
- Production-க்கு முன் CSRF protection, rate limits, account/email verification, admin moderation மற்றும் security testing சேர்க்கவும்.

## Bot ZIP
ZIP root-ல் `main.py` அல்லது `bot.py` இருக்க வேண்டும். Uploaded code server-side container-ல் இயங்கும். உங்களுக்குச் சொந்தமான அல்லது நம்பகமான code மட்டுமே upload செய்யவும்.
