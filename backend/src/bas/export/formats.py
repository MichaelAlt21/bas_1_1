"""GIS interchange of demonstration missions; not an autopilot command format."""
import json
from xml.etree.ElementTree import Element, SubElement, tostring


def geojson(plan):
    features=[]
    for mission in plan['missions']:
        base={k:v for k,v in mission.items() if k!='legs'}
        for leg in mission['legs']:
            properties={**base,**{k:v for k,v in leg.items() if k!='coordinates'},'demonstration_only':True}
            features.append(dict(type='Feature',geometry=dict(type='LineString',coordinates=leg['coordinates']),properties=properties))
        for kind,leg,index in [('start',mission['legs'][0],0),('landing',mission['legs'][-1],-1)]:
            features.append(dict(type='Feature',geometry=dict(type='Point',coordinates=leg['coordinates'][index]),
                properties=dict(aircraft_id=mission['aircraft_id'],sortie=mission.get('sortie',1),kind=kind,altitude_reference='AGL',altitude_m=0,demonstration_only=True)))
    return dict(type='FeatureCollection',features=features,plan_id=plan['id'],scenario_id=plan['scenario_id'],warnings=plan['warnings'],metrics=plan['metrics'],weather=plan.get('weather'),aircraft_profiles=plan.get('aircraft_profiles'))


def kml(plan):
    root=Element('kml',xmlns='http://www.opengis.net/kml/2.2')
    doc=SubElement(root,'Document')
    SubElement(doc,'name').text='KUMOsystems — демонстрационный план'
    SubElement(doc,'description').text='\n'.join(plan['warnings'])
    metadata = SubElement(doc, 'ExtendedData')
    for key in ('weather', 'aircraft_profiles', 'metrics'):
        SubElement(SubElement(metadata, 'Data', name=key), 'value').text=json.dumps(plan.get(key), ensure_ascii=False)
    for feature in geojson(plan)['features']:
        props=feature['properties']; geometry=feature['geometry']
        mark=SubElement(doc,'Placemark')
        flight=props['aircraft_id']+(f" · рейс {props['sortie']}" if props.get('sortie',1)>1 else '')
        extra=f" / {props['requirement_id']} · {props.get('sensor_name','')}" if props.get('requirement_id') and props.get('phase')=='survey' else ''
        SubElement(mark,'name').text=f"{flight} / {props.get('phase',props.get('kind'))} / {props.get('sequence','')}{extra}"
        ext=SubElement(mark,'ExtendedData')
        for key,value in props.items():
            SubElement(SubElement(ext,'Data',name=key),'value').text=json.dumps(value,ensure_ascii=False)
        geom=SubElement(mark,geometry['type'])
        SubElement(geom,'altitudeMode').text='relativeToGround'
        coords=geometry['coordinates']
        if geometry['type']=='Point':
            text=f'{coords[0]},{coords[1]},0'
        else:
            text=' '.join(f"{p[0]},{p[1]},{props['altitude_start_m'] if i==0 else props['altitude_end_m']}" for i,p in enumerate(coords))
        SubElement(geom,'coordinates').text=text
    return tostring(root,encoding='utf-8',xml_declaration=True)


def zones_kml(zones):
    """Restriction zones in the KUMO exchange notation, readable back by the zone import.

    Vertical limits use GND, UNL, FL100, "120 AGL", "400 AMSL"; time windows are
    `periods` (start/end;…) and `weekly` ("MON-FRI 08:00-18:00; SAT 10:00-14:00").
    """
    from bas.imports.altitudes import format_compact_limit
    root=Element('kml',xmlns='http://www.opengis.net/kml/2.2')
    doc=SubElement(root,'Document')
    SubElement(doc,'name').text='KUMOsystems — зоны ограничений'
    for zone in zones:
        mark=SubElement(doc,'Placemark')
        SubElement(mark,'name').text=zone.name or zone.id
        validity=zone.validity
        values={'zone_id':zone.id,'category':zone.category,'lower':format_compact_limit(zone.lower),'upper':format_compact_limit(zone.upper),
                'validity':validity.kind,'timezone':validity.timezone,
                'periods':';'.join(f'{p.start.isoformat()}/{p.end.isoformat()}' for p in validity.periods),
                'weekly':'; '.join(f"{','.join(w.days)} {w.start.strftime('%H:%M')}-{w.end.strftime('%H:%M')}" for w in validity.weekly),
                'notes':zone.notes,'source_text':zone.source_text,'parsed':'' if zone.parsed else 'false'}
        ext=SubElement(mark,'ExtendedData')
        for key,value in values.items():
            if value: SubElement(SubElement(ext,'Data',name=key),'value').text=value
        polygon=SubElement(mark,'Polygon')
        for index,ring in enumerate(zone.geometry.coordinates):
            boundary=SubElement(polygon,'outerBoundaryIs' if index==0 else 'innerBoundaryIs')
            SubElement(SubElement(boundary,'LinearRing'),'coordinates').text=' '.join(f'{x},{y}' for x,y in ring)
    return tostring(root,encoding='utf-8',xml_declaration=True)
