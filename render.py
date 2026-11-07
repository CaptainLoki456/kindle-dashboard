#!/usr/bin/env python3
"""
Kindle-Dashboard: Wetter + Kalender als Graustufen-PNG für E-Ink.

Erzeugt für jede Auflösung in AUFLOESUNGEN eine Datei  dash-<B>x<H>.png
im Ausgabeordner. Konfiguration über Umgebungsvariablen:

  STADT         Ort für das Wetter, z.B. "Hamburg"            (oder BREITE + LAENGE)
  BREITE        Breitengrad, z.B. 53.55                       (optional, statt STADT)
  LAENGE        Längengrad,  z.B. 9.99                        (optional, statt STADT)
  ORTSNAME      Anzeigename des Orts (optional)
  ICS_URLS      Ein oder mehrere ICS-Links (Outlook "Kalender veröffentlichen"),
                durch Leerzeichen, Komma oder Zeilenumbruch getrennt
  ZEITZONE      Standard: Europe/Berlin
  KALENDER_TAGE Wie viele Tage voraus Termine gezeigt werden (Standard: 7)
  AUFLOESUNGEN  Standard: 758x1024,1072x1448,1236x1648,1264x1680
  AUSGABE       Ausgabeordner (Standard: ./ausgabe)
  DEMO          =1 erzeugt ein Beispielbild mit erfundenen Daten (ohne Internet)
"""

from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HIER = Path(__file__).resolve().parent
FONT_DIR = HIER / "fonts"
SS = 2  # Supersampling-Faktor für glatte Kanten

WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
WT_KURZ = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]
MON_KURZ = ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sep.", "Okt.", "Nov.", "Dez."]

SCHWARZ = 0
DUNKEL = 70     # Sekundärtext – auf E-Ink lieber dunkelgrau als hellgrau
MITTEL = 150    # Linien, dezente Elemente
WEISS = 255


# --------------------------------------------------------------------------- Daten

@dataclass
class Termin:
    titel: str
    beginn: datetime | date
    ende: datetime | date
    ganztags: bool
    ort: str = ""


WMO = {
    0: ("Klar", "sonne"), 1: ("Überwiegend klar", "teils"), 2: ("Teilweise bewölkt", "teils"),
    3: ("Bedeckt", "wolke"), 45: ("Nebel", "nebel"), 48: ("Reifnebel", "nebel"),
    51: ("Leichter Niesel", "niesel"), 53: ("Nieselregen", "niesel"), 55: ("Starker Niesel", "niesel"),
    56: ("Gefrierender Niesel", "niesel"), 57: ("Gefrierender Niesel", "niesel"),
    61: ("Leichter Regen", "regen"), 63: ("Regen", "regen"), 65: ("Starker Regen", "regen"),
    66: ("Gefrierender Regen", "regen"), 67: ("Gefrierender Regen", "regen"),
    71: ("Leichter Schnee", "schnee"), 73: ("Schneefall", "schnee"), 75: ("Starker Schnee", "schnee"),
    77: ("Schneegriesel", "schnee"), 80: ("Regenschauer", "regen"), 81: ("Regenschauer", "regen"),
    82: ("Heftige Schauer", "regen"), 85: ("Schneeschauer", "schnee"), 86: ("Schneeschauer", "schnee"),
    95: ("Gewitter", "gewitter"), 96: ("Gewitter, Hagel", "gewitter"), 99: ("Gewitter, Hagel", "gewitter"),
}


def wmo(code) -> tuple[str, str]:
    return WMO.get(int(code) if code is not None else -1, ("–", "wolke"))


