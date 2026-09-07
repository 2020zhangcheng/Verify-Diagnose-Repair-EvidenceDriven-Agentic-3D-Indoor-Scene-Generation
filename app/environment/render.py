"""Pinhole flat-color rendering with metric camera-Z depth and real occlusion."""
import json
import math
import struct
import zlib
from app.contracts.models import Box, CameraPose
from app.environment.simulation_models import SimObject
from app.environment.geometry import rotate, ray_interval


def room_surfaces(scene):
    x,y,z = scene.room_size_m
    boxes = [("floor",(0,0,-.05),(x,y,.1),(160,160,150)),
             ("ceiling",(0,0,z+.05),(x,y,.1),(225,225,220)),
             ("left",(-x/2-.05,0,z/2),(.1,y,z),(200,205,210)),
             ("right",(x/2+.05,0,z/2),(.1,y,z),(200,205,210)),
             ("front",(0,y/2+.05,z/2),(x,.1,z),(215,215,210)),
             ("back",(0,-y/2-.05,z/2),(x,.1,z),(215,215,210))]
    return tuple(SimObject(object_id=f"room:{name}",geometry=Box(pose=CameraPose(frame_id=scene.frame_id,position_m=pos,orientation_xyzw=(0,0,0,1)),size_m=size),color=color) for name,pos,size,color in boxes)


def calibration(scene):
    focal = scene.width/(2*math.tan(math.radians(scene.horizontal_fov_deg)/2))
    return {"schema_version":1,"width":scene.width,"height":scene.height,"fx":focal,"fy":focal,
            "cx":scene.width/2,"cy":scene.height/2,"pixel_coordinates":"pixel centers at (column+0.5,row+0.5)",
            "camera_axes":"+X right, +Y up, -Z forward", "depth_convention":"camera_z",
            "unit":"m","near_m":scene.near_m,"far_m":scene.far_m,"renderer":"box-raycast-v1"}


def png_bytes(width,height,pixels):
    def chunk(kind, data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    raw = b''.join(b'\x00'+bytes(pixels[row*width*3:(row+1)*width*3]) for row in range(height))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',width,height,8,2,0,0,0))
            +chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b''))


def render(scene,pose):
    intrinsics = calibration(scene)
    surfaces = (*scene.objects,*room_surfaces(scene))
    pixels,depth = [],[]
    for row in range(scene.height):
        scanline=[]
        for col in range(scene.width):
            local=((col+.5-intrinsics['cx'])/intrinsics['fx'], -(row+.5-intrinsics['cy'])/intrinsics['fy'], -1)
            direction=rotate(local,pose)
            best,color=None,(0,0,0)
            for obj in surfaces:
                interval=ray_interval(pose.position_m,direction,obj.geometry)
                if interval is None:
                    continue
                hit=interval[0] if interval[0]>=scene.near_m else interval[1]
                if scene.near_m<=hit<=scene.far_m and (best is None or hit<best):
                    best,color=hit,obj.color
            pixels.extend(color)
            scanline.append(best)
        depth.append(scanline)
    depth_data={"schema_version":1,"width":scene.width,"height":scene.height,"unit":"m",
                "convention":"camera_z","invalid":None,"depth_m":depth}
    return png_bytes(scene.width,scene.height,pixels),depth_data,intrinsics
