import json, urllib.request
B="http://rdfzaiclass.shenzhuo.vip:20347/debate"
def post(p, body):
    r=urllib.request.Request(B+p, data=json.dumps(body).encode(), headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r, timeout=90) as f: return json.loads(f.read().decode())
CASES=[
 ("主席点名我方(正方二辩)", ["[会场] 感谢双方一辩。下面进入驳论环节。有请正方二辩，针对反方立论展开反驳。"], 2000, True, True),
 ("主席点名对方(反方二辩)", ["[会场] 感谢正方二辩。下面有请反方二辩，针对正方立论展开反驳。"], 2000, True, False),
 ("对方说完静默6s",        ["[对方] 我方认为人工智能的发展总体上是消极的。"], 6000, True, True),
 ("对方半句静默2s",        ["[对方] 我方认为人工智能的发展总体上"], 2000, False, False),
 ("静默仅1.5s",            ["[对方] 我方认为人工智能的发展总体上是消极的。"], 1500, True, False),
 ("我方刚说完",            ["[我方] 我方认为人工智能的发展总体上是积极的。"], 6000, True, False),
]
p=f=0
for name, recent, gap, tail, exp in CASES:
    try:
        d=post("/api/should-speak", {"recent":recent, "our_side":"正方", "gap_ms":gap, "tail_complete":tail})
        ok=(d.get("speak")==exp); p+=ok; f+= (not ok)
        print(("  ✓ " if ok else "  ✗ ")+name+f"  → {d.get('speak')} ({d.get('reason','')})")
    except Exception as e:
        f+=1; print("  ✗ "+name+f"  异常 {e}")
print(f"\n线上后端回归：{p} 通过 / {f} 失败")
