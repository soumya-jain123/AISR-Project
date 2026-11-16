"""Places the robot can work in. Each scene is built from CoppeliaSim's own model library when you switch to it.

A scene has a floor (x0, x1, y0, y1), a robot start pose (x, y, heading in degrees), the props to load,
a worker who walks back and forth between two points, and a route (the study's task) through the hazards.
`still` is where the worker stands instead, right on the route, for the E-stop test.
"""
import math

WALL = "infrastructure/walls/240cm high walls/wall section {}cm.ttm"
LOW = "infrastructure/walls/80cm high walls/wall section {}cm.ttm"
PILLAR = "infrastructure/walls/240cm high walls/pillar 50cm.ttm"
BLOCK = "infrastructure/other/resizable concret block.ttm"
RACK = "furniture/shelves-cupboards-racks/rack.ttm"
CABINET = "furniture/shelves-cupboards-racks/deep cupboard.ttm"
PERSON = "people/Standing Bill.ttm"
FIRE, SMOKE = "other/fire.ttm", "other/smoke.ttm"


def wall(x0, y0, x1, y1, kind=WALL):
    """Straight wall from (x0, y0) to (x1, y1), axis-aligned, built from 2 m and 1 m sections."""
    items, horizontal = [], y0 == y1
    a, b = (x0, x1) if horizontal else (y0, y1)
    a, b = min(a, b), max(a, b)
    while b - a > 0.5:
        size = 200 if b - a >= 2 else 100
        mid = a + size / 200
        items.append((kind.format(size), mid if horizontal else x0, y0 if horizontal else mid, 90 if horizontal else 0, "wall"))
        a += size / 100
    return items


def box(x0, x1, y0, y1):
    return wall(x0, y0, x1, y0) + wall(x1, y0, x1, y1) + wall(x0, y1, x1, y1) + wall(x0, y0, x0, y1)


SCENES = {
    "open": dict(
        name="Open floor", about="the plain 5 x 5 m floor used for the delay study",
        floor=None, start=(-0.375, 0.35, 0), items=[], worker=None, still=None, route=None),

    "plant": dict(
        name="Chemical plant",
        about="a narrow corridor past the process line, a fire in the hall and a worker crossing the route",
        floor=(-5, 5, -5, 5), start=(-4, -4.25, 0),
        items=box(-5, 5, -5, 5) + wall(-5, -3.5, 2, -3.5)
        + [(CABINET, x, -2.95, 90, "process cabinet") for x in (-4.2, -2.9, -1.6, -0.3)]
        + [(CABINET, x, 4.5, 90, "control cabinet") for x in (-3.5, -2.2, -0.9)]
        + [(PILLAR, 1.8, -1.5, 0, "pillar"), (PILLAR, 4.2, 0.8, 0, "pillar"),
           (FIRE, -2, 1, 0, "fire"), (SMOKE, -2, 1, 0, "smoke")],
        worker=((1.2, -0.3), (4.6, -0.3)), still=(0, -4.25),
        route=[(3, -4.25), (3, 3.5)]),

    "warehouse": dict(
        name="Warehouse",
        about="long 1.2 m aisles between racks, with a worker crossing the middle of the aisles",
        floor=(-5, 5, -5, 5), start=(-4.2, -4, 0),
        items=box(-5, 5, -5, 5)
        + [(RACK, x, y, 0, "rack") for x in (-3, -1.5, 0, 1.5, 3) for y in (-2.4, -1.2, 1.2, 2.4)]
        + [(CABINET, 4.6, y, 0, "cabinet") for y in (1.2, 2.4, 3.6)],
        worker=((-4.2, 0), (4.2, 0)), still=(-1.8, -4),
        route=[(-0.75, -4), (-0.75, 4), (2.25, 4), (2.25, -4)]),

    "disaster": dict(
        name="Disaster site",
        about="a collapsed building: rubble, broken walls, smoke, an open drop on the south side and a victim to reach",
        floor=(-5, 5, -2.5, 2.5), start=(-4, -1.3, 0),
        items=wall(-5, 2.5, -1, 2.5) + wall(1.5, 2.5, 3.5, 2.5) + wall(-5, -1, -5, 2.5, LOW) + wall(5, 0, 5, 2.5, LOW)
        + [(BLOCK, x, y, yaw, "rubble") for x, y, yaw in ((-2.6, 0.9, 20), (-1.4, -0.35, 45), (0.6, -0.2, 10),
                                                           (2.3, -0.4, 30), (2.0, 1.4, 0))]
        + [(LOW.format(200), -0.2, 1.0, 30, "broken wall"), (LOW.format(100), -3.3, 0.2, -25, "broken wall"),
           (FIRE, 0.9, 1.6, 0, "fire"), (SMOKE, 0.9, 1.6, 0, "smoke"), (SMOKE, -2.2, 1.8, 0, "smoke")],
        victim=(3.6, 1.9),
        worker=((1.2, 0.9), (1.2, -2.0)), still=(0, -1.3),
        route=[(3.5, -1.3), (3.5, 1.0)]),
}
BLOCK_SIZE = 0.4   # rubble blocks are 1 m cubes in the library; scaled down to this