def hole_wetter(tz: ZoneInfo) -> dict:
    import requests

    breite, laenge = os.environ.get("BREITE"), os.environ.get("LAENGE")
    ortsname = os.environ.get("ORTSNAME", "").strip()
    if not (breite and laenge):
        stadt = os.environ.get("STADT", "").strip()
        if not stadt:
            raise RuntimeError("Weder STADT noch BREITE/LAENGE gesetzt")
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": stadt, "count": 1, "language": "de"}, timeout=20)
        r.raise_for_status()
        treffer = (r.json().get("results") or [None])[0]
        if not treffer:
            raise RuntimeError(f"Ort '{stadt}' nicht gefunden")
        breite, laenge = treffer["latitude"], treffer["longitude"]
        ortsname = ortsname or treffer["name"]

    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": breite, "longitude": laenge, "timezone": str(tz), "forecast_days": 4,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day",
        "hourly": "temperature_2m,weather_code,precipitation_probability,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    }, timeout=20)
    r.raise_for_status()
    d = r.json()
    d["ortsname"] = ortsname
    return d


def hole_termine(tz: ZoneInfo, heute: date, tage: int) -> list[Termin]:
    import requests
    import icalendar
    import recurring_ical_events

    urls = [u for u in re.split(r"[\s,]+", os.environ.get("ICS_URLS", "")) if u]
    if not urls:
        return []
    start = datetime.combine(heute, time.min, tz)
    ende = start + timedelta(days=tage)
    termine: list[Termin] = []
    for url in urls:
        url = re.sub(r"^webcals?://", "https://", url)
        if url.startswith("http"):
            r = requests.get(url, timeout=30, headers={"User-Agent": "kindle-dashboard"})
            r.raise_for_status()
            inhalt = r.content
        else:  # lokale Datei, praktisch zum Testen
            inhalt = Path(url).read_bytes()
        cal = icalendar.Calendar.from_ical(inhalt)
        for ev in recurring_ical_events.of(cal).between(start, ende):
            if str(ev.get("STATUS", "")).upper() == "CANCELLED":
                continue
            titel = str(ev.get("SUMMARY", "")).strip() or "(ohne Titel)"
            if re.match(r"^(Abgesagt|Canceled|Cancelled):", titel, re.I):
                continue
            b = ev.decoded("DTSTART")
            e = ev.decoded("DTEND") if ev.get("DTEND") else None
            if isinstance(b, datetime):
                b = (b if b.tzinfo else b.replace(tzinfo=tz)).astimezone(tz)
                if isinstance(e, datetime):
                    e = (e if e.tzinfo else e.replace(tzinfo=tz)).astimezone(tz)
                else:
                    e = b + (ev.decoded("DURATION") if ev.get("DURATION") else timedelta(0))
                termine.append(Termin(titel, b, e, False, str(ev.get("LOCATION", "")).strip()))
            else:
                e = e if isinstance(e, date) and e > b else b + timedelta(days=1)
                termine.append(Termin(titel, b, e, True, str(ev.get("LOCATION", "")).strip()))
    return termine


