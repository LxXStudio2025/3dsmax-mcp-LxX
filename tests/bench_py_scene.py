import json, os, time
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_py_scene.json")
r = {}

def mark(k, v):
    r[k] = v
    f = open(OUT, "w"); json.dump(r, f, indent=2); f.close()   # checkpoint every step

t0 = time.time()
try:
    import MaxPlus
    mark("import_MaxPlus_ms", (time.time() - t0) * 1000)
except Exception as e:
    mark("import_error", str(e)); raise SystemExit

t0 = time.time()
made = []
try:
    for i in range(20):
        obj = MaxPlus.Factory.CreateGeomObject(MaxPlus.ClassIds.Box)
        pb = obj.ParameterBlock
        pb.Length.Value = 10.0
        pb.Width.Value = 10.0
        pb.Height.Value = 10.0
        made.append(MaxPlus.Factory.CreateNode(obj))
    mark("create_20_ms", (time.time() - t0) * 1000)
except Exception as e:
    mark("create_20_error", "%s: %s" % (type(e).__name__, e))

t0 = time.time()
try:
    n = 0
    for node in MaxPlus.Core.GetRootNodes():
        _ = node.Name
        n += 1
    mark("read_names_ms", (time.time() - t0) * 1000)
    mark("node_count", n)
except Exception as e:
    mark("read_error", "%s: %s" % (type(e).__name__, e))

t0 = time.time()
try:
    for node in MaxPlus.Core.GetRootNodes():
        node.Name = node.Name
    mark("write_names_ms", (time.time() - t0) * 1000)
except Exception as e:
    mark("write_error", "%s: %s" % (type(e).__name__, e))

mark("done", True)
