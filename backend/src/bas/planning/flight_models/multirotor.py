"""Constant airspeed, uniform wind; not a calibrated power model."""
import math


def duration(a,b,airspeed,wind_speed,wind_from):
    distance=math.dist(a,b)
    if distance<1e-6: return 0.
    ux,uy=(b[0]-a[0])/distance,(b[1]-a[1])/distance
    direction=math.radians(wind_from)
    wx,wy=-wind_speed*math.sin(direction),-wind_speed*math.cos(direction)
    along,cross=wx*ux+wy*uy,wx*(-uy)+wy*ux
    if abs(cross)>=airspeed: return math.inf
    ground=math.sqrt(airspeed**2-cross**2)+along
    return distance/ground if ground>.5 else math.inf


def route_time(points,settings,weather):
    return sum(duration(a,b,settings.airspeed_mps,weather.wind_speed_mps,weather.wind_from_deg) for a,b in zip(points,points[1:]))
