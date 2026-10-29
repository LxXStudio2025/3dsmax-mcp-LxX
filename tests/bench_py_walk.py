import json, os, time
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_py_walk.json")
import MaxPlus
r = {}

root = MaxPlus.Core.GetRootNode()

t0 = time.time()
names = []
for i in range(root.NumChildren):
    child = root.GetChild(i)
    names.append(child.Name)
r["walk_names_ms"] = (time.time() - t0) * 1000
r["count"] = len(names)

t0 = time.time()
for i in range(root.NumChildren):
    child = root.GetChild(i)
    child.Name = child.Name
r["walk_write_ms"] = (time.time() - t0) * 1000

f = open(OUT, "w"); json.dump(r, f, indent=2); f.close()