def demo_daten(tz: ZoneInfo, jetzt: datetime):
    heute = jetzt.date()
    h0 = jetzt.replace(minute=0, second=0, microsecond=0)
    stunden = [h0 + timedelta(hours=i) for i in range(30)]
    temps = [11 + 4 * math.sin((s.hour - 9) / 24 * 2 * math.pi) for s in stunden]
    codes = [2, 2, 3, 3, 61, 61, 80, 3, 2, 1, 0, 0, 0, 0, 1, 2, 3, 3, 2, 2, 1, 1, 0, 0, 2, 2, 3, 3, 61, 61]
    wetter = {
        "ortsname": os.environ.get("ORTSNAME") or os.environ.get("STADT") or "Hamburg",
        "current": {"temperature_2m": 12.4, "apparent_temperature": 9.8, "weather_code": 2,
                    "wind_speed_10m": 17.0, "is_day": 1},
        "hourly": {"time": [s.strftime("%Y-%m-%dT%H:00") for s in stunden], "temperature_2m": temps,
                   "weather_code": codes, "precipitation_probability": [10, 15, 30, 45, 70, 80, 60, 30, 20,
                   10, 5, 5, 0, 0, 5, 10, 20, 25, 15, 10, 5, 5, 0, 0, 10, 20, 35, 40, 65, 70],
                   "is_day": [1 if 7 <= s.hour < 18 else 0 for s in stunden]},
        "daily": {"time": [(heute + timedelta(days=i)).isoformat() for i in range(4)],
                  "weather_code": [61, 3, 0, 71], "temperature_2m_max": [14.8, 12.1, 10.3, 4.2],
                  "temperature_2m_min": [7.2, 6.0, 2.4, -1.5],
                  "precipitation_probability_max": [80, 30, 5, 60]},
    }

    def t(tag, hh, mm, dauer_min, titel, ort=""):
        b = datetime.combine(heute + timedelta(days=tag), time(hh, mm), tz)
        return Termin(titel, b, b + timedelta(minutes=dauer_min), False, ort)

    termine = [
        Termin("Homeoffice", heute, heute + timedelta(days=1), True),
        t(0, 8, 30, 30, "Team-Standup", "Microsoft Teams"),
        t(0, jetzt.hour, 0, 90, "Projektbesprechung Q4", "Raum 3.14"),
        t(0, 17, 45, 60, "Zahnarzt Dr. Weber", "Lange Reihe 12"),
        t(0, 19, 30, 150, "Abendessen mit Lena und Jonas im neuen italienischen Restaurant"),
        Termin("Urlaub Ostsee", heute + timedelta(days=2), heute + timedelta(days=4), True),
        t(1, 9, 0, 60, "Quartalsplanung", "Teams"),
        t(1, 12, 30, 60, "Mittagessen mit Sarah"),
        t(1, 18, 0, 90, "Fußball"),
        t(3, 10, 0, 120, "Brunch bei Mama"),
        t(4, 9, 0, 30, "Werkstatt: Reifenwechsel"),
        t(5, 14, 0, 60, "Steuerberater"),
    ]
    return wetter, termine


# --------------------------------------------------------------------------- Zeichnen

