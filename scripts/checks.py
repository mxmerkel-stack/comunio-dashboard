"""Prüfung von Aufstellung und Kontostand vor dem Aufstellungsschluss (Comunio-Regeln).

Regeln (comunio.de, Spielregeln):
- Ist der Kontostand zum Aufstellungsschluss negativ, gibt es für den Spieltag keine Punkte.
- Jede unbesetzte Position kostet 4 Punkte.
- Spieler auf einer fremden Position bekommen keine Punkte.
- Aufstellungsschluss ist der Anpfiff des ersten Spiels des Spieltags.
"""
POS_DE = {"keeper": "Torwart", "defender": "Abwehr", "midfielder": "Mittelfeld", "striker": "Sturm"}
OUT = {"INJURED": "verletzt", "REHABILITATION": "in der Reha", "SUSPENDED": "gesperrt/freigestellt",
       "REDBANNED": "rot-gesperrt", "YELLOWREDBANNED": "gelb-rot-gesperrt", "YELLOWBANNED": "gelb-gesperrt",
       "AWAY": "abwesend", "RETIRED": "nicht mehr in der Liga"}


def f1(v):
    return f"{v:.1f}".replace(".", ",")


def eur(v):
    return f"{v:,.0f} €".replace(",", ".")


def slot_types(tactic):
    t = str(tactic or "").replace("-", "")
    if len(t) != 3 or not t.isdigit():
        return None
    d, m, f = int(t[0]), int(t[1]), int(t[2])
    return ["striker"] * f + ["midfielder"] * m + ["defender"] * d + ["keeper"]


def lineup_checks(raw, nm, balance, offers, squad_rows):
    """nm: nextMatch-Block aus dem Dashboard; squad_rows: eigener Kader mit Status/Prognose."""
    issues = []
    lu = raw.get("lineup") or {}
    slots = lu.get("slots") or []
    by_id = {r["id"]: r for r in (nm.get("squad") or [])}
    own = {r["id"]: r for r in squad_rows}

    if balance is not None and balance < 0:
        top = sorted(offers or [], key=lambda o: -(o.get("price") or 0))[:3]
        tip = (" Beste Angebote: " + ", ".join(f"{o['player']} {eur(o['price'])} ({o['from']})" for o in top) + ".") if top else ""
        issues.append({"level": "warn", "title": f"Kontostand im Minus: {eur(balance)}",
                       "detail": f"Ist das Konto beim Aufstellungsschluss noch negativ, gibt es für den ganzen Spieltag keine Punkte. "
                                 f"Du musst mindestens {eur(-balance)} einnehmen.{tip}"})

    types = slot_types(lu.get("tactic"))
    empty = [s for s in slots if not s.get("id")]
    if slots and empty:
        issues.append({"level": "warn", "title": f"{len(empty)} unbesetzte Position(en)",
                       "detail": f"Jede leere Position kostet 4 Punkte – zusammen −{4 * len(empty)} Punkte."})
    elif not slots and not lu.get("ids"):
        issues.append({"level": "warn", "title": "Keine Aufstellung gefunden",
                       "detail": "Comunio hat keine gespeicherte Aufstellung geliefert. Bitte prüfen."})

    for i, s in enumerate(slots):
        if not s.get("id"):
            continue
        want = types[i] if types and i < len(types) else None
        if want and s.get("type") and s["type"] != want:
            issues.append({"level": "warn", "title": f"{s['name']} steht auf der falschen Position",
                           "detail": f"{s['name']} ({POS_DE.get(s['type'], s['type'])}) ist als {POS_DE.get(want, want)} aufgestellt und bekommt so keine Punkte."})
        st = s.get("status") or "ACTIVE"
        r = by_id.get(s["id"]) or {}
        inj = (own.get(s["id"]) or {}).get("injury") or {}
        if st in OUT:
            ret = f" Erwartete Rückkehr: {inj['return']}." if inj.get("return") else ""
            issues.append({"level": "warn", "title": f"{s['name']} ist {OUT[st]}",
                           "detail": f"Fällt voraussichtlich aus und holt 0 Punkte.{ret} {s.get('statusInfo') or ''}".strip()})
        elif st == "WEAKENED":
            issues.append({"level": "info", "title": f"{s['name']} ist angeschlagen",
                           "detail": (s.get("statusInfo") or "Einsatz fraglich.") + (f" Einschätzung: {inj['return']}." if inj.get("return") else "")})
        elif r and r.get("p_start") is not None and r["p_start"] < 0.35 and not r.get("played"):
            issues.append({"level": "info", "title": f"{s['name']}: nur {round(r['p_start'] * 100)} % Startelf-Chance",
                           "detail": f"Einsatz insgesamt {round(r['p_play'] * 100)} %, erwartet {f1(r['e_match'])} Punkte."})

    best = None
    if nm.get("squad"):
        now, top = nm.get("nowTotal") or 0, nm.get("total") or 0
        ins = [r["name"] for r in nm["squad"] if r.get("inBest") and not r.get("inLineup")]
        outs = [r["name"] for r in nm["squad"] if r.get("inLineup") and not r.get("inBest")]
        best = {"now": now, "best": top, "gain": round(top - now, 2), "formation": nm.get("formation"),
                "formationNow": "-".join(str(lu.get("tactic") or "")) if str(lu.get("tactic") or "").isdigit() else lu.get("tactic"), "in": ins, "out": outs, "optimal": top - now < 0.5}
        if not best["optimal"]:
            issues.append({"level": "info", "title": f"Bessere Aufstellung möglich (+{f1(top - now)} Punkte)",
                           "detail": f"Empfehlung {nm.get('formation')}: rein {', '.join(ins) or '–'}, raus {', '.join(outs) or '–'}. "
                                     f"Erwartet {f1(top)} statt {f1(now)} Punkte."})
    warn = sum(1 for i in issues if i["level"] == "warn")
    return {"md": nm.get("md"), "deadline": nm.get("date"), "started": nm.get("started"), "balance": balance,
            "issues": issues, "warnings": warn, "best": best}
