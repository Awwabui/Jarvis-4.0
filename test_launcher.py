# ============================================================================
# test_launcher.py — dry-run validation for the v5 Intelligent Open System.
# Launches NOTHING. Verifies:
#   1. Real system discovery (Start Menu / Program Files / Steam / Epic /
#      Xbox / UWP) + persistent cache
#   2. Fuzzy matching + confidence policy
#   3. Website intelligence (learned cache / DNS validation / search)
#   4. tool registration sanity (livekit function_tool)
# Usage:  venv\Scripts\python.exe test_launcher.py [--full]
#   --full : also runs a full system rescan (ignores cache)
# ============================================================================
import asyncio
import sys

# getattr: the stubs type stdout as TextIO, which has no `reconfigure`.
_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):
    _reconfigure(encoding="utf-8")

import jarvis_launcher as jl


async def main():
    full = "--full" in sys.argv

    # ── 1. Discovery ──────────────────────────────────────────────
    print("\n=== 1. SYSTEM DISCOVERY ===")
    apps = await jl.get_app_index(force=full)
    print(f"Discovered entries: {len(apps)}")
    by_source = {}
    for a in apps:
        by_source[a["source"]] = by_source.get(a["source"], 0) + 1
    for src, n in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f"  {src:12s} {n}")
    sample = [a["display"] for a in apps[:8]]
    print(f"Sample: {sample}")

    # ── 2. Matching ───────────────────────────────────────────────
    print("\n=== 2. MATCHING (dry — no launch) ===")
    queries = ["vs code", "spotify", "chrome", "notepad", "gta 5",
               "youtube", "racing", "netflix", "explorer", "vlc"]
    for q in queries:
        matches = jl.match_apps(jl.clean_query(q), apps)
        if matches:
            top, s = matches[0]
            runner = matches[1][1] if len(matches) > 1 else 0
            strong = (s >= jl._CONFIDENT
                      or (s >= jl._GOOD and (len(matches) == 1
                                             or s - runner >= jl._GAP_NEEDED)))
            decision = ("LAUNCH" if strong
                        else ("ASK-USER" if s >= jl._ASK else "FALL-THROUGH"))
            print(f"  '{q:10s}' → {top['display']!r:28s} "
                  f"[{top['source']}] score={s} {decision}")
        else:
            print(f"  '{q:10s}' → (no app match → website/file path)")

    # ── 3. Website intelligence ───────────────────────────────────
    print("\n=== 3. WEBSITE INTELLIGENCE ===")
    for site in ["youtube", "netflix", "wikipedia", "openai", "github"]:
        try:
            url, how = await asyncio.wait_for(
                jl.resolve_website(site, allow_search=not full), timeout=25)
            print(f"  '{site:10s}' → {url}  (how: {how})")
        except Exception as e:
            print(f"  '{site:10s}' → ERROR: {e}")

    # ── 4. Memory / tools sanity ──────────────────────────────────
    print("\n=== 4. TOOL REGISTRATION + MEMORY ===")
    for name in ("smart_open", "refresh_app_index_tool",
                 "list_discovered_apps_tool", "resolve_website_tool"):
        obj = getattr(jl, name, None)
        print(f"  {name:26s} {'✅ registered' if obj else '❌ missing'}")
    print(f"  launch history: {len(jl._last_played() or []) if jl._last_played() else 0} "
          f"entries file={jl.LAUNCH_HISTORY_FILE}")
    print(f"  cache file:     {jl.APP_INDEX_FILE}")

    print("\n✅ All validation checks finished (nothing was launched).")


if __name__ == "__main__":
    asyncio.run(main())