class Leinwand:
    def __init__(self, breite: int, hoehe: int):
        self.B, self.H = breite, hoehe
        self.k = breite / 1072 * SS          # Basis-Layout ist 1072 px breit
        self.img = Image.new("L", (breite * SS, hoehe * SS), WEISS)
        self.d = ImageDraw.Draw(self.img)
        self._fonts: dict = {}

    def u(self, v: float) -> int:
        return int(round(v * self.k))

    def font(self, schnitt: str, groesse: float):
        key = (schnitt, groesse)
        if key not in self._fonts:
            self._fonts[key] = ImageFont.truetype(str(FONT_DIR / f"Inter-{schnitt}.otf"), self.u(groesse))
        return self._fonts[key]

    def text(self, xy, s, schnitt="Regular", groesse=30, farbe=SCHWARZ, anker="la"):
        self.d.text((self.u(xy[0]), self.u(xy[1])), s, font=self.font(schnitt, groesse), fill=farbe, anchor=anker)

    def breite(self, s, schnitt="Regular", groesse=30) -> float:
        return self.d.textlength(s, font=self.font(schnitt, groesse)) / self.k

    def kuerzen(self, s, max_b, schnitt="Regular", groesse=30) -> str:
        if self.breite(s, schnitt, groesse) <= max_b:
            return s
        while s and self.breite(s + "…", schnitt, groesse) > max_b:
            s = s[:-1]
        return s.rstrip() + "…"

    def linie(self, x1, y1, x2, y2, staerke=2, farbe=SCHWARZ):
        self.d.line([(self.u(x1), self.u(y1)), (self.u(x2), self.u(y2))], fill=farbe, width=max(1, self.u(staerke)))

    def rechteck(self, x1, y1, x2, y2, farbe=SCHWARZ, radius=0):
        self.d.rounded_rectangle([self.u(x1), self.u(y1), self.u(x2), self.u(y2)], radius=self.u(radius), fill=farbe)

    def symbol(self, art: str, cx, cy, groesse, tag=True):
        g = self.u(groesse)
        ico = zeichne_symbol(art, g, tag)
        self.img.paste(SCHWARZ, (self.u(cx) - g // 2, self.u(cy) - g // 2), ico)

    def fertig(self) -> Image.Image:
        return self.img.resize((self.B, self.H), Image.LANCZOS)


def zeichne_symbol(art: str, g: int, tag: bool) -> Image.Image:
    """Liefert eine Maske (L): 255 = schwarz zeichnen. Linienstil, passend für E-Ink."""
    maske = Image.new("L", (g, g), 0)
    w = max(2, round(g * 0.055))  # Strichstärke

    def wolken_form(ox, oy, s):
        m = Image.new("L", (g, g), 0)
        dm = ImageDraw.Draw(m)
        def kreis(x, y, r):
            dm.ellipse([ox + (x - r) * s, oy + (y - r) * s, ox + (x + r) * s, oy + (y + r) * s], fill=255)
        kreis(0.36, 0.56, 0.17)
        kreis(0.56, 0.45, 0.23)
        kreis(0.76, 0.58, 0.15)
        dm.rounded_rectangle([ox + 0.20 * s, oy + 0.56 * s, ox + 0.90 * s, oy + 0.73 * s], radius=0.085 * s, fill=255)
        return m

    def wolke(ox=0.0, oy=0.0, s=None):
        s = s or g
        form = wolken_form(ox, oy, s)
        rand = ImageChops.subtract(form.filter(ImageFilter.MaxFilter(min(31, 2 * w + 1))), form)
        nonlocal maske
        # Inneres freistellen (verdeckt z.B. die Sonne), dann Umriss zeichnen
        maske = ImageChops.lighter(ImageChops.subtract(maske, form), rand)

    def sonne(cx, cy, r):
        dm = ImageDraw.Draw(maske)
        dm.ellipse([cx - r, cy - r, cx + r, cy + r], outline=255, width=w)
        for i in range(8):
            a = i * math.pi / 4
            dm.line([(cx + math.cos(a) * r * 1.5, cy + math.sin(a) * r * 1.5),
                     (cx + math.cos(a) * r * 2.15, cy + math.sin(a) * r * 2.15)], fill=255, width=w)

    def mond(cx, cy, r):
        m = Image.new("L", (g, g), 0)
        dm = ImageDraw.Draw(m)
        dm.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
        dm.ellipse([cx - r * 0.35, cy - r * 1.25, cx + r * 1.55, cy + r * 0.55], fill=0)
        rand = ImageChops.subtract(m.filter(ImageFilter.MaxFilter(min(31, 2 * w + 1))), m)
        nonlocal maske
        maske = ImageChops.lighter(maske, rand)

    dm = ImageDraw.Draw(maske)
    if art == "sonne":
        (sonne if tag else mond)(g * 0.5, g * 0.5, g * (0.2 if tag else 0.3))
    elif art == "teils":
        (sonne if tag else mond)(g * 0.33, g * 0.33, g * (0.13 if tag else 0.19))
        wolke(g * 0.10, g * 0.14, g * 0.90)
    elif art == "wolke":
        wolke(0, -g * 0.05)
    elif art == "nebel":
        for i, (a, b) in enumerate([(0.18, 0.82), (0.10, 0.74), (0.24, 0.90), (0.14, 0.70)]):
            y = g * (0.32 + i * 0.13)
            dm.line([(g * a, y), (g * b, y)], fill=255, width=w)
    else:
        wolke(0, -g * 0.16)
        dm = ImageDraw.Draw(maske)
        if art == "regen":
            for x in (0.34, 0.52, 0.70):
                dm.line([(g * x, g * 0.66), (g * (x - 0.07), g * 0.88)], fill=255, width=w)
        elif art == "niesel":
            for x, y in ((0.34, 0.70), (0.52, 0.78), (0.70, 0.70), (0.43, 0.88), (0.61, 0.88)):
                r = w * 0.9
                dm.ellipse([g * x - r, g * y - r, g * x + r, g * y + r], fill=255)
        elif art == "schnee":
            for x, y in ((0.32, 0.74), (0.55, 0.80), (0.75, 0.72)):
                r = g * 0.065
                for i in range(3):
                    a = i * math.pi / 3 + math.pi / 2
                    dm.line([(g * x - math.cos(a) * r, g * y - math.sin(a) * r),
                             (g * x + math.cos(a) * r, g * y + math.sin(a) * r)], fill=255, width=max(2, w - 1))
        elif art == "gewitter":
            dm.polygon([(g * 0.56, g * 0.58), (g * 0.38, g * 0.80), (g * 0.50, g * 0.80),
                        (g * 0.44, g * 0.98), (g * 0.66, g * 0.72), (g * 0.53, g * 0.72), (g * 0.60, g * 0.58)],
                       fill=255)
    return maske


def temp(v) -> str:
    if v is None:
        return "–"
    t = round(v)
    return f"{'−' if t < 0 else ''}{abs(t)}°"


def tag_label(tag: date, heute: date) -> str:
    if tag == heute:
        return "Heute"
    if tag == heute + timedelta(days=1):
        return "Morgen"
    return f"{WOCHENTAGE[tag.weekday()]}, {tag.day}. {MON_KURZ[tag.month - 1]}"


# --------------------------------------------------------------------------- Layout

def render(B: int, H: int, jetzt: datetime, wetter: dict | None, termine: list[Termin] | None,
           fehler: dict) -> Image.Image:
    L = Leinwand(B, H)
    breite_basis = 1072
    hoehe_basis = H / B * breite_basis
    M = 60                      # Seitenrand
    R = breite_basis - M        # rechter Rand
    heute = jetzt.date()

    # ---- Kopf
    L.text((M, 52), WOCHENTAGE[jetzt.weekday()].upper(), "SemiBold", 28, DUNKEL)
    L.text((M - 4, 88), f"{jetzt.day}. {MONATE[jetzt.month - 1]}", "Bold", 92)
    if wetter and wetter.get("ortsname"):
        L.text((R, 60), wetter["ortsname"], "SemiBold", 30, SCHWARZ, "ra")
    L.text((R, 102), f"Stand {jetzt:%H:%M}", "Regular", 26, DUNKEL, "ra")
    y = 222
    L.linie(M, y, R, y, 3)

    # ---- Wetter
    y += 34
    if wetter:
        c = wetter["current"]
        beschr, art = wmo(c.get("weather_code"))
        istag = bool(c.get("is_day", 1))
        L.symbol(art, M + 95, y + 92, 190, istag)
        L.text((M + 210, y + 4), temp(c.get("temperature_2m")), "Bold", 150)
        dly = wetter["daily"]
        x2 = 640
        L.text((x2, y + 14), L.kuerzen(beschr, R - x2, "SemiBold", 36), "SemiBold", 36)
        zeilen = [
            f"Max {temp(dly['temperature_2m_max'][0])}  ·  Min {temp(dly['temperature_2m_min'][0])}",
            f"Gefühlt {temp(c.get('apparent_temperature'))}",
            f"Wind {round(c.get('wind_speed_10m') or 0)} km/h",
            f"Regenrisiko {dly['precipitation_probability_max'][0] or 0} %",
        ]
        for i, z in enumerate(zeilen):
            L.text((x2, y + 66 + i * 38), L.kuerzen(z, R - x2, "Regular", 27), "Regular", 27, DUNKEL)

        # Vorhersage-Leiste: 4 Stundenwerte | 3 Tage
        y += 226
        L.linie(M, y, R, y, 1.5, MITTEL)
        y += 18
        hrs = wetter["hourly"]
        zeiten = [datetime.fromisoformat(t).replace(tzinfo=jetzt.tzinfo) for t in hrs["time"]]
        start = next((i for i, t in enumerate(zeiten) if t > jetzt), 0)
        idx = [start + k * 3 for k in range(4) if start + k * 3 < len(zeiten)]
        zellen = 7
        zb = (R - M) / zellen
        for k, i in enumerate(idx):
            cx = M + zb * (k + 0.5)
            _, a = wmo(hrs["weather_code"][i])
            L.text((cx, y), f"{zeiten[i]:%H} Uhr", "Medium", 24, DUNKEL, "ma")
            L.symbol(a, cx, y + 76, 74, bool(hrs["is_day"][i]))
            L.text((cx, y + 122), temp(hrs["temperature_2m"][i]), "SemiBold", 32, SCHWARZ, "ma")
            p = hrs["precipitation_probability"][i]
            if p is not None and p >= 20:
                L.text((cx, y + 162), f"{p} %", "Regular", 22, DUNKEL, "ma")
        L.linie(M + zb * 4, y + 6, M + zb * 4, y + 180, 1.5, MITTEL)
        for k in range(1, 4):
            if k >= len(dly["time"]):
                break
            cx = M + zb * (4 + k - 0.5)
            tg = date.fromisoformat(dly["time"][k])
            _, a = wmo(dly["weather_code"][k])
            L.text((cx, y), WT_KURZ[tg.weekday()], "SemiBold", 24, SCHWARZ, "ma")
            L.symbol(a, cx, y + 76, 74, True)
            L.text((cx, y + 122), f"{temp(dly['temperature_2m_max'][k])} / {temp(dly['temperature_2m_min'][k])}",
                   "Medium", 26, SCHWARZ, "ma")
            p = dly["precipitation_probability_max"][k]
            if p is not None and p >= 20:
                L.text((cx, y + 162), f"{p} %", "Regular", 22, DUNKEL, "ma")
        y += 206
    else:
        L.text((M, y + 10), "Wetter gerade nicht verfügbar", "SemiBold", 34)
        L.text((M, y + 60), L.kuerzen(fehler.get("wetter", ""), R - M, "Regular", 24), "Regular", 24, DUNKEL)
        y += 120

    L.linie(M, y, R, y, 3)
    y += 30

    # ---- Kalender
    unten = hoehe_basis - 50
    L.text((M, y), "TERMINE", "SemiBold", 24, DUNKEL)
    y += 50
    if termine is None:
        L.text((M, y), "Kalender nicht erreichbar", "SemiBold", 34)
        L.text((M, y + 50), L.kuerzen(fehler.get("kalender", ""), R - M, "Regular", 24), "Regular", 24, DUNKEL)
        return L.fertig()
    if not termine and not os.environ.get("ICS_URLS") and not os.environ.get("DEMO"):
        L.text((M, y), "Noch kein Kalender verbunden (ICS_URLS fehlt)", "Regular", 30, DUNKEL)
        return L.fertig()

    tage = int(os.environ.get("KALENDER_TAGE", "7"))
    zeit_sp = 168
    titel_x = M + zeit_sp
    ausgelassen = 0
    voll = False
    gezeigt_tage = 0
    for n in range(tage):
        tag = heute + timedelta(days=n)
        tag_b = datetime.combine(tag, time.min, jetzt.tzinfo)
        tag_e = tag_b + timedelta(days=1)
        ganz = [t for t in termine if t.ganztags and t.beginn <= tag < t.ende]
        zeit = [t for t in termine if not t.ganztags and t.beginn < tag_e and t.ende > tag_b
                and (t.beginn >= tag_b or n == 0)]
        if n == 0:
            zeit = [t for t in zeit if t.ende > jetzt or t.ende == t.beginn and t.beginn > jetzt - timedelta(minutes=30)]
        zeit.sort(key=lambda t: t.beginn)
        ganz.sort(key=lambda t: t.titel.lower())
        if not ganz and not zeit:
            if n == 0:
                if y + 100 > unten:
                    break
                L.text((M, y), "Heute", "Bold", 32)
                y += 52
                L.text((titel_x, y), "Keine weiteren Termine", "Regular", 30, DUNKEL)
                y += 66
                gezeigt_tage += 1
            continue
        if voll:
            ausgelassen += len(ganz) + len(zeit)
            continue
        if y + 52 + 56 > unten:
            voll = True
            ausgelassen += len(ganz) + len(zeit)
            continue
        if gezeigt_tage:
            y += 8
        L.text((M, y), tag_label(tag, heute), "Bold", 32)
        y += 52
        gezeigt_tage += 1
        for t in ganz + zeit:
            hat_ort = bool(t.ort) and not t.ganztags
            hoehe = 84 if hat_ort else 54
            if y + hoehe > unten:
                voll = True
                ausgelassen += 1
                continue
            titel_b = R - titel_x
            if t.ganztags:
                txt = L.kuerzen(t.titel, titel_b - 28, "SemiBold", 28)
                tb = L.breite(txt, "SemiBold", 28)
                L.rechteck(titel_x - 14, y - 4, titel_x + tb + 14, y + 40, SCHWARZ, 8)
                L.text((titel_x, y + 2), txt, "SemiBold", 28, WEISS)
                if t.ende - t.beginn > timedelta(days=1):
                    letzt = t.ende - timedelta(days=1)
                    L.text((M, y + 4), f"bis {letzt.day}.{letzt.month}.", "Medium", 24, DUNKEL)
                else:
                    L.text((M, y + 4), "ganztags", "Medium", 24, DUNKEL)
            else:
                laeuft = t.beginn <= jetzt < t.ende
                if t.beginn < tag_b:
                    zt = "bis " + (f"{t.ende:%H:%M}" if t.ende < tag_e else "…")
                    L.text((M, y + 2), zt, "Medium", 26, DUNKEL)
                else:
                    L.text((M, y), f"{t.beginn:%H:%M}", "SemiBold", 30)
                if laeuft:
                    L.rechteck(M - 26, y + 2, M - 16, y + 38, SCHWARZ, 3)
                L.text((titel_x, y), L.kuerzen(t.titel, titel_b, "Medium", 30), "Medium", 30)
                if hat_ort:
                    neben = f"{t.ort}"
                    if laeuft:
                        neben = f"Jetzt · bis {t.ende:%H:%M} · {t.ort}"
                    L.text((titel_x, y + 40), L.kuerzen(neben, titel_b, "Regular", 24), "Regular", 24, DUNKEL)
                elif laeuft:
                    hoehe = 84
                    L.text((titel_x, y + 40), f"Jetzt · bis {t.ende:%H:%M}", "Regular", 24, DUNKEL)
            y += hoehe
    if ausgelassen:
        L.text((M, min(y + 6, unten - 10)), f"+ {ausgelassen} weitere Termine", "Medium", 26, DUNKEL)
    elif gezeigt_tage <= 1 and n == tage - 1 and y + 60 < unten:
        L.text((M, y + 10), f"Keine weiteren Termine in den nächsten {tage} Tagen", "Regular", 26, DUNKEL)
    return L.fertig()


# --------------------------------------------------------------------------- Main

def main() -> int:
    tz = ZoneInfo(os.environ.get("ZEITZONE", "Europe/Berlin"))
    jetzt = datetime.now(tz)
    if os.environ.get("JETZT"):  # nur für Tests
        jetzt = datetime.fromisoformat(os.environ["JETZT"]).replace(tzinfo=tz)
    tage = int(os.environ.get("KALENDER_TAGE", "7"))
    fehler: dict[str, str] = {}

    if os.environ.get("DEMO"):
        wetter, termine = demo_daten(tz, jetzt)
    else:
        try:
            wetter = hole_wetter(tz)
        except Exception as e:  # Bild trotzdem erzeugen
            wetter, fehler["wetter"] = None, str(e)
            print("Wetter-Fehler:", e, file=sys.stderr)
        try:
            termine = hole_termine(tz, jetzt.date(), tage)
        except Exception as e:
            termine, fehler["kalender"] = None, str(e)
            print("Kalender-Fehler:", e, file=sys.stderr)

    ziel = Path(os.environ.get("AUSGABE", "ausgabe"))
    ziel.mkdir(parents=True, exist_ok=True)
    aufl = os.environ.get("AUFLOESUNGEN", "758x1024,1072x1448,1236x1648,1264x1680")
    for a in re.split(r"[\s,]+", aufl.strip()):
        b, h = (int(x) for x in a.lower().split("x"))
        img = render(b, h, jetzt, wetter, termine, fehler)
        pfad = ziel / f"dash-{b}x{h}.png"
        img.save(pfad, optimize=True)  # 8-Bit-Graustufen, wie eips es erwartet
        print("geschrieben:", pfad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
