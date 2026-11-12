#!/usr/bin/env python3
"""Rebuild the data behind redstonegis.com/FireCost from the live WFIGS services.

Writes fires.json, asof.json and data/*.json into the output folder given on the command line.
Fires: 2026 wildfires with a reported cost above zero or 500 acres or more.
Stops with an error, writing nothing useful, if the services return too little to trust.
"""
import json, math, os, shutil, sys, time, urllib.parse, urllib.request
import shapely
from shapely.geometry import mapping, shape

OUT = sys.argv[1] if len(sys.argv) > 1 else 'out'
BASE = 'https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/'
INCID = BASE + 'WFIGS_Incident_Locations/FeatureServer/0/query'
PERIM = BASE + 'WFIGS_Daily_Perimeters_Public/FeatureServer/0/query'
YEAR = 2026
DAY = 864e5
I_FIELDS = ('IncidentName,UniqueFireIdentifier,FireDiscoveryDateTime,ContainmentDateTime,FireOutDateTime,IncidentSize,DiscoveryAcres,'
            'EstimatedCostToDate,EstimatedFinalCost,POOState,POOCounty,POOLandownerCategory,POOLandownerKind,POOJurisdictionalAgency,'
            'FireCause,FireCauseGeneral,FireCauseSpecific,TotalIncidentPersonnel,IncidentManagementOrganization,IncidentComplexityLevel,'
            'IncidentShortDescription,CpxName,ModifiedOnDateTime_dt,PercentContained')
P_FIELDS = ('OBJECTID,attr_UniqueFireIdentifier,poly_FeatureCategory,poly_GISAcres,attr_ModifiedOnDateTime_dt,attr_EstimatedCostToDate,'
            'attr_PercentContained,attr_TotalIncidentPersonnel,attr_Fatalities')


def post(url, params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data, {'User-Agent': 'redstonegis-firecost-refresh'})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                out = json.load(r)
            if 'error' in out:
                raise RuntimeError(out['error'])
            return out
        except Exception as e:  # noqa: BLE001
            print('   retry', attempt + 1, str(e)[:160], flush=True)
            time.sleep(6 * (attempt + 1))
    raise SystemExit('WFIGS did not answer after repeated tries; leaving the published data as it is.')


def pages(url, where, fields, size, **extra):
    offset = 0
    while True:
        out = post(url, dict(where=where, outFields=fields, outSR=4326, f='geojson', orderByFields='OBJECTID',
                             returnGeometry='true', resultOffset=offset, resultRecordCount=size, **extra))
        feats = out.get('features', [])
        yield from feats
        more = out.get('exceededTransferLimit') or (out.get('properties') or {}).get('exceededTransferLimit')
        if not feats or not (more or len(feats) == size):
            return
        offset += len(feats)


