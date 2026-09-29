"""Sample translation memories so the workspace can be tried without a TMS login."""
from __future__ import annotations

from copy import deepcopy

DEMOS = [
    {
        "id": "demo:ui",
        "name": "Product UI",
        "source": "en-US",
        "targets": ["es-419"],
        "units": [
            ("1", "Open the project", "Abrir el proyecto", "20260301T100000Z", "editor"),
            ("2", "Save", "", "20260302T100000Z", "editor"),
            ("3", "42", "42", "20260302T100000Z", "editor"),
            ("4", ">>>", ">>>", "20260302T100000Z", "editor"),
            ("5", "Open the project", "Abrir el proyecto", "20260303T100000Z", "editor"),
            ("6", "Settings", "Settings", "20260303T100000Z", "editor"),
            ("7", "Click the the button", "Haga clic en el botón", "20260304T100000Z", "editor"),
            ("8", "https://example.com/help", "https://example.com/help", "20260304T100000Z", "editor"),
            ("9", "  Hello   world  ", "Hola   mundo ", "20260305T100000Z", "editor"),
            ("10", "Changes were saved.", "Los cambios se guardaron", "20260305T100000Z", "editor"),
            ("11", "The plan costs $19.99 per month.", "El plan cuesta 19,99 al mes.", "20260306T100000Z", "editor"),
            ("12", "Read more at https://brand.example/docs.", "Más información en https://brand.example/ayuda.", "20260306T100000Z", "editor"),
            ("13", "Log in to the dashboard", "Entrar al tablero", "20260307T100000Z", "editor"),
            ("14", "Archive", "Archivar", "20200115T100000Z", "olduser"),
            ("15", "Search projects", "Buscar proyectos", "20260308T100000Z", "editor"),
            ("16", "Welcome back", "  Bienvenido   de nuevo. ", "20260308T100000Z", "editor"),
        ],
    },
    {
        "id": "demo:mkt",
        "name": "Spring campaign",
        "source": "en-US",
        "targets": ["fr-FR"],
        "units": [
            ("1", "A quieter kind of luxury.", "Un luxe plus calme.", "20260201T100000Z", "writer"),
            ("2", "Shop the edit", "Découvrir la sélection", "20260201T100000Z", "writer"),
            ("3", "New", "", "20260202T100000Z", "writer"),
            ("4", "100", "100", "20260202T100000Z", "writer"),
            ("5", "***", "***", "20260202T100000Z", "writer"),
            ("6", "Shop the edit", "Découvrir la sélection", "20260203T100000Z", "writer"),
            ("7", "Limited", "Limited", "20260203T100000Z", "writer"),
            ("8", "Free shipping over €50.", "Livraison offerte dès 50 €.", "20260204T100000Z", "writer"),
            ("9", "See the lookbook!", "Voir le lookbook", "20260204T100000Z", "writer"),
            ("10", "  Crafted   slowly. ", "Fabriqué   lentement.", "20260205T100000Z", "writer"),
            ("11", "https://campaign.example", "https://campaign.example", "20260205T100000Z", "writer"),
            ("12", "Join the the list", "Rejoindre la liste", "20260206T100000Z", "writer"),
            ("13", "Lookbook: https://campaign.example/ss", "Lookbook : https://campaign.example/fr", "20260206T100000Z", "writer"),
            ("14", "This season, less noise.", "Cette saison, moins de bruit.", "20260207T100000Z", "writer"),
        ],
    },
    {
        "id": "demo:legal",
        "name": "Policy clauses",
        "source": "en-GB",
        "targets": ["de-DE"],
        "units": [
            ("1", "The agreement starts on the signature date.", "Der Vertrag beginnt am Tag der Unterzeichnung.", "20260110T100000Z", "counsel"),
            ("2", "Liability", "Liability", "20260110T100000Z", "counsel"),
            ("3", "Fees are billed in GBP.", "Die Gebühren werden in EUR berechnet.", "20260111T100000Z", "counsel"),
            ("4", "Notice", "", "20260111T100000Z", "counsel"),
            ("5", "See https://legal.example/terms for the full text.", "Den vollständigen Text finden Sie unter https://legal.example/agb.", "20260112T100000Z", "counsel"),
            ("6", "The parties shall shall cooperate.", "Die Parteien arbeiten zusammen.", "20260112T100000Z", "counsel"),
            ("7", "Governing law.", "Anwendbares Recht", "20260113T100000Z", "counsel"),
            ("8", "  Confidential.  ", "Vertraulich.", "20260113T100000Z", "counsel"),
            ("9", "12", "12", "20260114T100000Z", "counsel"),
            ("10", "§", "§", "20260114T100000Z", "counsel"),
            ("11", "The agreement starts on the signature date.", "Der Vertrag beginnt am Tag der Unterzeichnung.", "20260115T100000Z", "legacy"),
            ("12", "Either party may terminate.", "Jede Partei kann kündigen.", "20190601T100000Z", "legacy"),
            ("13", "https://legal.example/privacy", "https://legal.example/privacy", "20260116T100000Z", "counsel"),
            ("14", "Schedules form part of this agreement.", "Die Anhänge sind Bestandteil dieses Vertrags.", "20260116T100000Z", "counsel"),
        ],
    },
]


def list_tms() -> list[dict]:
    return [
        {
            "id": tm["id"],
            "name": tm["name"],
            "source": tm["source"],
            "targets": tm["targets"],
            "units": len(tm["units"]),
        }
        for tm in DEMOS
    ]


def tus_for(tm_id: str) -> list[dict]:
    for tm in DEMOS:
        if tm["id"] == tm_id:
            src = tm["source"]
            tgt = tm["targets"][0]
            return [
                {
                    "tuid": row[0],
                    "source": row[1],
                    "target": row[2],
                    "src_lang": src,
                    "tgt_lang": tgt,
                    "changedate": row[3],
                    "changeid": row[4],
                }
                for row in tm["units"]
            ]
    raise KeyError(tm_id)


def clone_tus(tm_id: str) -> list[dict]:
    return deepcopy(tus_for(tm_id))