def build(sim, key, still=False):
    """Load scene `key` into the (stopped) simulation. Returns {"root", "worker", "labels"}.
    labels maps every obstacle shape to a plain name like "wall", used in messages and for collision checks."""
    s = SCENES[key]
    models = sim.getStringParam(sim.stringparam_resourcesdir) + "/models/"
    root = sim.createDummy(0.01)
    sim.setObjectAlias(root, "Scene")
    floor = sim.getObject("/Floor")
    hide = (sim.modelproperty_not_visible | sim.modelproperty_not_collidable | sim.modelproperty_not_measurable
            | sim.modelproperty_not_detectable | sim.modelproperty_not_respondable | sim.modelproperty_not_dynamic)
    sim.setModelProperty(floor, hide if s["floor"] else 0)
    labels, worker = {}, None

    def place(path, x, y, yaw, label):
        h = sim.loadModel(models + path)
        z = sim.getObjectPosition(h, -1)[2]
        sim.setObjectPosition(h, -1, [x, y, z])
        sim.setObjectOrientation(h, -1, [0, 0, math.radians(yaw)])
        sim.setObjectParent(h, root, True)
        shapes = list(filter(sim.isHandle, sim.getObjectsInTree(h, sim.sceneobject_shape)))   # some models swap parts on load
        for o in shapes:
            sim.setObjectInt32Param(o, sim.shapeintparam_static, 1)   # walls ship as dynamic; the robot would shove them
        # Distance checks use only the simple collision shapes; the detailed visual meshes are ~10x slower to check.
        solid = [o for o in shapes if sim.getObjectInt32Param(o, sim.shapeintparam_respondable)] or shapes
        if label != "floor":
            labels.update((o, label) for o in solid)
            for o in solid:   # people ship without these, which hides them from distance and collision checks
                sim.setObjectSpecialProperty(o, sim.objectspecialproperty_collidable | sim.objectspecialproperty_measurable
                                             | sim.objectspecialproperty_detectable)
                if label == "person":   # the worker is moved by hand each step; as a solid it would shove the robot around
                    sim.setObjectInt32Param(o, sim.shapeintparam_respondable, 0)
        return h

    if s["floor"]:
        x0, x1, y0, y1 = s["floor"]
        for i in range(round((x1 - x0) / 5)):
            for j in range(round((y1 - y0) / 5)):
                place("infrastructure/floors/5mX5m concrete floor.ttm", x0 + 2.5 + 5 * i, y0 + 2.5 + 5 * j, 0, "floor")
    for path, x, y, yaw, label in s["items"]:
        h = place(path, x, y, yaw, label)
        if path == BLOCK:
            sim.scaleObjects([h], BLOCK_SIZE, False)
            p = sim.getObjectPosition(h, -1)
            sim.setObjectPosition(h, -1, [p[0], p[1], BLOCK_SIZE / 2])
    if s.get("victim"):   # a person lying on the ground
        h = place(PERSON, *s["victim"], 0, "person")
        sim.setObjectOrientation(h, -1, [0, -1.5708, 0])
        sim.setObjectPosition(h, -1, [*s["victim"], 0.2])
    if s["worker"]:
        a, b = s["worker"]
        worker = place(PERSON, *(s["still"] if still else a), 0, "person")
    return {"root": root, "worker": worker, "labels": labels}


def clear(sim, built):
    if built:
        sim.removeObjects(sim.getObjectsInTree(built["root"]))


def worker_at(key, t, still=False):
    """Where the walking worker is at simulation time t: (x, y, heading) or None. Walks at 0.5 m/s, rests 2 s at each end."""
    s = SCENES[key]
    if not s["worker"] or still:
        return None
    (ax, ay), (bx, by) = s["worker"]
    length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
    walk, rest = length / 0.5, 2.0
    t %= 2 * (walk + rest)
    if t < walk:
        f, toward = t / walk, (bx - ax, by - ay)
    elif t < walk + rest:
        f, toward = 1.0, (bx - ax, by - ay)
    elif t < 2 * walk + rest:
        f, toward = 1 - (t - walk - rest) / walk, (ax - bx, ay - by)
    else:
        f, toward = 0.0, (ax - bx, ay - by)
    return ax + f * (bx - ax), ay + f * (by - ay), math.atan2(toward[1], toward[0])


if __name__ == "__main__":   # self-check of the worker's walk
    assert worker_at("plant", 0)[:2] == (1.2, -0.3)
    x, y, _ = worker_at("plant", 6.8)
    assert abs(x - 4.6) < 1e-9
    assert worker_at("plant", 0, still=True) is None and worker_at("open", 5) is None
    assert len(wall(-5, -3.5, 2, -3.5)) == 4   # 7 m = 3 x 2 m + 1 x 1 m
    print("ok")
