"""Header-based parser for the public accessory catalog."""
from config import ACCESSORIES_LIST_GID
from sync.fetch import sheet_by_gid

HEADERS = {
    'Name': 'name', 'Gacha 5* A4?': 'gacha_a4', 'Rank': 'tier',
    'Explanation': 'comments',
    '(Eventually) Obtainable from Awakening Shard Exchange?': 'exchange',
    'exact_effect_text': 'description', 'stats_text': 'stats',
    'in_game_name': 'in_game_name', 'character': 'owner',
    'equip_restriction': 'restriction', 'verification_status': 'verification',
    'verified_on': 'verified_on', 'tier_status': 'tier_status',
    'audit_notes': 'audit_notes', 'accessory_id': 'accessory_id', 'is_a4': 'is_a4',
}
REQUIRED = {'Name', 'accessory_id', 'exact_effect_text', 'verification_status', 'Rank', 'Explanation'}


def parse_accessories(payload):
    sheet = sheet_by_gid(payload, ACCESSORIES_LIST_GID)
    if sheet is None:
        raise ValueError('Accessory Tier List tab is missing')
    rows = {}
    for grid in sheet.get('data', []):
        start = grid.get('startRow', 0)
        col = grid.get('startColumn', 0)
        for offset, row in enumerate(grid.get('rowData', [])):
            dest = rows.setdefault(start + offset + 1, {})
            for j, cell in enumerate(row.get('values', []), col):
                dest[j] = str(cell.get('formattedValue', ''))
    headers = {v.strip(): k for k, v in rows.get(1, {}).items() if v.strip()}
    if not REQUIRED <= headers.keys():
        raise ValueError(f'Accessory headers missing: {sorted(REQUIRED - headers.keys())}')
    if len(headers) != len([v for v in rows.get(1, {}).values() if v.strip()]):
        raise ValueError('Duplicate accessory headers')
    result, warnings, seen = [], [], set()
    for row_num, cells in sorted(rows.items()):
        if row_num == 1 or not any(v.strip() for v in cells.values()):
            continue
        item = {dest: cells.get(headers.get(src, -1), '') for src, dest in HEADERS.items()}
        for field in ('name', 'accessory_id', 'verification', 'tier', 'is_a4', 'gacha_a4', 'exchange'):
            item[field] = item[field].strip()
        if not item['name'] or not item['accessory_id'] or item['accessory_id'] in seen:
            raise ValueError(f'Accessory row {row_num}: missing name/ID or duplicate ID')
        if len(item['accessory_id']) > 96:
            raise ValueError(f'Accessory row {row_num}: ID exceeds autocomplete limit')
        seen.add(item['accessory_id'])
        status = item['verification']
        if status not in ('verified_text', 'verified_no_effect', 'not_verified'):
            raise ValueError(f'Accessory row {row_num}: invalid verification status')
        if (status == 'verified_text') != bool(item['description'].strip()):
            raise ValueError(f'Accessory row {row_num}: inconsistent description/verification')
        if item['tier'] not in ('S+', 'S', 'A', 'B', 'C', 'D'):
            warnings.append(f"{item['name']}: unrated ({item['tier'] or 'blank'})")
            item['tier'] = 'Unrated'
        for field, choices in [('gacha_a4', ('Yes', 'No')), ('exchange', ('Yes', 'No', 'N/A'))]:
            if item[field] not in choices:
                item[field] = 'Unverified'
        if item['is_a4'] not in ('Yes', 'No', 'Unverified', ''):
            raise ValueError(f'Accessory row {row_num}: invalid is_a4')
        if item['is_a4'] in ('', 'Unverified'):
            if item['gacha_a4'] == 'Yes':
                item['is_a4'] = 'Yes'
            elif item['exchange'] == 'N/A':
                item['is_a4'] = 'No'
            else:
                item['is_a4'] = 'Unverified'
        if item['gacha_a4'] == 'Yes' and item['is_a4'] == 'No':
            raise ValueError(f'Accessory row {row_num}: contradictory A4 flags')
        item['source_row'] = row_num
        result.append(item)
    if not result:
        raise ValueError('Accessory import is empty')
    if 'is_a4' not in headers:
        warnings.append('is_a4 column missing; using gacha A4 and exchange N/A evidence where available')
    pending = sum(x['verification'] == 'not_verified' for x in result)
    if pending:
        warnings.append(f'{pending} accessories lack verified effect descriptions')
    return result, warnings
