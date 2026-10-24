#!/usr/bin/env python3
"""Convert the W01 learner workbook into the existing fit-gap contract.

The XLSX reader uses Python's standard library. Schema validation uses
jsonschema, which is already a dependency of the supplied student product.
This tool never calls a model or decides the learner's fit-gap categories.
"""
import argparse
import json
import posixpath
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
FIT_HEADERS = ['编号', '客户诉求', '适配分类', '能力 ID', '证据状态', '下一步']
EVIDENCE_HEADERS = ['编号', '证据来源', '定位', '支持内容']
STATUSES = {'已核实': 'verified', '角色转述': 'reported', '未知': 'unknown', '冲突': 'conflicting'}


class InputError(ValueError):
    """A learner-correctable input problem."""


def read_workbook(path):
    """Read saved values, including inline strings and shared rich strings."""
    with zipfile.ZipFile(path) as archive:
        if sum(info.file_size for info in archive.infolist()) > 80_000_000:
            raise InputError('工作簿过大，请仅保留本周填写表。')
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(t.text or '' for t in si.findall('.//s:t', NS))
                       for si in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        relationships = {
            rel.attrib['Id']: rel.attrib
            for rel in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        }
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        sheets = {}
        for sheet in workbook.findall('s:sheets/s:sheet', NS):
            name = sheet.attrib['name']
            if name not in ('适配判断', '证据明细'):
                continue
            if name in sheets:
                raise InputError(f'工作表重名：{name}。')
            relation = relationships[sheet.attrib[REL]]
            if relation.get('TargetMode') == 'External':
                raise InputError(f'{name} 不能引用外部工作表。')
            target = relation['Target']
            target = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl', target))
            if not target.startswith('xl/'):
                raise InputError(f'{name} 的文件位置无效。')
            cells = {}
            for cell in ET.fromstring(archive.read(target)).findall('.//s:sheetData/s:row/s:c', NS):
                address = cell.attrib['r']
                if cell.find('s:f', NS) is not None:
                    raise InputError(f'{name}!{address} 含公式，请改为实际填写的值后保存。')
                if cell.attrib.get('t') == 'e':
                    raise InputError(f'{name}!{address} 是 Excel 错误值，请先修正。')
                value = cell.findtext('s:v', default='', namespaces=NS)
                if cell.attrib.get('t') == 's':
                    value = strings[int(value)]
                elif cell.attrib.get('t') == 'inlineStr':
                    value = ''.join(t.text or '' for t in cell.findall('.//s:t', NS))
                cells[address] = value.strip()
            sheets[name] = cells
        if set(sheets) != {'适配判断', '证据明细'}:
            raise InputError('请保留“适配判断”和“证据明细”两张工作表及其名称。')
        return sheets


def rows(cells, headers, sheet_name):
    actual = [cells.get(f'{chr(65 + i)}9', '') for i in range(len(headers))]
    if actual != headers:
        raise InputError(f'{sheet_name} 第 9 行表头不符，请使用原模板，不移动或改名表头。')
    grouped = {}
    for address, value in cells.items():
        match = re.fullmatch(r'([A-Z]+)([0-9]+)', address)
        if not match:
            raise InputError(f'{sheet_name} 中存在无效单元格位置：{address}。')
        grouped.setdefault(int(match[2]), {})[match[1]] = value
    for row, row_cells in sorted(grouped.items()):
        if row < 10:
            continue
        values = [row_cells.get(chr(65 + i), '') for i in range(len(headers))]
        for column, value in row_cells.items():
            if value and (len(column) > 1 or ord(column) - 65 >= len(headers)):
                raise InputError(f'{sheet_name}!{column}{row} 位于填写列之外，请移回对应字段。')
        if any(values):
            yield row, values


