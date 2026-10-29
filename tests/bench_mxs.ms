(
    -- Mirrors tests/bench_py.py exactly. Returns timings in milliseconds.
    local t = #()
    local t0 = 0
    local i = 0

    -- B1: pure loop 100k
    t0 = timeStamp()
    local x = 0.0
    for i = 1 to 100000 do x += i
    append t (timeStamp() - t0)

    -- B2: string concat 5k x 8 chars
    t0 = timeStamp()
    local s = ""
    for i = 1 to 5000 do s += "abcdefgh"
    append t (timeStamp() - t0)

    -- B3: array append 100k
    t0 = timeStamp()
    local lst = #()
    for i = 1 to 100000 do append lst i
    append t (timeStamp() - t0)

    -- B4: no dict in MAXScript -- reuse the slot with a bitArray build instead
    t0 = timeStamp()
    local ba = #{}
    for i = 1 to 50000 do ba[i] = true
    append t (timeStamp() - t0)

    -- B5: create 200 boxes
    t0 = timeStamp()
    for i = 1 to 200 do (Box width:10 length:10 height:10)
    append t (timeStamp() - t0)

    -- B6: read 200 node names
    t0 = timeStamp()
    local names = for o in objects collect o.name
    append t (timeStamp() - t0)

    -- B7: write 200 node names (no-op write)
    t0 = timeStamp()
    for o in objects do o.name = o.name
    append t (timeStamp() - t0)

    t
)
