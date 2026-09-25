from config import ACCESSORIES_LIST_GID
from sync.accessory_parsers import HEADERS


def accessory_payload(entries=None, headers=None):
    headers = list(headers or HEADERS)
    if entries is None:
        entries = [dict(name='Test cap charm', accessory_id='testcap', tier='S',
                        description='Raise Dmg. Limit by 50,000.', verification='verified_text',
                        comments='A useful damage-cap accessory.', gacha_a4='No')]
    rows = [[{'formattedValue': h} for h in headers]]
    rows += [[{'formattedValue': str(item.get(HEADERS[h], ''))} for h in headers] for item in entries]
    return {'sheets': [{'properties': {'sheetId': ACCESSORIES_LIST_GID, 'title': 'Tier List'},
                        'data': [{'rowData': [{'values': row} for row in rows]}]}]}
