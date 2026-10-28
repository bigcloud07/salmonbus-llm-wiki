#!/usr/bin/env python3
"""File-based retention and forecast evaluation analysis; see README for contracts."""
import argparse
import csv
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value, name, integer=False):
    try:
        n = float(value)
        require(math.isfinite(n) and n >= 0 and (not integer or n.is_integer()), f'invalid {name}')
        return int(n) if integer else n
    except (ValueError, TypeError):
        raise ValueError(f'invalid {name}: {value!r}')


def table(path, fields):
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        require(reader.fieldnames == fields, f'{path}: expected columns {fields}')
        rows = list(reader)
        require(all(None not in r and all(v is not None and v.strip() for v in r.values()) for r in rows), f'{path}: missing or extra field')
        return rows


def day(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f'invalid ISO date: {value!r}')


def retention(path, config):
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.reader(f))
    require(len(rows) >= 10 and all(len(rows[i]) == 1 for i in (0, 1, 3, 4, 5)) and not rows[2] and not rows[6], 'unsupported Amplitude metadata layout')
    metric = rows[5][0]
    require(metric in ('N-Day Retention', 'Unbounded Retention'), 'unsupported retention metric')
    require(rows[3][0] == config['start_event'] and rows[4][0] == config['return_event'] and metric == config['metric'], 'retention metadata does not match contract')
    header = rows[7]
    prefix = 'Day' if metric == 'N-Day Retention' else 'Week'
    require(header[:3] == ['Segment', 'Start Date', 'Users'] and header[3:] == [f'{prefix} {i}' for i in range(len(header)-3)], 'unsupported retention table header')
    index = config['period_index'] + 3
    require(index < len(header), 'requested period missing from header')
    data, seen = [], set()
    for row in rows[8:]:
        require(3 <= len(row) <= len(header), 'invalid retention row width')
        if row[1] == 'Overall':
            require(row[2] in ('Retained', 'Retained %'), 'unsupported Overall row')
            continue
        try:
            d = datetime.strptime(row[1], '%b %d, %Y').date()
        except ValueError:
            raise ValueError(f'invalid cohort date: {row[1]!r}')
        key = (row[0], d)
        require(key not in seen, 'duplicate cohort date and segment')
        seen.add(key)
        users = number(row[2], 'users', True)
        returned = number(row[index], 'retained', True) if len(row) > index and row[index].strip() else None
        require(returned is None or returned <= users, 'retained exceeds users')
        data.append((row[0], d, users, returned))
    require(data, 'no cohort rows')
    return data


def evaluation(path):
    fields = ['start_date','end_date','route_id','model_id','absolute_error_sum','mae_count','brier_sum','brier_count']
    rows = table(path, fields)
    for r in rows:
        r['start_date'], r['end_date'] = day(r['start_date']), day(r['end_date'])
        require(r['start_date'] <= r['end_date'], 'invalid evaluation period')
        for name in fields[4:]: r[name] = number(r[name], name, name.endswith('count'))
        require((r['mae_count'] or r['absolute_error_sum'] == 0) and (r['brier_count'] or r['brier_sum'] == 0), 'sum with zero denominator')
        require(r['brier_sum'] <= r['brier_count'], 'Brier sum exceeds count')
    for i, a in enumerate(rows):
        for b in rows[i+1:]:
            require(not (a['route_id'] == b['route_id'] and a['model_id'] == b['model_id'] and a['start_date'] <= b['end_date'] and b['start_date'] <= a['end_date']), 'overlapping evaluation periods')
    return rows


def mappings(path):
    rows = table(path, ['external_route_id','external_model_id','route_id','model_id','start_date','end_date'])
    for r in rows:
        r['start_date'], r['end_date'] = day(r['start_date']), day(r['end_date'])
        require(r['start_date'] <= r['end_date'], 'invalid mapping period')
    for i, a in enumerate(rows):
        for b in rows[i+1:]:
            overlap = a['start_date'] <= b['end_date'] and b['start_date'] <= a['end_date']
            external = (a['external_route_id'], a['external_model_id']) == (b['external_route_id'], b['external_model_id'])
            internal = (a['route_id'], a['model_id']) == (b['route_id'], b['model_id'])
            require(not (overlap and (external or internal)), 'ambiguous or duplicate mapping')
    return rows


