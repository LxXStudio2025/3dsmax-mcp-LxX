import json, os, time
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_py_pure.json")
r = {}
def timed(name, fn):
    t0 = time.time()
    try:
        r[name] = time.time() - t0 if fn() is None else time.time() - t0
    except Exception as exc:
        r[name] = -1
        r[name + "_err"] = "%s: %s" % (type(exc).__name__, exc)

def b1():
    x = 0
    for i in range(100000): x += i
def b2():
    s = ""
    for i in range(5000): s += "abcdefgh"
def b3():
    lst = []
    for i in range(100000): lst.append(i)
def b4():
    d = {}
    for i in range(50000): d[i] = i * 2

timed("py_loop_100k", b1)
timed("py_strcat_5k", b2)
timed("py_list_100k", b3)
timed("py_dict_50k", b4)
f = open(OUT, "w"); json.dump(r, f, indent=2); f.close()
