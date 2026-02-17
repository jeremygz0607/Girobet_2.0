# Deploying to Vultr

This guide walks you through running the Aviator scraper + signal engine on a Vultr VPS (Ubuntu).

---

## 1. Create a VPS on Vultr

1. Log in at [vultr.com](https://www.vultr.com) → **Deploy New Server**.
2. **Server type:** Cloud Compute.
3. **Location:** Choose a region close to you or to the game (e.g. São Paulo if targeting BRT).
4. **Image:** **Ubuntu 22.04 LTS**.
5. **Plan:** At least **1 vCPU, 2 GB RAM** (scraper runs a headless browser; 4 GB is safer).
6. Add your **SSH key** (recommended) or set a root password.
7. Deploy. Note the server **IP address**.

---

## 2. Connect and prepare the server

```bash
# Replace with your server IP
ssh root@YOUR_SERVER_IP
```

Update the system and install Python, Chrome (for SeleniumBase), and Git:

```bash
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git unzip

# Chrome/Chromium for headless scraping (SeleniumBase uses Chrome)
apt install -y wget gnupg
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add -
echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list
apt update
apt install -y google-chrome-stable
# Or use Chromium instead (lighter): apt install -y chromium-browser
```

---

## 3. MongoDB

You need a MongoDB instance. Two options:

**Option A – MongoDB Atlas (recommended)**  
- Create a free cluster at [mongodb.com/atlas](https://www.mongodb.com/atlas).  
- Get the connection string (e.g. `mongodb+srv://user:pass@cluster.mongodb.net/`).  
- Use it as `MONGODB_URI` in `.env`. No install on the VPS.

**Option B – MongoDB on the same VPS**  
```bash
apt install -y mongodb
systemctl enable mongodb
systemctl start mongodb
# MONGODB_URI will be: mongodb://localhost:27017/
```

---

## 4. Deploy the project

**Option A – Upload with Git (if the project is in a repo)**  
```bash
cd /opt
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git aviator-signal
cd aviator-signal
```

**Option B – Upload from your PC (no Git)**  
On your **Windows PC** (PowerShell), from the project folder:

```powershell
scp -r "c:\Users\American Eagle\Downloads\Version1.1\*" root@YOUR_SERVER_IP:/opt/aviator-signal/
```

Then on the server:

```bash
cd /opt/aviator-signal
```

Create a virtual environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 5. Environment variables

Create `.env` in the project root (e.g. `/opt/aviator-signal/.env`). **Do not commit this file.**

```bash
nano /opt/aviator-signal/.env
```

Add (replace with your real values):

```env
# Casino login (required for scraper)
AVIATOR_USERNAME=your_casino_username
AVIATOR_PASSWORD=your_casino_password

# MongoDB (required for log_monitor)
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/
MONGODB_DATABASE=casino
MONGODB_COLLECTION=rounds

# Telegram (required for notifications)
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_CHANNEL_ID=@your_channel_or_numeric_id
AFFILIATE_LINK=https://your-affiliate-link.com

# Optional
# AVIATOR_GAME_URL=https://...
# AVIATOR_LOGIN_URL=https://...
# SCRAPE_INTERVAL_SECONDS=4
```

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`). Restrict permissions:

```bash
chmod 600 /opt/aviator-signal/.env
```

---

## 6. Run with systemd (recommended)

So the scraper and log monitor keep running and restart on reboot.

Create a user to run the app (optional but good practice):

```bash
adduser --disabled-password --gecos "" aviator
# Give ownership of the app folder
chown -R aviator:aviator /opt/aviator-signal
```

**Service 1 – Aviator scraper**

```bash
nano /etc/systemd/system/aviator-scraper.service
```

Paste (adjust paths if you used a different directory):

```ini
[Unit]
Description=Aviator payout scraper
After=network.target

[Service]
Type=simple
User=aviator
Group=aviator
WorkingDirectory=/opt/aviator-signal
Environment=PATH=/opt/aviator-signal/venv/bin
ExecStart=/opt/aviator-signal/venv/bin/python aviator.py
Restart=always
RestartSec=10
StandardOutput=append:/opt/aviator-signal/log.log
StandardError=append:/opt/aviator-signal/log.log

[Install]
WantedBy=multi-user.target
```

**Service 2 – Log monitor (MongoDB + signal engine + scheduler)**

```bash
nano /etc/systemd/system/aviator-log-monitor.service
```

```ini
[Unit]
Description=Aviator log monitor and signal engine
After=network.target

[Service]
Type=simple
User=aviator
Group=aviator
WorkingDirectory=/opt/aviator-signal
Environment=PATH=/opt/aviator-signal/venv/bin
ExecStart=/opt/aviator-signal/venv/bin/python log_monitor.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Optional – Watchdog (restarts scraper on repeated “No payouts”)**

```bash
nano /etc/systemd/system/aviator-watchdog.service
```

```ini
[Unit]
Description=Aviator scraper watchdog
After=network.target aviator-scraper.service

[Service]
Type=simple
User=aviator
Group=aviator
WorkingDirectory=/opt/aviator-signal
Environment=PATH=/opt/aviator-signal/venv/bin
ExecStart=/opt/aviator-signal/venv/bin/python run_aviator_watchdog.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
systemctl daemon-reload
systemctl enable aviator-scraper aviator-log-monitor
systemctl start aviator-scraper aviator-log-monitor

# Optional
# systemctl enable aviator-watchdog && systemctl start aviator-watchdog
```

Check status:

```bash
systemctl status aviator-scraper aviator-log-monitor
journalctl -u aviator-log-monitor -f
tail -f /opt/aviator-signal/log.log
```

---

## 7. Log rotation (optional)

To avoid `log.log` growing forever:

```bash
nano /etc/logrotate.d/aviator
```

```text
/opt/aviator-signal/log.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
```

---

## 8. Firewall (optional)

If you don’t need inbound HTTP/SSH from elsewhere, you can leave UFW off or allow only SSH:

```bash
ufw allow 22/tcp
ufw enable
```

Outbound: MongoDB (Atlas), Telegram, and the casino URLs must be reachable (default allow).

---

## Quick reference

| Task              | Command |
|-------------------|--------|
| Start scraper     | `systemctl start aviator-scraper` |
| Start log monitor | `systemctl start aviator-log-monitor` |
| Stop all          | `systemctl stop aviator-scraper aviator-log-monitor` |
| View scraper log  | `tail -f /opt/aviator-signal/log.log` |
| View monitor log  | `journalctl -u aviator-log-monitor -f` |
| Restart after code change | `systemctl restart aviator-scraper aviator-log-monitor` |

---

## Troubleshooting

- **Chrome not found:** Install `google-chrome-stable` as in step 2, or set `CHROME_PATH` in the environment if you use Chromium.
- **“No payouts” repeatedly:** Check game URL and login in `.env`; run `python aviator.py` once manually and watch for errors.
- **MongoDB connection failed:** Check `MONGODB_URI`, firewall, and Atlas IP whitelist (allow `0.0.0.0/0` for testing).
- **Telegram not sending:** Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHANNEL_ID`; bot must be in the channel as admin.
