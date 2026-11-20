# 💾 Betrieb & Sicherung

[← Übersicht](../../readme.md) · [Installation](installation.md) · [Dashboard](dashboard.md) · [Adminbereich](adminbereich.md) · [Die Bridge](bridge.md) · **Betrieb & Sicherung**

## Dienste

```bash
systemctl status asl-zello-bridge n8zello
journalctl -u asl-zello-bridge -f      # Bridge live
journalctl -u n8zello -f               # Dashboard
```

Das Dashboard läuft als Benutzer `n8zello` ohne Root-Rechte (Gruppe `systemd-journal` zum Lesen des Journals). Per `/etc/sudoers.d/n8zello` darf es genau: die bei der Installation gefundenen Bridge-Dienste starten/stoppen/neu starten, `/usr/local/sbin/n8zello-backup` ausführen, das Board neu starten oder herunterfahren. Es begrenzt sich selbst auf 96 MB RAM (`MemoryMax`).

## Sicherung

| Weg | Ergebnis |
|---|---|
| Installation (Schritt 7) | `/root/zellobridge-backup-DATUM.tar.gz` |
| Dashboard: Admin → Backup | Download im Browser |
| Kommandozeile | `sudo /usr/local/sbin/n8zello-backup /root/sicherung.tar.gz` |

Inhalt: Unit und Drop-ins von `asl-zello-bridge` (Zello-Konto, Passwort, Kanal, Ports), die Schlüsseldatei aus `ZELLO_PRIVATE_KEY`, `/etc/svxlink`, `/root/svxlink-usrp.sh`, SvxLink-Dienste und – beim Download aus dem Dashboard – eine Kopie der Dashboard-Datenbank.

> 🔒 Die Sicherung enthält Passwörter und Schlüssel. Nicht ins Git-Repository, nicht in die Cloud ohne Verschlüsselung.

**Wiederherstellen:**

```bash
sudo tar xzf zellobridge-backup-*.tar.gz -C /
sudo systemctl daemon-reload
sudo systemctl restart asl-zello-bridge
```

## Typische Probleme

| Anzeige | Ursache / Lösung |
|---|---|
| ✕ Bridge stopped | Dienst gestoppt oder abgestürzt: Admin → Services → Journal, dann *Restart* |
| ✕ Zello offline / error | Internet weg, Zello-Konto gesperrt, falsches Passwort oder abgelaufener Schlüssel – das Journal zeigt den Grund (`Authentication failed`, `Kicked from channel` …) |
| Woodpecker-Ereignisse | Zello bremst zu viele kurze Sendungen; auf der Funkseite Rauschsperre / Tastendrücker prüfen |
| USRP „not bound“ | Die Bridge lauscht nicht auf dem RX-Port: Dienst neu starten, Ports in `systemctl edit asl-zello-bridge` prüfen |
| Keine Durchgänge im Dashboard | `LOG_LEVEL` der Bridge muss INFO oder DEBUG sein; Settings → *Bridge service* richtig? |
| Temperatur hoch (> 75 °C) | Das Tinker Board drosselt; Kühlkörper/Lüfter prüfen |
| Dashboard nicht erreichbar | `systemctl status n8zello`; Port belegt? `sudo ss -ltnp` |

## Testen ohne Bridge

```bash
N8ZELLO_DATA=/tmp/demo N8ZELLO_DEMO=1 N8ZELLO_PORT=8080 python3 -m n8zello   # erfundene Werte und Verkehr
python3 -m pytest tests                                                     # Tests
```
