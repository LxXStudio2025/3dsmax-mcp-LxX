"""Benchmark run INSIDE 3ds Max 2016's own Python 2.7 (via python.ExecuteFile).

Writes JSON next to this file so the MAXScript side can be compared against it.
Python 2.7: no f-strings, range() builds a list, use xrange for loops.
"""
import json
import os
import time

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_py.json")
r = {"python_version": "%d.%d.%d" % __import__("sys").version_info[:3]}


def timed(name, fn):
    t0 = time.time()
    try:
        extra = fn()
        r[name] = time.time() - t0
        if extra is not None:
            r[name + "_info"] = extra
    except Exception as exc:
        r[name] = -1
        r[name + "_error"] = "%s: %s" % (type(exc).__name__, exc)


# ---------------------------------------------------------------- B1 pure loop
def b1():
    x = 0
    for i in xrange(100000):
        x += i
    return x


# ------------------------------------------------------------ B2 str concat 5k
def b2():
    s = ""
    for i in xrange(5000):
        s += "abcdefgh"
    return len(s)


# ---------------------------------------------------------- B3 list append 100k
def b3():
    lst = []
    for i in xrange(100000):
        lst.append(i)
    return len(lst)


# ------------------------------------------------------ B4 dict build 50k
def b4():
    d = {}
    for i in xrange(50000):
        d[i] = i * 2
    return len(d)


timed("py_loop_100k", b1)
timed("py_strcat_5k", b2)
timed("py_list_100k", b3)
timed("py_dict_50k", b4)

# ------------------------------------------------- B5 MaxPlus: create 200 boxes
def b5():
    import MaxPlus
    made = 0
    for i in xrange(200):
        obj = MaxPlus.Factory.CreateGeomObject(MaxPlus.ClassIds.Box)
        pb = obj.ParameterBlock
        pb.Length.Value = 10.0
        pb.Width.Value = 10.0
        pb.Height.Value = 10.0
        MaxPlus.Factory.CreateNode(obj)
        made += 1
    return made


timed("py_maxplus_create_200", b5)


# ------------------------------------------------ B6 MaxPlus: read 200 node names
def b6():
    import MaxPlus
    names = []
    for n in MaxPlus.Core.GetRootNodes():
        names.append(n.Name)
    return len(names)


timed("py_maxplus_read_names", b6)


# --------------------------------------------- B7 MaxPlus: write 200 node names
def b7():
    import MaxPlus
    cnt = 0
    for n in MaxPlus.Core.GetRootNodes():
        n.Name = n.Name  # no-op write, still crosses the bridge
        cnt += 1
    return cnt


timed("py_maxplus_write_names", b7)

with open(OUT, "w") as fh:
    json.dump(r, fh, indent=2, sort_keys=True)
