# MikrotikAlignFree

Free tool for aiming MikroTik point-to-point wireless links. It is not an official MikroTik product.
Python standard library only, no license key, no registration. Works on Windows 7-11, Linux and macOS.

**Languages:** English (default) and Bulgarian. Switch with the 🌐 button at the top right.

## Features
- Live signal from the antenna (RouterOS API), peak hold, sound/voice indication.
- Signal from both sides: second antenna through the link itself, or a second computer (MQTT sync with a room code).
- Chat and voice messages between the two installers, "I am aiming" lock.
- Antenna discovery on the cable (MNDP, like Neighbors in WinBox).
- Link plan: distance, azimuth, tilt, Fresnel zone, expected signal, automatic magnetic declination (WMM2025, offline), terrain profile and map.
- Antenna setup wizard (WiFi and Wireless packages), automatic backup, speed test (bandwidth test) from the antennas.
- Printable report with saved measurement points.

## Run
```
python mikrotikalignfree.py
```
Then open http://127.0.0.1:8080 (the browser opens automatically).

## Windows installer
The `build-installer` GitHub Actions workflow builds `MikrotikAlignFree-Setup-<version>.exe` and a portable zip
(Actions -> build-installer -> Run workflow -> Artifacts). Pushing a tag like `v0.5.0` also creates a release.

## License
MIT