def ratio(n, d):
    return '없음 (분모 0)' if not d else f'{n / d:.4f} ({n:g} / {d:g})'


def run(args):
    config = json.loads(Path(args.contract).read_text(encoding='utf-8'))
    required = ['question','synthetic','metric','start_event','return_event','period_index','granularity','window','timezone','identity','filters','as_of','source','observation_rule']
    require(all(k in config and config[k] not in ('', None) for k in required), 'contract missing required fields; use "unknown" for unverified settings')
    require(type(config['synthetic']) is bool and type(config['period_index']) is int and config['period_index'] >= 0, 'invalid contract types')
    as_of = day(config['as_of'])
    cohorts = retention(args.retention, config)
    start, end = day(args.start), day(args.end)
    require(start <= end, 'invalid analysis period')
    selected = [r for r in cohorts if start <= r[1] <= end]
    require(selected, 'no cohorts in analysis period')
    if config['granularity'] == 'weekly':
        dates = sorted(r[1] for r in selected)
        require(all((b-a).days >= 7 for a,b in zip(dates, dates[1:])), 'overlapping weekly cohorts')
    require(len({r[0] for r in selected}) == 1, 'selecting multiple segments is unsupported')
    require(config['granularity'] in ('daily', 'weekly', 'unknown'), 'unsupported granularity')
    if config['granularity'] == 'daily':
        require(len(selected) == (end-start).days+1, 'missing cohort dates')
    known = [r for r in selected if r[3] is not None]
    complete = (config['metric'] == 'N-Day Retention' and config['granularity'] == 'daily' and config['window'] == '24h' and config['timezone'] != 'unknown' and config['observation_rule'] == 'conservative_next_day' and all(d + timedelta(days=config['period_index']+1) <= as_of for _,d,_,_ in selected) and len(known) == len(selected))
    # Exclude incomplete cells rather than silently treating them as zero.
    numerator, denominator = sum(r[3] for r in known), sum(r[2] for r in known)
    ev = evaluation(args.evaluation) if args.evaluation else []
    maps = mappings(args.mapping) if args.mapping else []
    lines = [('# 합성 자료 보고서' if config['synthetic'] else '# 분석 보고서'), '', f"질문: {config['question']}", f"출처: {config['source']} / 파일: {args.retention} / 기준일: {as_of}", f"정의: {config['metric']}, {config['start_event']} → {config['return_event']}, {config['granularity']}, {config['window']}, {config['timezone']}, {config['period_index']} 구간", f"필터: {config['filters']} / identity: {config['identity']} / 관찰 규칙: {config['observation_rule']}", f'시작 집단 기간: {start} ~ {end}; 집계 단위: 일별 사용자-시작일 합계' if config['granularity'] == 'daily' else f'시작 집단 기간: {start} ~ {end}; 집계 단위: 시작 집단별 사용자 합계 (기간 고유 사용자 아님)', f'관찰 완료 확인: {"완료" if complete else "미확인 또는 미완료; 확정 D7 아님"}', f'값이 있는 집단: {len(known)}/{len(selected)}; 재방문율: {ratio(numerator, denominator)}', '빈 셀은 제외했으며 0으로 처리하지 않았습니다. Overall은 다른 집계 단위이므로 합산하지 않았습니다.']
    if ev:
        in_period = [r for r in ev if start <= r['start_date'] and r['end_date'] <= end]
        omitted = len(ev)-len(in_period)
        lines += [f"평가 출처: {args.evaluation}; 기간 안에 완전히 포함된 행 {len(in_period)}개, 제외 {omitted}개", '좌석 MAE: '+ratio(sum(r['absolute_error_sum'] for r in in_period), sum(r['mae_count'] for r in in_period)), '만석 Brier: '+ratio(sum(r['brier_sum'] for r in in_period), sum(r['brier_count'] for r in in_period))]
        matched = sum(any(m['route_id'] == r['route_id'] and m['model_id'] == r['model_id'] and m['start_date'] <= r['start_date'] and m['end_date'] >= r['end_date'] for m in maps) for r in in_period)
        lines.append(f'ID 매핑: 평가 행 {matched}/{len(in_period)}개에 유효한 매핑; 미매핑 {len(in_period)-matched}개. 매핑은 기간 전체를 덮어야 합니다.')
    if args.groups:
        require(args.evaluation and args.mapping, 'group connection requires evaluation and mapping')
        require(complete and config.get('group_unit') == 'user-start-day' and config.get('group_definition') == 'same-as-retention', 'group connection requires completed observation and matching user-start-day definition')
        groups = table(args.groups, ['start_date','end_date','external_route_id','external_model_id','users','retained'])
        used = set()
        for g in groups:
            a, b = day(g['start_date']), day(g['end_date'])
            require(start <= a <= b <= end, 'group period outside analysis period')
            users, returned = number(g['users'], 'users', True), number(g['retained'], 'retained', True)
            require(returned <= users, 'retained exceeds users')
            matches = [m for m in maps if m['external_route_id'] == g['external_route_id'] and m['external_model_id'] == g['external_model_id'] and m['start_date'] <= a and m['end_date'] >= b]
            require(len(matches) <= 1, 'ambiguous group mapping')
            key = (a,b,g['external_route_id'],g['external_model_id'])
            require(all(not (x[2:] == key[2:] and x[0] <= b and a <= x[1]) for x in used), 'overlapping group periods')
            used.add(key)
            matches_ev = [r for r in in_period if matches and r['route_id'] == matches[0]['route_id'] and r['model_id'] == matches[0]['model_id'] and r['start_date'] == a and r['end_date'] == b]
            require(len(matches_ev) <= 1, 'ambiguous evaluation group')
            label = f"{a} ~ {b}, {g['external_route_id']}, {g['external_model_id']}"
            if not matches_ev:
                lines.append(f'집단 {label}: 연결 누락 (매핑 또는 동일 기간 평가 없음)')
            else:
                r = matches_ev[0]
                lines.append(f"집단 {label}: 재방문 {ratio(returned, users)}, MAE {ratio(r['absolute_error_sum'], r['mae_count'])}, Brier {ratio(r['brier_sum'], r['brier_count'])}")
        lines.append('집단 연결은 제공된 동일 기간·노선·모델 집계에 한정하며 사용자별 노출을 의미하지 않습니다.')
    lines += ['연결 범위: 이 Amplitude 표에는 노선·모델별 집단 키가 없어 평가와 결합할 수 없습니다. 전역 리텐션을 각 집단에 복제하지 않았습니다.', '확인 사실: 위 값은 제공된 파일의 집계입니다. 원인은 확인하지 못했습니다.', '한계: CSV는 화면의 관찰 미완료 표시를 보존하지 않습니다. 사용자별 예보 노출 키가 없어 개인 경험이나 인과관계를 판단할 수 없습니다. 평가의 상세·compact·archive 포함 범위와 운영 수집 여부는 입력 제공자가 확인해야 합니다.', '추가 자료: 검증된 시간대와 24시간 기준, identity 및 필터 설정, 관찰 완료 정보, 같은 기간·집계 수준의 노선/모델별 리텐션과 유효기간별 ID 매핑, 평가 누락·제외 현황.']
    require(Path(args.output).resolve() not in {Path(p).resolve() for p in (args.retention, args.contract, args.evaluation, args.mapping, args.groups) if p}, 'output must not overwrite input')
    Path(args.output).write_text('\n\n'.join(lines)+'\n', encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('retention','contract','start','end','output'): p.add_argument('--'+name, required=True)
    p.add_argument('--evaluation'); p.add_argument('--mapping'); p.add_argument('--groups')
    args = p.parse_args()
    try: run(args)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as e: p.exit(2, f'입력 오류: {e}\n')

if __name__ == '__main__': main()