def ms(v):
    """Epoch milliseconds, or None for missing or impossible dates (the source has a few typos such as year 5202)."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return v if 1.7e12 < v < 1.83e12 else None


def s(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def n(v, d=None):
    try:
        v = float(v)
        return d if math.isnan(v) else v
    except (TypeError, ValueError):
        return d


def rnd(c):
    return [rnd(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], 5), round(c[1], 5)]


def poly(geom):
    """A valid polygon geometry, or None."""
    if not geom:
        return None
    g = shape(geom)
    if not g.is_valid:
        g = shapely.make_valid(g)
    if g.geom_type == 'GeometryCollection':
        parts = [x for x in g.geoms if x.geom_type in ('Polygon', 'MultiPolygon')]
        g = shapely.unary_union(parts) if parts else None
    if g is None or g.is_empty or g.geom_type not in ('Polygon', 'MultiPolygon'):
        return None
    return g


def build_fire(f, recs):
    """Time steps, perimeters and active edges for one fire, from its perimeter records."""
    rows = []
    lat0 = math.radians(f['lat'])
    for p, g in recs:
        t = ms(p.get('attr_ModifiedOnDateTime_dt'))
        if t is None or g is None:
            continue
        a = n(p.get('poly_GISAcres'))
        if a is None or a <= 0:
            a = shapely.area(g) * (111320 ** 2) * math.cos(lat0) / 4046.8564
        rows.append((t, a, g, p))
    if not rows:
        return None
    rows.sort(key=lambda r: r[0])
    disc = f['disc'] or rows[0][0] - 3600000
    steps = [dict(t=disc, a=round(f['da'] or 0, 1), c=None, g=-1)]
    geoms, kept_a, cmax, last_t = [], 0, None, disc
    days = {}
    for r in rows:
        days.setdefault(int(r[0] // DAY), []).append(r)
    for day in sorted(days):
        grp = days[day]
        costs = [c for c in (n(r[3].get('attr_EstimatedCostToDate')) for r in grp) if c and c > 0]
        if costs:
            cmax = max(cmax or 0, max(costs))
        t, a, g, p = grp[-1]
        # keep the last perimeter of each UTC day, and only when it grew: shrinking records are remaps or errors
        if (a >= kept_a * 1.0005 or not geoms) and (a >= kept_a or not geoms):
            kept_a = a
            geoms.append(g)
        t = max(t, last_t + 3600000)
        last_t = t
        st = dict(t=t, a=round(kept_a, 1), c=cmax, g=len(geoms) - 1)
        pc, pe = n(p.get('attr_PercentContained')), n(p.get('attr_TotalIncidentPersonnel'))
        if pc is not None:
            st['pc'] = round(pc)
        if pe:
            st['p'] = int(pe)
        prev = steps[-1]
        if len(steps) > 1 and prev['g'] == st['g'] and prev['c'] == st['c'] and prev.get('p') == st.get('p') and prev.get('pc') == st.get('pc'):
            continue
        steps.append(st)
    if f['cost'] and steps[-1]['c'] != f['cost']:
        if f['mod'] and f['mod'] > steps[-1]['t'] + DAY / 2:
            st = dict(steps[-1])
            st.update(t=f['mod'], c=f['cost'], late=1)
            st.pop('p', None)
            if f['pc'] is not None:
                st['pc'] = round(f['pc'])
            steps.append(st)
        else:
            steps[-1]['c'] = f['cost']
    fin_m = math.sqrt(max(kept_a, 1) * 4046.86)
    tol = min(60, max(6, fin_m / 500)) / 111320
    fine = [shapely.simplify(g, tol / 3, preserve_topology=True) for g in geoms]
    dd = max(10 / 111320, tol)
    act = []
    # active edge: the part of each perimeter that is not on the previous perimeter
    for i in range(1, len(geoms)):
        try:
            a = shapely.difference(shapely.boundary(fine[i]), shapely.buffer(shapely.boundary(fine[i - 1]), dd, quad_segs=2))
            parts = [x for x in shapely.get_parts(a) if x.geom_type == 'LineString' and x.length >= 2 * dd]
        except Exception:  # noqa: BLE001
            parts = [x for x in shapely.get_parts(shapely.boundary(fine[i])) if x.geom_type in ('LineString', 'LinearRing')]
        if parts:
            m = mapping(shapely.simplify(shapely.multilinestrings([shapely.LineString(x.coords) for x in parts]), tol))
            act.append(dict(type='Feature', properties=dict(g=i), geometry=dict(type=m['type'], coordinates=rnd(m['coordinates']))))
    per, bb = [], None
    for i, g in enumerate(geoms):
        g2 = shapely.simplify(g, tol, preserve_topology=True)
        if not g2.is_valid:
            g2 = shapely.make_valid(g2)
        if g2.geom_type == 'GeometryCollection':
            g2 = shapely.unary_union([x for x in g2.geoms if x.geom_type in ('Polygon', 'MultiPolygon')])
        if g2.is_empty:
            g2 = g
        b = g.bounds
        bb = b if bb is None else (min(bb[0], b[0]), min(bb[1], b[1]), max(bb[2], b[2]), max(bb[3], b[3]))
        m = mapping(g2)
        per.append(dict(type='Feature', properties=dict(g=i), geometry=dict(type=m['type'], coordinates=rnd(m['coordinates']))))
    return dict(steps=steps, per=per, act=act, pt=[f['lon'], f['lat']], bbox=[round(x, 5) for x in bb])


def main():
    t0 = time.time()
    where = (f"IncidentTypeCategory='WF' AND FireDiscoveryDateTime >= TIMESTAMP '{YEAR}-01-01 00:00:00' "
             f"AND FireDiscoveryDateTime < TIMESTAMP '{YEAR + 1}-01-01 00:00:00' AND (EstimatedCostToDate > 0 OR IncidentSize >= 500)")
    fires, seen = [], set()
    for ft in pages(INCID, where, I_FIELDS, 1000):
        p, g = ft['properties'], ft.get('geometry')
        u = s(p.get('UniqueFireIdentifier'))
        if not u or u in seen or not g or g.get('type') != 'Point':
            continue
        seen.add(u)
        lon, lat = g['coordinates'][:2]
        fires.append(dict(id=u, name=s(p.get('IncidentName')), st=(s(p.get('POOState')) or '')[-2:], disc=ms(p.get('FireDiscoveryDateTime')),
                          lon=round(lon, 5), lat=round(lat, 5), po=1, ac=round(n(p.get('IncidentSize'), 0), 1),
                          cost=n(p.get('EstimatedCostToDate'), 0) or None, fin=n(p.get('EstimatedFinalCost'), 0) or None,
                          cnty=s(p.get('POOCounty')), own=s(p.get('POOLandownerCategory')), kind=s(p.get('POOLandownerKind')),
                          ag=s(p.get('POOJurisdictionalAgency')), c1=s(p.get('FireCause')), c2=s(p.get('FireCauseGeneral')),
                          c3=s(p.get('FireCauseSpecific')), cont=ms(p.get('ContainmentDateTime')), out=ms(p.get('FireOutDateTime')),
                          pers=int(n(p.get('TotalIncidentPersonnel'), 0)) or None, org=s(p.get('IncidentManagementOrganization')),
                          cx=s(p.get('IncidentComplexityLevel')), desc=s(p.get('IncidentShortDescription')), cpx=s(p.get('CpxName')),
                          mod=ms(p.get('ModifiedOnDateTime_dt')), da=n(p.get('DiscoveryAcres')), pc=n(p.get('PercentContained')), fat=None))
    total = sum(f['cost'] or 0 for f in fires)
    print(f'incidents {len(fires)}, cost ${total:,.0f}, {round(time.time() - t0)} s', flush=True)
    # the November 3, 2026 extract had 1,774 fires and $4.29 billion; far less than that means a bad answer, not a real change
    if len(fires) < 1500 or total < 3.5e9:
        raise SystemExit('Too few fires or too little cost came back; leaving the published data as it is.')
    fires.sort(key=lambda f: (-(f['cost'] or 0), -f['ac'], f['id']))

    raw, by_id = {}, {f['id']: f for f in fires}
    ids = [f['id'] for f in fires]
    nrec = 0
    for i in range(0, len(ids), 40):
        chunk = ids[i:i + 40]
        inlist = ','.join("'" + u.replace("'", "''") + "'" for u in chunk)
        recs = {}
        for ft in pages(PERIM, f'attr_UniqueFireIdentifier IN ({inlist})', P_FIELDS, 100, maxAllowableOffset=0.00004, geometryPrecision=6):
            p = ft['properties']
            u = p.get('attr_UniqueFireIdentifier')
            if u not in by_id or not str(p.get('poly_FeatureCategory') or '').startswith('Wildfire'):
                continue
            nrec += 1
            f = by_id[u]
            fat, pe = n(p.get('attr_Fatalities'), 0), n(p.get('attr_TotalIncidentPersonnel'), 0)
            if fat:
                f['fat'] = max(f['fat'] or 0, int(fat))
            if pe:
                f['pers'] = max(f['pers'] or 0, int(pe))
            recs.setdefault(u, []).append((p, poly(ft.get('geometry'))))
        for u, rs in recs.items():
            d = build_fire(by_id[u], rs)
            if d:
                raw[u] = d
        print(f'perimeters: fires {min(i + 40, len(ids))}/{len(ids)}, records {nrec}, mapped {len(raw)}, {round(time.time() - t0)} s', flush=True)
    if len(raw) < 800:
        raise SystemExit('Too few fires came back with perimeters; leaving the published data as it is.')

    data_dir = os.path.join(OUT, 'data')
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(data_dir)
    out, cur, size, nb = [], {}, 0, 0

    def flush():
        nonlocal cur, size, nb
        if cur:
            json.dump(cur, open(os.path.join(data_dir, f'b{nb}.json'), 'w'), separators=(',', ':'))
            nb += 1
            cur, size = {}, 0

    for f in fires:
        d = raw.get(f['id'])
        f = {k: v for k, v in f.items() if v is not None}
        if d:
            text = json.dumps(d, separators=(',', ':'))
            f['n'], f['ng'] = len(d['steps']), len(d['per'])
            if len(text) > 150e3:
                open(os.path.join(data_dir, f['id'] + '.json'), 'w').write(text)
                f['f'] = -1
            else:
                if size + len(text) > 700e3:
                    flush()
                cur[f['id']] = d
                size += len(text)
                f['f'] = nb
        out.append(f)
    flush()
    json.dump(out, open(os.path.join(OUT, 'fires.json'), 'w'), separators=(',', ':'))
    json.dump(dict(t=int(time.time() * 1000), fires=len(out), mapped=len(raw), cost=total), open(os.path.join(OUT, 'asof.json'), 'w'))
    mb = sum(os.path.getsize(os.path.join(data_dir, x)) for x in os.listdir(data_dir)) / 1e6
    top = out[0]
    print(f'DONE fires {len(out)}, mapped {len(raw)}, bundles {nb}, data files {len(os.listdir(data_dir))}, {mb:.1f} MB, '
          f'costliest {top["name"]} ${top.get("cost", 0):,.0f}, {round(time.time() - t0)} s')
    for u in ('2026-COSJF-000536', '2026-WAOWF-260406'):
        if u in raw:
            d = raw[u]
            print('  check', u, 'steps', len(d['steps']), 'perimeters', len(d['per']), 'active edges', len(d['act']), 'last', d['steps'][-1])


if __name__ == '__main__':
    main()
