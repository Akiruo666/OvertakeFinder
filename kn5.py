"""Reading the road geometry from an Assetto Corsa track model (.kn5).

Only what is needed to know where the tarmac is: the node tree with the
triangles of every mesh and the name of its material. Textures are skipped.
Layout (little endian), as used by community tools; checked on a real file:

  "sc6969", int32 version (5 here; above 5 one extra int32 follows)
  int32 texture count; each: int32 flag, string name, int32 size, bytes
  int32 material count; each: string name, string shader, int16, (v>4) int32 depth,
        int32 property count (string name, float, 36 bytes), int32 texture count
        (string sampler, int32 slot, string texture)
  nodes (recursive): int32 type (1 dummy, 2 mesh, 3 skinned), string name,
        int32 child count, bool active, then
        type 1: 16 floats matrix;  type 2: 3 bytes flags, int32 vertex count,
        vertices of 44 bytes (position 3f, normal 3f, uv 2f, tangent 3f),
        int32 index count, uint16 indices, int32 material id, int32 layer,
        float lod in/out, float bounding sphere (4), bool renderable
        type 3: not needed here.
"""
import struct


class Kn5Error(Exception):
    pass


class _R:
    def __init__(self, buf):
        self.b, self.p = buf, 0

    def i32(self):
        (v,) = struct.unpack_from("<i", self.b, self.p)
        self.p += 4
        return v

    def u8(self):
        v = self.b[self.p]
        self.p += 1
        return v

    def string(self):
        n = self.i32()
        if not 0 <= n < 4096:
            raise Kn5Error("bad string length %d at %d" % (n, self.p))
        s = self.b[self.p:self.p + n].decode("utf-8", "replace")
        self.p += n
        return s

    def skip(self, n):
        self.p += n


def read_model(path):
    """Returns (materials, meshes). materials: list of (name, shader).
    meshes: list of dicts name, material (index), vertices (list of (x,y,z)),
    triangles (list of (i,j,k)) in world coordinates (parent matrices applied)."""
    with open(path, "rb") as fh:
        buf = fh.read()
    if buf[:6] != b"sc6969":
        raise Kn5Error("Not a KN5 model file.")
    r = _R(buf)
    r.skip(6)
    ver = r.i32()
    if ver < 1 or ver > 20:
        raise Kn5Error("Unsupported KN5 version %d." % ver)
    if ver > 5:
        r.i32()
    for _ in range(r.i32()):                    # textures
        r.i32()
        r.string()
        r.skip(r.i32())
    materials = []
    for _ in range(r.i32()):
        name, shader = r.string(), r.string()
        r.skip(2)
        if ver > 4:
            r.i32()
        for _ in range(r.i32()):
            r.string()
            r.skip(40)
        for _ in range(r.i32()):
            r.string()
            r.i32()
            r.string()
        materials.append((name, shader))
    meshes = []
    _node(r, None, materials, meshes)
    return materials, meshes


def _mul(a, b):
    """Row-vector convention (as in DirectX): result = a * b."""
    return [sum(a[i * 4 + k] * b[k * 4 + j] for k in range(4)) for i in range(4) for j in range(4)]


_IDENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def _node(r, parent, materials, meshes):
    kind = r.i32()
    name = r.string()
    children = r.i32()
    r.u8()                                     # active
    matrix = parent or _IDENT
    if kind == 1:
        local = list(struct.unpack_from("<16f", r.b, r.p))
        r.skip(64)
        matrix = _mul(local, parent or _IDENT)
    elif kind == 2:
        r.skip(3)
        nv = r.i32()
        if not 0 <= nv < 5_000_000:
            raise Kn5Error("bad vertex count %d" % nv)
        raw = r.b[r.p:r.p + 44 * nv]
        r.skip(44 * nv)
        ni = r.i32()
        idx = struct.unpack_from("<%dH" % ni, r.b, r.p)
        r.skip(2 * ni)
        mat = r.i32()
        r.skip(4 + 8 + 16 + 1)                 # layer, lod in/out, sphere, renderable
        m = matrix
        verts = []
        for x, y, z, *_rest in struct.iter_unpack("<11f", raw):
            verts.append((x * m[0] + y * m[4] + z * m[8] + m[12],
                          x * m[1] + y * m[5] + z * m[9] + m[13],
                          x * m[2] + y * m[6] + z * m[10] + m[14]))
        tris = [(idx[i], idx[i + 1], idx[i + 2]) for i in range(0, ni - 2, 3)]
        meshes.append({"name": name, "material": mat, "vertices": verts, "triangles": tris})
    else:
        raise Kn5Error("Unsupported node type %d (%s)" % (kind, name))
    for _ in range(children):
        _node(r, matrix, materials, meshes)
