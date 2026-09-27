"""Shortest turn-limited paths at constant airspeed and bank in a steady wind.

The aircraft turns on circles of the given radius relative to the air mass, the way an autopilot
holds a constant bank. The air mass drifts with the wind, so over the ground those turns become
trochoids. The path is found with the virtual-target method: the Dubins path in the air frame
must end where the target will be, relative to the air, at the arrival time T, and T is the
path length divided by the airspeed.

All six Dubins words are tried: circle-straight-circle (LSL, RSR, LSR, RSL) and the loops
circle-circle-circle (RLR, LRL), which are shorter when the target is close, e.g. the next strip.
Poses are (x, y, heading) with the heading in radians counterclockwise from east.
"""
import math

TAU = 2 * math.pi
WORDS = ('LSL', 'RSR', 'LSR', 'RSL', 'RLR', 'LRL')
# Chord sagitta of sampled turns; the same angular step bounds the ground-track error in wind.
ARC_ERROR_M = 0.2


def _mod(angle):
    angle = math.fmod(angle, TAU)
    return angle + TAU if angle < 0 else angle


def _frame(start, finish, radius):
    """Normalised distance and end headings relative to the line between the poses."""
    dx, dy = finish[0] - start[0], finish[1] - start[1]
    d = math.hypot(dx, dy) / radius
    theta = _mod(math.atan2(dy, dx)) if d > 0 else 0.
    a, b = _mod(start[2] - theta), _mod(finish[2] - theta)
    return d, a, b, math.sin(a), math.sin(b), math.cos(a), math.cos(b), math.cos(a - b)


def _word(word, frame):
    """(t, p, q) of one word: arcs in radians and the straight part in radii; None if it does not exist."""
    d, a, b, sa, sb, ca, cb, cab = frame
    dd = d * d
    if word == 'LSL':
        square = 2 + dd - 2 * cab + 2 * d * (sa - sb)
        if square < 0: return None
        turn = math.atan2(cb - ca, d + sa - sb)
        return _mod(turn - a), math.sqrt(square), _mod(b - turn)
    if word == 'RSR':
        square = 2 + dd - 2 * cab + 2 * d * (sb - sa)
        if square < 0: return None
        turn = math.atan2(ca - cb, d - sa + sb)
        return _mod(a - turn), math.sqrt(square), _mod(turn - b)
    if word == 'LSR':
        square = -2 + dd + 2 * cab + 2 * d * (sa + sb)
        if square < 0: return None
        p = math.sqrt(square)
        turn = math.atan2(-ca - cb, d + sa + sb) - math.atan2(-2., p)
        return _mod(turn - a), p, _mod(turn - b)
    if word == 'RSL':
        square = -2 + dd + 2 * cab - 2 * d * (sa + sb)
        if square < 0: return None
        p = math.sqrt(square)
        turn = math.atan2(ca + cb, d - sa - sb) - math.atan2(2., p)
        return _mod(a - turn), p, _mod(b - turn)
    if word == 'RLR':
        cosine = (6 - dd + 2 * cab + 2 * d * (sa - sb)) / 8
        if abs(cosine) > 1: return None
        p = _mod(TAU - math.acos(cosine))
        t = _mod(a - math.atan2(ca - cb, d - sa + sb) + p / 2)
        return t, p, _mod(a - b - t + p)
    cosine = (6 - dd + 2 * cab + 2 * d * (sb - sa)) / 8
    if abs(cosine) > 1: return None
    p = _mod(TAU - math.acos(cosine))
    t = _mod(-a - math.atan2(ca - cb, d + sa - sb) + p / 2)
    return t, p, _mod(b - a - t + p)


def words(start, finish, radius):
    """{word: (t, p, q)} for every word that exists between the poses."""
    frame = _frame(start, finish, radius)
    found = {word: _word(word, frame) for word in WORDS}
    return {word: value for word, value in found.items() if value is not None}


def _advance(pose, kind, amount, radius):
    """Pose after `amount` (radians for turns, metres for straights) of one segment."""
    x, y, h = pose
    if kind == 'L':
        return (x + radius * (math.sin(h + amount) - math.sin(h)), y + radius * (math.cos(h) - math.cos(h + amount)), h + amount)
    if kind == 'R':
        return (x + radius * (math.sin(h) - math.sin(h - amount)), y + radius * (math.cos(h - amount) - math.cos(h)), h - amount)
    return (x + amount * math.cos(h), y + amount * math.sin(h), h)


def wind_vector(weather):
    """Velocity of the air mass: `wind_from_deg` is where the wind blows from, clockwise from north."""
    direction = math.radians(weather.wind_from_deg)
    return (-weather.wind_speed_mps * math.sin(direction), -weather.wind_speed_mps * math.cos(direction))


def air_heading(track, wind, speed):
    """Heading that keeps the ground track `track` (the crab angle applied); None if the wind wins."""
    ux, uy = math.cos(track), math.sin(track)
    cross = -wind[0] * uy + wind[1] * ux
    if abs(cross) >= speed:
        return None
    heading = track - math.asin(cross / speed)
    ground = speed * math.cos(heading - track) + wind[0] * ux + wind[1] * uy
    return heading if ground > .5 else None


