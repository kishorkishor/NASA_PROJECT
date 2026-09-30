"""Screenshot and layout/accessibility checks for the demo page (the server must be running on :8765).

    pip install playwright
    python tools/capture_screenshots.py

Writes tools/screenshots/*.png and tools/screenshots/ui_checks.json: horizontal overflow, text contrast,
touch-target sizes and console errors at desktop and phone widths in light and dark mode, plus the
error, loading and empty states. Uses the installed Google Chrome.
"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8765/"
OUT = Path(__file__).parent / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

METRICS_JS = r"""
() => {
  const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const lum = ([r, g, b]) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const over = (top, under) => { const a = top[3]; return [0, 1, 2].map(i => top[i] * a + under[i] * (1 - a)).concat(1); };
  const bgOf = el => { const layers = []; for (let e = el; e; e = e.parentElement) {
      const c = parse(getComputedStyle(e).backgroundColor); if (c && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; } }
    let bg = parse(getComputedStyle(document.body).backgroundColor) || [255, 255, 255, 1];
    for (const l of layers.reverse()) bg = over(l, bg); return bg; };
  const ratio = (a, b) => { const [x, y] = [lum(a) + 0.05, lum(b) + 0.05]; return x > y ? x / y : y / x; };
  const pairs = {
    "body lede": ".step .lede", "sub text": ".sub", "tile note": ".t-note", "tile label": ".t-label",
    "legend": ".legend li", "table cell": "table.data td", "table header": "table.data th",
    "place sub": ".place-sub", "action text": ".action", "caveat": ".caveat", "meta": ".meta",
    "step link": ".stepbar a", "seg button (off)": ".seg button[aria-pressed=false]", "axis tick (svg)": ".axis text",
    "badge": ".badge", "map legend": ".map-legend span", "footer": "footer p",
  };
  const contrast = {};
  for (const [name, sel] of Object.entries(pairs)) {
    const el = document.querySelector(sel); if (!el) { contrast[name] = null; continue; }
    const cs = getComputedStyle(el); const fg = parse(el instanceof SVGElement ? cs.fill : cs.color);
    const bg = bgOf(el instanceof SVGElement ? el.closest("div") : el);
    contrast[name] = { ratio: +ratio(over(fg, bg), bg).toFixed(2), fontSize: cs.fontSize };
  }
  const targets = {};
  const measure = (name, sel) => { const els = [...document.querySelectorAll(sel)].filter(e => e.offsetParent !== null);
    if (!els.length) return; const rs = els.map(e => e.getBoundingClientRect());
    targets[name] = { count: els.length, minW: Math.round(Math.min(...rs.map(r => r.width))), minH: Math.round(Math.min(...rs.map(r => r.height))) }; };
  measure("theme button", "#theme-btn"); measure("segmented buttons", ".seg button"); measure("step bar links", ".stepbar a");
  measure("next links", ".next-link"); measure("place rows", "#places tbody tr"); measure("table-view toggles", "details.table-view summary");
  measure("map zoom buttons", ".leaflet-control-zoom a");
  return { viewport: [innerWidth, innerHeight], scrollWidth: document.scrollingElement.scrollWidth,
           overflowX: document.scrollingElement.scrollWidth > innerWidth, contrast, targets };
}
"""


def settle(page):
    page.wait_for_selector("#inspector .place-title", timeout=30000)
    page.wait_for_selector("#chart-fixed svg", timeout=30000)
    page.wait_for_timeout(1500)  # basemap tiles


def run():
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        configs = [
            ("desktop-light", {"width": 1280, "height": 800}, False, "light"),
            ("desktop-dark", {"width": 1280, "height": 800}, False, "dark"),
            ("iphone-390-light", {"width": 390, "height": 844}, True, "light"),
            ("iphone-390-dark", {"width": 390, "height": 844}, True, "dark"),
            ("android-360-light", {"width": 360, "height": 800}, True, "light"),
        ]
        for name, vp, mobile, theme in configs:
            ctx = browser.new_context(viewport=vp, device_scale_factor=1, is_mobile=mobile, has_touch=mobile, color_scheme=theme)
            page = ctx.new_page()
            errors = []
            page.on("console", lambda m, e=errors: e.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda ex, e=errors: e.append(str(ex)))
            page.goto(BASE + f"?theme={theme}", wait_until="networkidle")
            settle(page)
            page.screenshot(path=OUT / f"{name}-full.png", full_page=True)
            for sel, part in [("#step-1 .card", "step1"), ("#step-2 .card:has(#chart-fixed)", "step2-chart"),
                              ("#step-2 .card:has(#calendar)", "step2-calendar"), ("#step-3 .grid-2", "step3-map-inspector")]:
                el = page.locator(sel).first
                if el.count():
                    el.screenshot(path=OUT / f"{name}-{part}.png")
            results[name] = {"console_errors": errors, **page.evaluate(METRICS_JS)}
            ctx.close()

        # Interaction proof (desktop, light).
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, color_scheme="light")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda ex: errors.append(str(ex)))
        page.goto(BASE + "?theme=light", wait_until="networkidle")
        settle(page)
        it = {}
        chart = page.locator("#chart-fixed svg")
        chart.scroll_into_view_if_needed()
        box = chart.bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.5)
        page.wait_for_timeout(300)
        it["hover_tooltip_visible"] = page.evaluate("document.querySelector('#tooltip').classList.contains('on')")
        it["hover_tooltip_text"] = page.inner_text("#tooltip")
        page.locator("#step-2 .card").first.screenshot(path=OUT / "interact-hover-tooltip.png")
        page.mouse.move(5, 5)
        chart.focus()
        page.keyboard.press("ArrowLeft")
        page.wait_for_timeout(200)
        it["keyboard_tooltip_text"] = page.inner_text("#tooltip")
        page.click("[data-region=all]")
        it["region_toggle_label"] = page.get_attribute("#chart-fixed svg", "aria-label")[:70]
        before = page.inner_text("#insp-title")
        page.locator("#places tbody tr").nth(2).click()
        page.wait_for_function(f"document.querySelector('#insp-title')?.textContent !== {json.dumps(before)}", timeout=10000)
        it["row_click_inspector"] = [before, page.inner_text("#insp-title")]
        page.locator("#places tbody tr").nth(4).focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(800)
        it["row_enter_selected"] = page.get_attribute("#places tbody tr:nth-child(5)", "aria-selected")
        page.click("[data-kind=forecast]")
        page.wait_for_function("document.querySelector('#places-title').textContent.includes('2027')", timeout=15000)
        it["season_toggle_title"] = page.inner_text("#places-title")
        it["season_toggle_count"] = page.inner_text("#map-count")
        page.locator("#step-3 .grid-2").screenshot(path=OUT / "interact-forecast.png")
        page.click("#theme-btn")
        it["theme_after_click"] = page.evaluate("document.documentElement.dataset.theme || 'auto'")
        it["page_errors"] = errors
        # "Not sampled" Sentinel-2 state: a monitor cell far down the list.
        page.click("[data-kind=observed]")
        page.wait_for_function("document.querySelector('#places-title').textContent.includes('2026')", timeout=15000)
        m = page.evaluate("fetch('/api/map?kind=observed').then(r => r.json())")
        pi = m["columns"].index("priority")
        row = next(r for r in m["rows"] if r[pi] == "monitor")
        page.evaluate(f"select({row[0]}, {row[1]}, {{pan: true}})")
        page.wait_for_timeout(1200)
        it["monitor_cell_s2_text"] = page.inner_text("#inspector")[-400:]
        page.locator("#inspector").screenshot(path=OUT / "state-not-sampled.png")
        ctx.close()
        results["interactions"] = it

        # Error state + retry, and reduced motion.
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, color_scheme="light", reduced_motion="reduce")
        page = ctx.new_page()
        page.route("**/api/calendar/annual*", lambda route: route.fulfill(status=500, body="boom"))
        page.goto(BASE + "?theme=light", wait_until="networkidle")
        page.wait_for_selector("#chart-naive .state button", timeout=15000)
        page.locator("#step-1 .card").screenshot(path=OUT / "state-error.png")
        err_text = page.inner_text("#chart-naive")
        page.unroute("**/api/calendar/annual*")
        page.click("#chart-naive .state button")
        page.wait_for_selector("#chart-naive svg", timeout=15000)
        results["error_state"] = {"message": err_text, "retry_recovers": page.locator("#chart-naive svg").count() == 1}
        results["reduced_motion_tooltip_transition"] = page.evaluate("getComputedStyle(document.querySelector('#tooltip')).transitionDuration")
        ctx.close()

        # Loading state (slow API).
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, color_scheme="light")
        page = ctx.new_page()
        page.route("**/api/summary", lambda route: None)  # never answered: the page must hold its loading state
        page.goto(BASE + "?theme=light", wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        page.locator("#step-1 .card").screenshot(path=OUT / "state-loading.png")
        results["loading_state_text"] = page.inner_text("#chart-naive")
        ctx.close()
        browser.close()

    (OUT / "ui_checks.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    run()
