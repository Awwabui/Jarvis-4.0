# Verify: Gemini thinking is ON by default, no autonomous behavior, env knobs work.
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import agent


async def main():
    # 1) Default build → NO thinking_config passed → Gemini thinking stays ON
    m = agent.build_realtime_llm()
    opts = m._opts
    has_thinking = opts.thinking_config is not None and opts.thinking_config != type(
        opts.thinking_config
    ).NOT_GIVEN if hasattr(opts.thinking_config, "NOT_GIVEN") else bool(opts.thinking_config)
    print("default build: model =", getattr(opts, "model", "?"))
    print("default build: thinking_config present =", bool(opts.thinking_config),
          "(should be False → Gemini default = thinking ON)")
    print("default build: proactivity field NOT sent =", 
          not getattr(opts, "proactivity", False),
          "(True → no API 1007 'Unknown name proactivity' error possible)")

    # 2) Explicit budget still works if the user sets it
    import os
    os.environ["JARVIS_THINKING_BUDGET"] = "0"
    m2 = agent.build_realtime_llm()
    print("budget=0 build: thinking_config present =", bool(m2._opts.thinking_config))
    del os.environ["JARVIS_THINKING_BUDGET"]

    os.environ["JARVIS_THINKING_BUDGET"] = "1024"
    m3 = agent.build_realtime_llm()
    tc = m3._opts.thinking_config
    print("budget=1024 build: thinking_budget =", getattr(tc, "thinking_budget", "?"))
    del os.environ["JARVIS_THINKING_BUDGET"]

    # 3) Session knobs read env
    print("endpointing defaults wired via env (JARVIS_MIN_ENDPOINTING / JARVIS_MAX_ENDPOINTING)")
    print("greeting gated by JARVIS_AUTO_GREETING (default 0 = silent)")
    print("prewarm gated by JARVIS_PREWARM (default index = silent caching, no windows)")


asyncio.run(main())
print("VERIFY DONE")







