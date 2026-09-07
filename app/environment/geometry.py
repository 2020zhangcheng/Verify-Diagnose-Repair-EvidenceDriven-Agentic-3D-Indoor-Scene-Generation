"""Small deterministic OBB primitives, independent of Planning and belief."""
from itertools import product
from math import sqrt


def dot(a, b):
    return sum(x*y for x,y in zip(a,b))


def sub(a,b):
    return tuple(x-y for x,y in zip(a,b))


def cross(a,b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def axes(pose):
    x,y,z,w = pose.orientation_xyzw
    return ((1-2*(y*y+z*z),2*(x*y+z*w),2*(x*z-y*w)),
            (2*(x*y-z*w),1-2*(x*x+z*z),2*(y*z+x*w)),
            (2*(x*z+y*w),2*(y*z-x*w),1-2*(x*x+y*y)))


def rotate(vector, pose):
    basis = axes(pose)
    return tuple(sum(vector[j]*basis[j][i] for j in range(3)) for i in range(3))


def ray_interval(origin, direction, box, padding=0):
    basis = axes(box.pose)
    offset = sub(origin,box.pose.position_m)
    low, high = -float("inf"), float("inf")
    for axis,size in zip(basis,box.size_m):
        p,d = dot(offset,axis),dot(direction,axis)
        extent = size/2+padding
        if abs(d) < 1e-12:
            if abs(p) > extent:
                return None
            continue
        a,b = (-extent-p)/d,(extent-p)/d
        low,high = max(low,min(a,b)),min(high,max(a,b))
        if low > high:
            return None
    return low,high


def intersects(a,b,tolerance=0):
    aa,bb = axes(a.pose),axes(b.pose)
    delta = sub(b.pose.position_m,a.pose.position_m)
    for candidate in (*aa,*bb,*(cross(x,y) for x in aa for y in bb)):
        norm = sqrt(dot(candidate,candidate))
        if norm < 1e-10:
            continue
        axis = tuple(x/norm for x in candidate)
        ra = sum(size/2*abs(dot(axis,v)) for size,v in zip(a.size_m,aa))
        rb = sum(size/2*abs(dot(axis,v)) for size,v in zip(b.size_m,bb))
        if abs(dot(delta,axis)) >= ra+rb-tolerance:
            return False
    return True


def corners(box):
    basis = axes(box.pose)
    return tuple(tuple(box.pose.position_m[i]+sum(sign[j]*box.size_m[j]/2*basis[j][i] for j in range(3)) for i in range(3)) for sign in product((-1,1),repeat=3))


def inside_room(point, size, margin=0):
    return (-size[0]/2+margin <= point[0] <= size[0]/2-margin
            and -size[1]/2+margin <= point[1] <= size[1]/2-margin
            and margin <= point[2] <= size[2]-margin)
