# 🌉 Die Bridge

[← Übersicht](../../readme.md) · [Installation](installation.md) · [Dashboard](dashboard.md) · [Adminbereich](adminbereich.md) · **Die Bridge** · [Betrieb & Sicherung](betrieb.md)

## Aufbau

```
  📻 Funk / Netz              🌉 Tinker Board (DietPi)              💬 Zello
 ┌───────────────┐  USRP UDP  ┌──────────────────────┐  WebSocket  ┌───────────────┐
 │ SvxLink       │ ─────────▶ │ asl-zello-bridge     │ ──────────▶ │ Zello-Kanal   │
 │ (svxlink-usrp)│ ◀───────── │ /opt/asl-zello-bridge│ ◀────────── │ Free / Work   │
 └───────────────┘ 34001/32001└──────────────────────┘    (Opus)   └───────────────┘
                                         │ Journal
                                         ▼
                             N8 ZelloBridge (/opt/n8zello)
```

| Teil | Auf dem Board | Aufgabe |
|---|---|---|
| **asl-zello-bridge** | `/opt/asl-zello-bridge` (Python-venv), Dienst `asl-zello-bridge.service` | setzt USRP-Audio in den Zello-Kanal um und zurück ([G4IYT](https://github.com/mattmelling/asl-zello-bridge)) |
| **PyOgg** | `/opt/PyOgg` | Opus/Ogg für die Bridge, aus dem Quelltext installiert |
| **SvxLink mit USRP** | `/home/svxlink/svxlink-usrp`, Start über `/root/svxlink-usrp.sh` | Funk-/Netzseite; spricht USRP mit der Bridge |
| **Ansagen** | `/home/svxlink/svxlink-sounds-de_DE-petra` | deutsche Sprachansagen für SvxLink |
| **Dashboard** | `/opt/n8zello`, Dienst `n8zello.service` | dieses Projekt |

## Konfiguration der Bridge

Die Bridge wird nur über Umgebungsvariablen im systemd-Dienst eingestellt:

```bash
sudo systemctl edit asl-zello-bridge
```

Vorlage (Werte in `<…>` ersetzen – **echte Zugangsdaten nie ins Repository!**):

```ini
[Service]
# USRP: wo die Bridge Audio vom Funk empfängt und wohin sie sendet
Environment=USRP_BIND=127.0.0.1
Environment=USRP_HOST=127.0.0.1
Environment=USRP_RXPORT=34001
Environment=USRP_TXPORT=32001

# Zello-Konto der Bridge (eigenes Konto, darf im Kanal sprechen und hören)
Environment=ZELLO_USERNAME=<benutzer>
Environment=ZELLO_PASSWORD=<passwort>
Environment="ZELLO_CHANNEL=<Kanalname>"

# Zello Free: Entwickler-Schlüssel aus developers.zello.com
Environment=ZELLO_PRIVATE_KEY=/opt/asl-zello-bridge/zello.key
Environment=ZELLO_ISSUER=<issuer>
Environment=ZELLO_WS_ENDPOINT=wss://zello.io/ws

# optional
Environment=USRP_GAIN_RX_DB=0
Environment=USRP_GAIN_TX_DB=0
Environment=LOG_LEVEL=INFO
```

Danach `sudo systemctl restart asl-zello-bridge` – oder im Dashboard unter Admin → Services.

> Das Dashboard liest die Durchgänge aus dem Journal. `LOG_LEVEL=INFO` (Vorgabe) genügt; das Standard-Logformat `LEVEL:name:Nachricht` darf nicht abgeschaltet werden.

Die SvxLink-Seite muss auf dieselben Ports zeigen (RX der Bridge = TX von SvxLink und umgekehrt).

## Neuaufbau in Kurzform

Falls das Board ersetzt werden muss – mit der Sicherung aus dem Dashboard (Admin → Backup) oder aus `/root/zellobridge-backup-*.tar.gz`:

1. DietPi auf die SD-Karte, Netzwerk wie bisher (192.168.79.7).
2. Abhängigkeiten: `apt install libogg-dev libopusenc-dev libflac-dev libopusfile-dev libopus-dev libvorbis-dev libopus0 git python3-venv python3-pip`
3. Bridge nach Anleitung von [asl-zello-bridge](https://github.com/mattmelling/asl-zello-bridge#install-with-pip--venv) in `/opt/asl-zello-bridge` installieren (venv, PyOgg, `pip install .`), Dienstdatei kopieren.
4. SvxLink mit USRP-Modul wieder einrichten.
5. Sicherung einspielen: `sudo tar xzf zellobridge-backup-*.tar.gz -C /` – bringt Dienst-Drop-in mit Zugangsdaten, Zello-Schlüssel und SvxLink-Konfiguration zurück.
6. `sudo systemctl daemon-reload && sudo systemctl enable --now asl-zello-bridge`
7. Dashboard: `git clone https://github.com/dhartmann1979/n8schicht && sudo ./n8schicht/zellobridge/scripts/install.sh`. Die Datenbank aus der Sicherung (`var/lib/n8zello/n8zello-backup.db`) kann nach `/var/lib/n8zello/n8zello.db` kopiert werden, dann sind Statistik und Admins wieder da.
