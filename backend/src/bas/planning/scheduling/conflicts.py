"""Continuous separation of timed legs, including climb/descent columns.

Horizontal only (the historical rule): legs closer than `separation` at any common moment clash,
whatever their heights. With `vertical`, a pair also counts as separated while their heights differ
by at least `vertical`; heights change linearly within a leg, so the check stays exact in time.
"""
import math


def first_conflict(first,second,separation=30.,vertical=None):
    """The first pair of legs (from first, from second) that come closer than separation, or None."""
    # Mission legs are ordered and do not overlap within one aircraft. A sweep
    # avoids quadratic work when sampled aircraft turns add many short segments.
    j = 0
    for a in first:
        while j < len(second) and second[j]['end'] <= a['start']:
            j += 1
        k = j
        while k < len(second) and second[k]['start'] < a['end']:
            b = second[k]
            k += 1
            lo,hi=max(a['start'],b['start']),min(a['end'],b['end'])
            if hi<=lo: continue
            def motion(leg):
                p,q=leg['xy'][0],leg['xy'][-1]; dt=leg['end']-leg['start']
                v=((q[0]-p[0])/dt,(q[1]-p[1])/dt)
                return (p[0]+v[0]*(lo-leg['start']),p[1]+v[1]*(lo-leg['start'])),v
            pa,va=motion(a); pb,vb=motion(b)
            r=(pa[0]-pb[0],pa[1]-pb[1]); v=(va[0]-vb[0],va[1]-vb[1])
            first_t,last_t=0.,hi-lo
            if vertical is not None:
                # Keep only the part of the common interval where the heights are closer than `vertical`.
                dz=height(a,lo)-height(b,lo); vz=climb(a)-climb(b)
                if abs(vz)<1e-12:
                    if abs(dz)>=vertical: continue
                else:
                    e0,e1=sorted(((-vertical-dz)/vz,(vertical-dz)/vz))
                    first_t,last_t=max(first_t,e0),min(last_t,e1)
                    if last_t<=first_t: continue
            vv=v[0]**2+v[1]**2
            t=max(first_t,min(last_t,-(r[0]*v[0]+r[1]*v[1])/vv)) if vv else first_t
            if math.hypot(r[0]+t*v[0],r[1]+t*v[1])<separation: return a,b
    return None


def climb(leg):
    return (leg['altitude_end_m']-leg['altitude_start_m'])/(leg['end']-leg['start'])


def height(leg,t):
    return leg['altitude_start_m']+climb(leg)*(t-leg['start'])


def conflict(first,second,separation=30.,vertical=None):
    return first_conflict(first,second,separation,vertical) is not None


def minimal_shift(a,b,separation,vertical):
    """Smallest delay of leg `a` that clears leg `b`.

    Both move linearly, so the delays at which they clash form one interval (the clashing
    moments are a convex set in time and delay); it contains zero and ends before `a` would
    start after `b` ends. Bisection finds its end to within 10 ms.
    """
    low,high=0.,max(b['end']-a['start'],.01)
    while high-low>.01:
        middle=(low+high)/2
        if first_conflict([dict(a,start=a['start']+middle,end=a['end']+middle)],[b],separation,vertical): low=middle
        else: high=middle
    return high


def _charger_free(bookings,capacity,start,length):
    """Earliest time from `start` at which one of `capacity` chargers stays free for `length`."""
    for begin in sorted({start,*(end for _,end in bookings if end>start)}):
        points=[begin,*(b for b,_ in bookings if begin<b<begin+length)]
        if all(sum(1 for b,e in bookings if b<=p<e)<capacity for p in points):
            return begin
    return max([start,*(end for _,end in bookings)])


def schedule(missions,turnaround_s=0.,chargers=None,handling_s=0.,vertical=None):
    """Delay each mission on the ground until it clears every mission scheduled before it.

    A clash moves the start just far enough for the clashing leg to begin after the other
    aircraft's leg ends; the check repeats until the whole mission is clear. Aircraft sharing a
    pad therefore launch one after another instead of waiting for whole flights to finish.

    A repeat sortie (`after` = index of the aircraft's previous mission) starts no sooner than
    that landing plus its ground time: the battery swap (`turnaround_s`) or, with `chargers`
    ({site id: count}), handling and then charging on a charger of the landing site. Chargers
    take one battery at a time; an aircraft waits for a free one. Missions are taken in the order
    they become ready, so the charger queue is first come, first served; with no repeat sorties
    that is the input order. The result keeps the input order. `vertical` enables separation by
    height (see first_conflict) and the minimal delay that clears a clash.
    """
    done=[None]*len(missions)
    bookings={}
    remaining=list(range(len(missions)))
    while remaining:
        ready=[]
        for k in remaining:
            after=missions[k].get('after')
            if after is None: ready.append((0.,k))
            elif done[after] is not None: ready.append((done[after]['finish_s'],k))
        landed,k=min(ready)
        remaining.remove(k)
        mission=missions[k]
        extra={}
        if mission.get('after') is None:
            offset=0.
        elif chargers is not None and mission.get('charge_site_id') is not None:
            site=mission['charge_site_id']; length=mission['charge_s']
            queue=bookings.setdefault(site,[])
            begin=_charger_free(queue,max(chargers.get(site,0),1),landed+handling_s,length)
            queue.append((begin,begin+length))
            offset=begin+length
            extra=dict(charge_start_s=begin,charge_end_s=begin+length,charge_wait_s=begin-landed-handling_s)
        else:
            offset=landed+mission.get('ground_s',turnaround_s)
        scheduled=[m for m in done if m is not None]
        while True:
            shifted=[dict(leg,start=leg['start']+offset,end=leg['end']+offset) for leg in mission['legs']]
            clash=next((pair for m in scheduled for pair in [first_conflict(shifted,m['legs'],vertical=vertical)] if pair),None)
            if clash is None: break
            a,b=clash
            # The historical rule waits for the other leg to end; with separation by height the
            # delay is just long enough, so aircraft can climb out of a shared pad one above another.
            offset+=max(b['end']-a['start'],1.) if vertical is None else minimal_shift(a,b,30.,vertical)
        entry=dict(mission,legs=shifted,start_s=offset,finish_s=shifted[-1]['end'],**extra)
        entry.pop('after',None)
        done[k]=entry
    return done
