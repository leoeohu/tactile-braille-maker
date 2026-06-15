"""Headless Blender step: heightmap PNG -> displaced, flat-bottomed solid plate -> STL.

Run by relief.py as:
    blender -b --python blender_displace.py -- <heightmap.png> <out.stl> \
            <size_x_mm> <size_y_mm> <mid_level> <strength_mm> <bottom_z_mm> <res>

Conventions
-----------
* 1 Blender unit == 1 mm (STL has no units; slicers read mm). No scene-unit scaling.
* Plate is size_x x size_y (its aspect matches the heightmap so the relief is NOT
  squished). `res` is the subdivision count along the LONGER side; the shorter side
  gets a proportional count so vertex density (precision) is uniform.
* z = (heightmap_value - mid_level) * strength. relief.py picks mid_level/strength so
  the flat plate surface sits at z=0:
    - raise   : mid=0,            features rise to +relief, bottom at -base.
    - engrave : mid=bg_gray,      features carve to -relief (braille still rises above 0),
                bottom at -(base+relief).
  The heightmap border equals the background value, so the perimeter sits at z=0.
* Flat bottom at bottom_z, vertical side walls -> watertight, prints flat-side-down.
"""
import sys
import bpy
import bmesh

argv = sys.argv[sys.argv.index("--") + 1:]
heightmap, out_stl = argv[0], argv[1]
size_x = float(argv[2])    # plate width, mm
size_y = float(argv[3])    # plate height, mm
mid_level = float(argv[4]) # displace mid-level (heightmap value that maps to z=0)
strength = float(argv[5])  # displace strength (mm per full heightmap unit)
bottom_z = float(argv[6])  # flat bottom plane (mm); must sit below the deepest feature
res = int(argv[7])         # subdivisions along the longer side

# proportional subdivisions -> uniform vertex density on a rectangular plate
longer = max(size_x, size_y)
res_x = max(2, round(res * size_x / longer))
res_y = max(2, round(res * size_y / longer))

# --- clean scene ---------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)

# --- subdivided plane (rectangular = aspect-correct) ---------------------
bpy.ops.mesh.primitive_grid_add(x_subdivisions=res_x, y_subdivisions=res_y, size=size_x)
obj = bpy.context.active_object
obj.name = "TactilePlate"
if size_y != size_x:                       # stretch Y to the real plate height, then bake it in
    obj.scale.y = size_y / size_x
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
print(f"[blender] plate {size_x:.1f}x{size_y:.1f}mm  grid {res_x}x{res_y} verts={len(obj.data.vertices)}")

# --- displacement from the heightmap ------------------------------------
img = bpy.data.images.load(heightmap)
img.colorspace_settings.name = 'Non-Color'  # map pixel value linearly to height
tex = bpy.data.textures.new("heightmap", 'IMAGE')
tex.image = img
tex.extension = 'EXTEND'        # clamp at edges, never tile
tex.use_interpolation = True    # smooth slopes (good for braille domes)
tex.filter_size = 0.1           # minimal filtering -> crisp edges (default 1.0 blurs)

mod = obj.modifiers.new("displace", 'DISPLACE')
mod.texture = tex
mod.texture_coords = 'UV'   # grid ships a 0..1 UV map -> image maps once across plate
mod.direction = 'Z'
mod.mid_level = mid_level   # heightmap value that maps to z=0 (the flat plate surface)
mod.strength = strength     # raise: +relief at value 1; engrave: features carved below 0
bpy.ops.object.modifier_apply(modifier=mod.name)

# --- turn the displaced sheet into a watertight, flat-bottomed solid ------
me = obj.data
bm = bmesh.new()
bm.from_mesh(me)

boundary = [e for e in bm.edges if e.is_boundary]            # outer rectangle
ret = bmesh.ops.extrude_edge_only(bm, edges=boundary)
geom = ret['geom']
for v in (g for g in geom if isinstance(g, bmesh.types.BMVert)):
    v.co.z = bottom_z                                       # flatten extruded rim
bottom_edges = [g for g in geom
                if isinstance(g, bmesh.types.BMEdge) and g.is_boundary]
bmesh.ops.contextual_create(bm, geom=bottom_edges)          # fill bottom n-gon
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)           # consistent outward normals

bm.to_mesh(me)
bm.free()
me.update()

# bbox report
zs = [v.co.z for v in me.vertices]
print(f"[blender] solid verts={len(me.vertices)} z=[{min(zs):.3f},{max(zs):.3f}]")

# --- export STL ----------------------------------------------------------
bpy.ops.object.select_all(action='DESELECT')
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
bpy.ops.wm.stl_export(
    filepath=out_stl,
    export_selected_objects=True,
    apply_modifiers=True,
    global_scale=1.0,
)
print(f"[blender] wrote {out_stl}")
