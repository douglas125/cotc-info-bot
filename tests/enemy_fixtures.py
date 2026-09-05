"""Small source-shaped enemy payloads; no network or live sheet assumptions."""
from __future__ import annotations

import config


def cell(text="", formula="", bg=None):
    value = {"formattedValue": str(text)} if text != "" else {}
    if formula:
        value["userEnteredValue"] = {"formulaValue": formula}
    if bg:
        value["effectiveFormat"] = {"backgroundColor": {
            k: int(bg[i:i + 2], 16) / 255 for k, i in (("red", 1), ("green", 3), ("blue", 5))
        }}
    return value


def sheet(gid, title, rows):
    return {"properties": {"sheetId": gid, "title": title},
            "data": [{"rowData": [{"values": row} for row in rows]}]}


def enemy_payload(ranks=("EX3",), members=("Captain",), *, hp=None, canonical="New display name"):
    """Complete tab inventory with one reference-backed ranked encounter."""
    tabs = {t.gid: sheet(t.gid, t.name, []) for t in config.ENEMIES_TABS}
    for key, gid in config.ENEMY_DATA_TAB_GIDS.items():
        tabs[gid] = sheet(gid, {"Osterra": "Orsterra Data", "Solistia": "Solistia Data",
                               "NPCs": "120 NPCs Data", "NPCs140": "140 NPCs Data"}[key], [])
    data = [[cell(), cell("Internal encounter"), cell("Shields"), cell("HP"), cell("Speed")]]
    starts = []
    for pos, name in enumerate(members):
        starts.append(len(data) + 1)
        for index, rank in enumerate(ranks):
            value = (hp or {}).get((pos, rank), str(10000 + pos * 1000 + index))
            data.append([cell(name if index == 0 else ""), cell(rank), cell("30"), cell(value), cell("500")])
    data_gid = config.ENEMY_DATA_TAB_GIDS['Osterra']
    tabs[data_gid] = sheet(data_gid, "Orsterra Data", data)
    display = [[cell() for _ in range(13)] for _ in range(16)]
    display[3][0] = cell(bg="#222222")
    display[3][1] = cell(canonical)
    display[3][5] = cell(ranks[-1])
    display[3][12] = cell(bg="#222222")
    display[5][6] = cell(formula="=SH")
    for pos, start in enumerate(starts):
        display[6][2 + pos] = cell(str(pos + 1))
        display[7][2 + pos] = cell("10,000", f"=VLOOKUP(F4,'Orsterra Data'!$B${start}:$E${start + len(ranks) - 1},3,FALSE)")
        display[6 + pos][7] = cell("30")
        display[6 + pos][8] = cell(formula="=Sword")
    spec = config.ENEMIES_TABS[0]
    tabs[spec.gid] = sheet(spec.gid, spec.name, display)
    return {"spreadsheetId": config.ENEMIES_SPREADSHEET_ID, "sheets": list(tabs.values())}


def fetch_with_enemies(character_payload):
    def fetch(api_key, spreadsheet_id=None):
        if spreadsheet_id == config.ENEMIES_SPREADSHEET_ID:
            return enemy_payload()
        return character_payload
    return fetch