def convert(sheets, root):
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise InputError('请使用工程虚拟环境中的 Python 运行；依赖安装方法见工程 README。') from exc
    root = Path(root)
    schema = json.loads((root / 'contracts/w01/product_fit_gap.schema.json').read_text(encoding='utf-8'))
    capabilities = json.loads((root / 'product/capabilities.json').read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    known = {item['id'] for item in capabilities['capabilities']}
    cells = sheets['适配判断']
    for address, label in [('A4', '契约版本'), ('C4', '产品版本'), ('E4', '能力矩阵版本')]:
        if cells.get(address) != label:
            raise InputError(f'适配判断!{address} 应为“{label}”，请保留模板中的版本区域。')
    output = {
        'contract_version': cells.get('B4', ''),
        'product_version': cells.get('D4', ''),
        'capability_matrix_version': cells.get('F4', ''),
        'items': [],
    }
    item_rows = []
    by_id = {}
    for row, values in rows(cells, FIT_HEADERS, '适配判断'):
        gap_id, request, category, capability_text, status, next_step = values
        if gap_id in by_id:
            raise InputError(f'适配判断第 {row} 行编号重复：{gap_id}。每个诉求只保留一行。')
        item = {
            'id': gap_id, 'request': request, 'category': category,
            'capability_ids': re.split(r'[\s,，;；、]+', capability_text) if capability_text else [],
            'evidence_status': STATUSES.get(status, status), 'evidence': [], 'next_step': next_step,
        }
        by_id[gap_id] = item
        output['items'].append(item)
        item_rows.append(row)
    for row, values in rows(sheets['证据明细'], EVIDENCE_HEADERS, '证据明细'):
        gap_id, source, locator, note = values
        if gap_id not in by_id:
            raise InputError(f'证据明细第 {row} 行编号“{gap_id}”在适配判断中不存在。')
        if not all(values):
            raise InputError(f'证据明细第 {row} 行请填写编号、证据来源、定位和支持内容；不用的整行留空。')
        by_id[gap_id]['evidence'].append({'source': source, 'locator': locator, 'note': note})
    if output['contract_version'] == 'w01-0.2.1' and schema.get('$id') != 'urn:fde:w01:fit-gap:0.2.1':
        raise InputError('当前工程仍是旧版 fit-gap 契约。请按任务书使用 fit-gap 0.2.1 修订工程包；不要把“待判断”改成其他分类来绕过检查。')
    errors = list(Draft202012Validator(schema).iter_errors(output))
    if errors:
        messages = []
        for error in errors[:8]:
            location = list(error.absolute_path)
            if len(location) >= 2 and location[0] == 'items' and isinstance(location[1], int):
                where = f'适配判断第 {item_rows[location[1]]} 行'
            else:
                where = '版本或填写条目'
            field = '.'.join(map(str, location)) or '整份表'
            messages.append(f'{where}（{field}）：{error.message}')
        raise InputError('\n'.join(messages))
    for row, item in zip(item_rows, output['items']):
        unknown = set(item['capability_ids']) - known
        if unknown:
            raise InputError(f'适配判断第 {row} 行存在未知能力 ID：{", ".join(sorted(unknown))}。请核对工程 product/capabilities.json。')
        if item['evidence_status'] == 'verified' and not item['evidence']:
            raise InputError(f'适配判断第 {row} 行选择了“已核实”，但没有对应证据。请补充证据或据实调整状态。')
    return output


def validate_week(week):
    if not re.fullmatch(r'w(?:0[1-9]|1[0-2])', week):
        raise InputError('周次须为 w01 至 w12，例如 --week w02。')
    return week


def write_output(value, root, overwrite=False, week='w01'):
    validate_week(week)
    root = Path(root).resolve()
    output = root / 'artifacts' / week / 'product_fit_gap.json'
    if output.is_symlink() or not output.resolve().is_relative_to(root):
        raise InputError('输出位置不能通过软链接指向工程目录以外。')
    if output.exists() and not overwrite:
        raise InputError('product_fit_gap.json 已存在。本次未覆盖；确认要更新时加 --overwrite。')
    output.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    # Exclusive creation also protects an existing answer created after the check.
    with output.open('w' if overwrite else 'x', encoding='utf-8') as handle:
        handle.write(data)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description='把 W1 fit-gap Excel 填写稿转换为正式提交 JSON。')
    parser.add_argument('--root', type=Path, default=Path.cwd(), help='解压后的工程目录，默认当前目录')
    parser.add_argument('--week', default='w01', help='填写稿与输出所在周次，w01 至 w12；默认 w01')
    parser.add_argument('--input', type=Path, help='填写后的 .xlsx；默认工程 artifacts/所选周次/product_fit_gap.xlsx')
    parser.add_argument('--overwrite', action='store_true', help='确认覆盖已有 fit-gap JSON')
    args = parser.parse_args(argv)
    try:
        validate_week(args.week)
        source = args.input or args.root / 'artifacts' / args.week / 'product_fit_gap.xlsx'
        value = convert(read_workbook(source), args.root)
        destination = write_output(value, args.root, args.overwrite, args.week)
    except (InputError, OSError, ValueError, KeyError, IndexError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(f'未生成或更新 JSON：{exc}', file=sys.stderr)
        return 1
    print(f'已生成 {destination}，共 {len(value["items"])} 条适配判断。')
    print('仅完成格式与引用检查；请继续完成其余产出物并运行公开 checker，最终判断由助教复核。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
