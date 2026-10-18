"""我方二辩「不与队友撞例子」单测

规则：场上我方一辩/三辩已经用过的案例，二辩不得重复引用。
跑法：cd debate-agent && python3 tools/test_norepeat.py
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from debate import engine, prompts  # noqa: E402

R = []


def ok(name, cond, extra=""):
    R.append(bool(cond))
    print(("✓ " if cond else "✗ ") + name + (("  " + str(extra)) if extra else ""))


# ---------- 1 检测：只认「我方」标记的行 ----------
print("\n======== 1 只从「我方」标记的行里提取已用素材 ========")
ctx = (
    "[我方] 我方一辩：相机诞生后写实肖像萎缩，绘画走向现代艺术。\n"
    "[对方] 对方一辩：AI 用 AlphaFold 证明技术多强，会消解人的价值。\n"
    "[hall] 主席：请双方注意时间。\n"
)
used = engine.used_materials(ctx)
ok("我方用过的「相机」被检出", "相机" in used, used)
ok("我方用过的「写实肖像」被检出", "写实肖像" in used, used)
ok("★对方提到的 AlphaFold 不算我方已用", "AlphaFold" not in used, used)

# ---------- 2 检测：多种素材 + 去重 ----------
print("\n======== 2 多素材提取与去重 ========")
ctx2 = ("[我方] 一辩：AlphaFold 预测两亿蛋白。\n"
        "[我方] 三辩：用计算器不失数学能力，工业革命织工失业也说明问题。\n"
        "[我方] 一辩：再说一遍 AlphaFold。\n")
used2 = engine.used_materials(ctx2)
ok("AlphaFold 命中", "AlphaFold" in used2, used2)
ok("计算器 命中", "计算器" in used2, used2)
ok("工业革命 命中", "工业革命" in used2, used2)
ok("重复出现只记一次", used2.count("AlphaFold") == 1, used2)

# ---------- 3 提示词拼接 ----------
print("\n======== 3 注入提示词的文案 ========")
n1 = engine._no_repeat_note(ctx)
ok("有素材时给出「请勿重复」清单", "请勿重复" in n1 and "相机" in n1)
n2 = engine._no_repeat_note("[对方] AI 取代岗位。")
ok("无素材时提示「尚未使用、只用一个」", "尚未使用" in n2 and "只用一个" in n2)
n3 = engine._no_repeat_note("")
ok("空输入不报错", isinstance(n3, str) and len(n3) > 0)

# ---------- 4 系统提示词里的硬约束 ----------
print("\n======== 4 提示词硬约束 ========")
ok("SYSTEM 含「与队友分工」段", "与队友分工" in prompts.SYSTEM)
ok("明确禁止复述队友例子", "绝不复述" in prompts.SYSTEM)
ok("明确禁止重讲队友金句", "金句" in prompts.SYSTEM)
ok("★纯净模式（反方训练版）不含该段", "与队友分工" not in prompts.SYSTEM_CLEAN)
ok("★纯净模式不含论据库", "AlphaFold" not in prompts.SYSTEM_CLEAN)
ok("ONE_SHOT 允许论据库例外", "【论据库】" in prompts.ONE_SHOT)

# ---------- 5 端到端：prompt 真的带上了清单 ----------
print("\n======== 5 出稿提示词里确实带上了排除清单 ========")
import asyncio


class _FakeUsage:
    def snapshot(self):
        return {}


captured = {}


async def _fake_stream(self, client, prompt, t0, out, usage, sys_p):
    captured["prompt"] = prompt
    out["keep"] = ["测试句。"]
    if False:
        yield {}


async def main():
    orig = engine.Engine._stream_draft
    engine.Engine._stream_draft = _fake_stream
    try:
        eng = engine.Engine.__new__(engine.Engine)
        eng.mode = "one_shot"
        eng.backup = None
        eng.strong = None
        class _R:
            model = "stub"

        eng.routes = {"fast": _R(), "strong": _R()}
        agen = eng._rebut_one_shot(
            opponent=ctx, our_side="正方", our_case="拓展", topic="AI 与人的价值",
            duration=45, usage=_FakeUsage(), strong_client=None, memory="", no_strategy=False,
        )
        async for _ in agen:
            pass
    finally:
        engine.Engine._stream_draft = orig


asyncio.run(main())
p = captured.get("prompt", "")
ok("prompt 含「请勿重复」清单", "请勿重复" in p)
ok("prompt 里列出「相机」", "相机" in p)
ok("prompt 未把对方提到的 AlphaFold 列为已用", "AlphaFold、相机" not in p)

passed = sum(1 for x in R if x)
print(f"\n结果：{passed} 通过 / {len(R) - passed} 失败")
sys.exit(0 if passed == len(R) else 1)