def crab_angle(track, wind, speed):
    """Angle between heading and ground track, radians; None when the track cannot be flown."""
    heading = air_heading(track, wind, speed)
    return None if heading is None else heading - track


def solve(start, finish, radius, speed, wind, word):
    """Arrival time and segment lengths of `word` from air pose `start` to the ground point of `finish`.

    `start` and `finish` carry air headings. The target moves in the air frame at minus the wind,
    so T = L(finish - wind * T) / speed is iterated; the map contracts while the wind is slower
    than the aircraft. None when the word does not exist or the iteration does not settle.
    """
    x0, y0 = start[0], start[1]
    t_now = math.hypot(finish[0] - x0, finish[1] - y0) / speed
    for _ in range(60):
        target = (finish[0] - wind[0] * t_now, finish[1] - wind[1] * t_now, finish[2])
        segments = _word(word, _frame(start, target, radius))
        if segments is None:
            return None
        t_next = radius * sum(segments) / speed
        if abs(t_next - t_now) <= 1e-9 * max(1., t_next):
            return t_next, segments
        t_now = t_next
    return None


def sample(start, word, segments, radius, speed, wind):
    """Ground-track parts of a solved path: [('turn'|'transit', [(x, y, t), ...]), ...]."""
    step = min(math.radians(5), 2 * math.acos(1 - ARC_ERROR_M / radius))
    pose, travelled, parts = (start[0], start[1], start[2]), 0., []
    for kind, amount in zip(word, segments):
        if amount <= 1e-9:
            continue
        length = amount * radius
        count = 1 if kind == 'S' else max(1, math.ceil(amount / step))
        points = []
        for i in range(count + 1):
            share = amount * i / count if kind != 'S' else length * i / count
            x, y, _ = _advance(pose, kind, share, radius)
            t = (travelled + (radius * share if kind != 'S' else share)) / speed
            points.append((x + wind[0] * t, y + wind[1] * t, t))
        parts.append(('transit' if kind == 'S' else 'turn', points))
        pose = _advance(pose, kind, amount if kind != 'S' else length, radius)
        travelled += length
    return parts


def fastest(start, finish, radius, speed, wind):
    """Arrival time of the fastest word between ground-track poses, without sampling; inf if none.

    Iterates on the shortest word at each step: every word's time map decreases through its own
    fixed point, so the fixed point of their minimum is the earliest arrival. When the minimum
    jumps between words and does not settle, each word is solved on its own.
    """
    heading0, heading1 = air_heading(start[2], wind, speed), air_heading(finish[2], wind, speed)
    if heading0 is None or heading1 is None:
        return math.inf
    origin = (start[0], start[1], heading0)
    t_now = math.hypot(finish[0] - start[0], finish[1] - start[1]) / speed
    for _ in range(40):
        frame = _frame(origin, (finish[0] - wind[0] * t_now, finish[1] - wind[1] * t_now, heading1), radius)
        lengths = [sum(found) for found in (_word(word, frame) for word in WORDS) if found is not None]
        t_next = radius * min(lengths) / speed
        # An estimate for ranking: a millisecond is plenty.
        if abs(t_next - t_now) <= 1e-3:
            return t_next
        t_now = t_next
    best = math.inf
    for word in WORDS:
        solved = solve(origin, (finish[0], finish[1], heading1), radius, speed, wind, word)
        if solved is not None and solved[0] < best:
            best = solved[0]
    return best


def paths(start, finish, radius, speed, wind):
    """Every feasible word from ground-track pose `start` to ground-track pose `finish` in the wind.

    Yields (arrival time, parts) with parts as in `sample`, pinned exactly to both end points.
    """
    heading0, heading1 = air_heading(start[2], wind, speed), air_heading(finish[2], wind, speed)
    if heading0 is None or heading1 is None:
        return
    for word in WORDS:
        solved = solve((start[0], start[1], heading0), (finish[0], finish[1], heading1), radius, speed, wind, word)
        if solved is None:
            continue
        arrival, segments = solved
        parts = sample((start[0], start[1], heading0), word, segments, radius, speed, wind)
        if not parts:
            continue
        end = parts[-1][1][-1]
        if math.hypot(end[0] - finish[0], end[1] - finish[1]) > 1e-3:
            continue
        parts[0][1][0] = (start[0], start[1], 0.)
        parts[-1][1][-1] = (finish[0], finish[1], arrival)
        yield arrival, parts


def loiter(start, radius, speed, wind, direction, seconds):
    """Constant-bank circling from a ground-track pose for `seconds`: [(x, y, t)] and the final track."""
    heading = air_heading(start[2], wind, speed)
    if heading is None:
        return None
    angle = speed * seconds / radius
    kind = 'L' if direction > 0 else 'R'
    parts = sample((start[0], start[1], heading), kind, (angle,), radius, speed, wind)
    points = parts[0][1]
    final = heading + direction * angle
    vx, vy = speed * math.cos(final) + wind[0], speed * math.sin(final) + wind[1]
    return points, math.atan2(vy, vx)
