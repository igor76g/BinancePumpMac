# Binance Pump Alerts 1.7.0 for macOS

Port of the Windows app. Monitors all Binance USDT-M perpetual futures (public endpoints only, no API keys).

- Alert when price moves >= 4 % within 5 min (Growth / Drop / Both), 24h volume > 30M USDT (all editable).
- Optional trade-volume spike filter (volume x N of the usual; 0 = off), separate volume and price settings.
- Cooldown per coin (15 min), connection-lost warning, history of alerts, REST polling only (low traffic).
- Alerts: sound (Glass), always-on-top popup with "Open in Vataga" / "Open on Binance", macOS banner, alerts.log.
- Double-click / buttons open the coin in Vataga.terminal (port 5005, partner id `BinancePumpAlerts`).
  In Vataga: Settings > General > "Application integration" on; link the window to "Screener".

## Build (GitHub Actions)
Upload the folder (with hidden `.github`) to GitHub -> Actions -> "Build macOS app" -> download
`BinancePumpAlerts-mac-apple-silicon` (M1-M4) or `BinancePumpAlerts-mac-intel`. Open the DMG, drag the app to Applications.
Local build: `./build.sh`. From source: `pip install -r requirements.txt && python3 run.py`.

## First launch
Not notarized: right-click -> Open, or `xattr -dr com.apple.quarantine /Applications/BinancePumpAlerts.app`.

## Notifications
Banners are sent through `osascript` and appear as "Script Editor": allow them in System Settings -> Notifications.
Popups and sound work regardless.

## Other
- "Keep the Mac awake while monitoring" uses `caffeinate` (closing the lid still sleeps the Mac).
- Data: `~/Library/Application Support/BinancePumpAlerts/` (settings.json, alerts.log).
- Not built/tested on a real Mac by the author.
