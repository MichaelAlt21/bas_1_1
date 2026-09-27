"""Strip candidates with an explicit rectangular footprint approximation."""
import math
from shapely.affinity import rotate
from shapely.geometry import LineString,box
from bas.planning.geometry import reject

MAX_STRIPS = 400
# Clipped strip ends are pulled back from the blocker boundary so they stay strictly in free space.
CUT_INSET_M = 0.05
MIN_PIECE_M = 1.0


def _pieces(line,free,area):
    """Parts of a row that stay in free space, in flight order; each is still over the area."""
    if free.covers(line): return [line]
    (x0,y0),(x1,y1)=line.coords
    length=line.length; ux,uy=(x1-x0)/length,(y1-y0)/length
    spans=[]
    for part in getattr(line.intersection(free),'geoms',[line.intersection(free)]):
        if part.geom_type!='LineString' or part.length<MIN_PIECE_M+2*CUT_INSET_M: continue
        s=sorted(((px-x0)*ux+(py-y0)*uy) for px,py in (part.coords[0],part.coords[-1]))
        a=s[0]+(CUT_INSET_M if s[0]>1e-6 else 0); b=s[1]-(CUT_INSET_M if s[1]<length-1e-6 else 0)
        piece=LineString([(x0+ux*a,y0+uy*a),(x0+ux*b,y0+uy*b)])
        if free.covers(piece) and piece.intersection(area).length>=MIN_PIECE_M: spans.append((a,piece))
    return [piece for _,piece in sorted(spans,key=lambda item:item[0])]


def split_long(lines,max_length):
    """Cut strips longer than max_length into equal consecutive pieces in flight order."""
    pieces=[]
    for line in lines:
        count=max(1,math.ceil(line.length/max_length))
        if count==1: pieces.append(line); continue
        (x0,y0),(x1,y1)=line.coords
        pieces.extend(LineString([(x0+(x1-x0)*k/count,y0+(y1-y0)*k/count),(x0+(x1-x0)*(k+1)/count,y0+(y1-y0)*(k+1)/count)]) for k in range(count))
    if len(pieces)>MAX_STRIPS: reject('TOO_MANY_STRIPS',f'Более {MAX_STRIPS} галсов после разбиения длинных: уменьшите район или измените параметры съемки.')
    return pieces


def generate(area,width,overlap,angle,free=None):
    """Serpentine rows across each area part; with `free`, rows are cut where they would cross a blocker."""
    if area.geom_type == 'MultiPolygon':
        lines=[]
        for component in sorted(area.geoms,key=lambda p:p.bounds):
            lines.extend(generate(component,width,overlap,angle,free))
            if len(lines)>MAX_STRIPS: reject('TOO_MANY_STRIPS',f'Более {MAX_STRIPS} галсов суммарно по зонам: уменьшите район или измените параметры съемки.')
        return lines
    rotated=rotate(area,-angle,origin=(0,0))
    x0,y0,x1,y1=rotated.bounds
    count=max(1,math.ceil((y1-y0)/(width*(1-overlap/100))))
    if count>MAX_STRIPS: reject('TOO_MANY_STRIPS',f'Более {MAX_STRIPS} галсов: уменьшите район или измените параметры съемки.')
    step=(y1-y0)/count; lines=[]
    for row in range(count):
        y=y0+(row+.5)*step
        band=rotated.intersection(box(x0-1,y-step/2,x1+1,y+step/2))
        if band.is_empty: continue
        left,_,right,_=band.bounds
        if right-left<.01: continue
        coords=[(left,y),(right,y)]
        if row%2: coords.reverse()
        line=rotate(LineString(coords),angle,origin=(0,0))
        lines.extend([line] if free is None else _pieces(line,free,area))
    return lines
