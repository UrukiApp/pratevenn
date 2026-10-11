"""Check UI state and layout without model weights or a running server.

Run with ``PLAYWRIGHT_BROWSERS_PATH=.cache/playwright uv run --locked python scripts/check_ui.py``.
"""

from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Page, Route, expect, sync_playwright

WEB = Path(__file__).resolve().parents[1] / "pratevenn" / "web"
CHAT_ID = "00000000-0000-4000-8000-000000000001"
MESSAGES = [
    {"role": "user", "content": "Hei", "source": "text"},
    {"role": "assistant", "content": "Hei!", "source": "text"},
]
STATUS = {
    "version": "0.1.0a7",
    "models": {kind: [{"id": kind, "label": kind}] for kind in ("stt", "llm", "tts")},
    "selected": {kind: kind for kind in ("stt", "llm", "tts")},
    "llm_device": "CPU",
    "system_prompt": "Svar på norsk.",
    "max_system_prompt_chars": 4000,
    "max_chat_messages": 400,
    "context_size": 8192,
    "context_sizes": [2048, 4096, 8192, 16384, 32768],
}
STATUS["models"]["stt"].append({"id": "stt-alternative", "label": "Alternative"})
MOCKS = """
localStorage.setItem('konvers.saveChats', 'false');
localStorage.setItem('konvers.showInlineFeedback', 'true');
localStorage.setItem('konvers.models', JSON.stringify({stt: 'stt-alternative'}));
window.connections = [];
window.sent = [];
window.delayAudio = false;
window.closeBeforeOpen = false;
window.AudioContext = class {
    createAnalyser() { return {connect() {}}; }
    resume() {
        if (delayAudio) return new Promise(resolve => window.resumeAudio = resolve);
        return Promise.resolve();
    }
};
window.WebSocket = class {
    static OPEN = 1;
    readyState = 0;
    constructor() {
        connections.push(this);
        queueMicrotask(() => {
            if (closeBeforeOpen) this.close();
            else { this.readyState = 1; this.onopen?.(); }
        });
    }
    send(data) { sent.push(JSON.parse(data)); }
    close() { this.readyState = 3; queueMicrotask(() => this.onclose?.()); }
};
window.emit = event => connections.at(-1).onmessage({data: JSON.stringify(event)});
"""


def check_hologram(page: Page) -> None:
    # The projection responds to speech and stays still with reduced motion.
    page.set_viewport_size({"width": 1280, "height": 800})
    page.evaluate("document.body.dataset.state = 'speaking'")
    core = page.locator(".holo-core")
    quiet_glow = core.evaluate("element => getComputedStyle(element).boxShadow")
    page.locator("#hologram").evaluate("element => element.style.setProperty('--voice', 0.8)")
    page.wait_for_function("""
        new DOMMatrix(getComputedStyle(document.querySelector('.holo-core')).transform).a > 1.09
    """)
    assert core.evaluate("element => getComputedStyle(element).boxShadow") != quiet_glow
    expect(page.locator("#hologram")).to_have_attribute("aria-hidden", "true")
    screenshot_dir = Path(".cache")
    screenshot_dir.mkdir(exist_ok=True)
    for theme in ("light", "dark"):
        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
        page.screenshot(path=str(screenshot_dir / f"pratevenn-hologram-{theme}.png"))
    page.locator("#hologram").screenshot(path=str(screenshot_dir / "pratevenn-hologram-detail.png"))
    page.set_viewport_size({"width": 390, "height": 700})
    page.screenshot(path=str(screenshot_dir / "pratevenn-hologram-mobile.png"))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.emulate_media(reduced_motion="reduce")
    for selector in (".holo-orbit", ".holo-core svg", ".holo-core::after"):
        element, _, pseudo = selector.partition("::")
        assert (
            page.locator(element).first.evaluate(
                "(element, pseudo) => getComputedStyle(element, pseudo).animationName",
                f"::{pseudo}" if pseudo else None,
            )
            == "none"
        )
    assert core.evaluate("element => getComputedStyle(element).transform") == "none"


def check_theme(page: Page) -> None:
    page.emulate_media(color_scheme="light")
    page.evaluate("localStorage.setItem('pratevenn.theme', 'invalid')")
    page.reload()
    expect(page.locator("#start")).to_be_enabled()
    page.emulate_media(color_scheme="dark")
    expect(page.locator("#themeToggle")).to_have_attribute("aria-label", "Switch to light theme")
    page.wait_for_function("getComputedStyle(document.body).backgroundColor === 'rgb(33, 33, 33)'")
    expect(page.locator('meta[name="theme-color"]')).to_have_attribute("content", "#212121")
    page.evaluate("""() => {
        Storage.prototype.getItem = () => { throw new Error('Storage unavailable'); };
    }""")
    page.emulate_media(color_scheme="light")
    expect(page.locator("#themeToggle")).to_have_attribute("aria-label", "Switch to dark theme")
    expect(page.locator('meta[name="theme-color"]')).to_have_attribute("content", "#ffffff")
    page.locator("#themeToggle").click()
    page.emulate_media(color_scheme="dark")
    page.emulate_media(color_scheme="light")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    expect(page.locator("#themeToggle")).to_have_attribute("aria-label", "Switch to light theme")
    page.add_init_script(
        "Storage.prototype.getItem = () => { throw new Error('Storage unavailable'); }"
    )
    page.emulate_media(color_scheme="dark")
    page.reload()
    expect(page.locator("#start")).to_be_enabled()
    expect(page.locator('meta[name="theme-color"]')).to_have_attribute("content", "#212121")


def check_hover_colors(page: Page) -> None:
    page.set_viewport_size({"width": 1280, "height": 800})
    for theme in ("light", "dark"):
        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
        for selector, variable in (
            (".prompt-chip", "--surface-hover"),
            (".sidebar-new-chat", "--surface-hover"),
            ("#themeToggle", "--surface-bg"),
        ):
            page.locator(selector).first.hover()
            page.wait_for_function(
                """({selector, variable}) => {
                    const color = document.createElement('span').style;
                    color.backgroundColor = getComputedStyle(document.documentElement)
                        .getPropertyValue(variable).trim();
                    return getComputedStyle(document.querySelector(selector)).backgroundColor
                        === color.backgroundColor;
                }""",
                arg={"selector": selector, "variable": variable},
                timeout=2000,
            )


def check_narrow_layout(page: Page) -> None:
    page.set_viewport_size({"width": 320, "height": 568})
    for selector in (".header-actions", "#newChat", "#ready", "#textForm", "#send"):
        box = page.locator(selector).bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 320, (selector, box)


def check_text_contrast(page: Page) -> None:
    page.set_viewport_size({"width": 1280, "height": 800})
    page.evaluate("document.body.dataset.state = 'error'")
    for theme in ("light", "dark"):
        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
        page.wait_for_timeout(200)
        failures = page.evaluate(r"""() => {
            const luminance = color => {
                const channels = color.match(/[\d.]+/g).slice(0, 3).map(value => {
                    const channel = Number(value) / 255;
                    return channel <= 0.04045 ? channel / 12.92
                        : ((channel + 0.055) / 1.055) ** 2.4;
                });
                return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
            };
            return [
                ['.chip-title', '.prompt-chip'], ['.chip-example', '.prompt-chip'],
                ['summary', 'aside'], ['.eyebrow', 'body'], ['footer', 'body'], ['#status', 'body'],
            ].flatMap(([foreground, background]) => {
                const a = luminance(getComputedStyle(document.querySelector(foreground)).color);
                const b = luminance(
                    getComputedStyle(document.querySelector(background)).backgroundColor);
                const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
                return ratio < 4.5 ? [{foreground, ratio}] : [];
            });
        }""")
        assert not failures, (theme, failures)


def main() -> None:
    held: dict[str, Route] = {}
    delays: set[str] = set()

    def respond(route: Route) -> None:
        path = urlsplit(route.request.url).path
        if path in delays:
            held[path] = route
        elif path == "/api/status":
            route.fulfill(json=STATUS)
        elif path == "/api/chats":
            route.fulfill(json=[{"id": CHAT_ID, "title": "Hei", "updated": "2026-09-30"}])
        elif path == f"/api/chats/{CHAT_ID}":
            route.fulfill(json={"id": CHAT_ID, "messages": MESSAGES})
        else:
            route.fulfill(
                path=WEB / (path.removeprefix("/static/") if path != "/" else "index.html")
            )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", respond)
        page.add_init_script(MOCKS)

        # Starter buttons must not override model initialization.
        delays.add("/api/status")
        page.goto("http://127.0.0.1:8000")
        expect(page).to_have_title("Pratevenn · Let's speak Norwegian")
        page.locator(".prompt-chip").first.click()
        expect(page.locator("#send")).to_be_disabled()
        held.pop("/api/status").fulfill(json=STATUS)
        delays.clear()
        expect(page.locator("#start")).to_be_enabled()
        expect(page.locator("#version")).to_have_text("Pratevenn v0.1.0a2 · ")
        expect(page.locator("#showInlineFeedback")).to_be_checked()
        expect(page.locator("#sttModel")).to_have_value("stt-alternative")
        expect(page.locator("#contextSize")).to_have_value("8192")
        expect(page.locator("#speed")).to_have_value("0.85")
        expect(page.locator("#speedValue")).to_have_text("0.85x")
        expect(page.locator("#pause")).to_have_value("1300")
        expect(page.locator("#pauseValue")).to_have_text("1.3 s")
        expect(page.locator("#threshold")).to_have_value("-30")
        expect(page.locator("#thresholdValue")).to_have_text("\u221230 dB")

        # Stop during audio startup must prevent a late connection in both modes.
        for voice in (True, False):
            page.evaluate("delayAudio = true")
            if voice:
                page.locator("#start").click()
            else:
                page.locator("#text").fill("Hei")
                page.locator("#send").click()
            page.wait_for_function("typeof resumeAudio === 'function'")
            expect(page.locator("#stop")).to_be_enabled()
            page.locator("#stop").click()
            page.evaluate("resumeAudio(); resumeAudio = undefined; delayAudio = false")
            page.wait_for_timeout(50)
            assert page.evaluate("connections.length") == 0
            expect(page.locator("#modelSettings")).to_be_enabled()
            expect(page.locator("#contextSize")).to_be_enabled()

        # Closing during the handshake must release the busy controls.
        page.evaluate("closeBeforeOpen = true")
        page.locator("#send").click()
        expect(page.locator("body")).to_have_attribute("data-state", "error")
        expect(page.locator("#send")).to_be_enabled()
        page.evaluate("closeBeforeOpen = false")

        # A saved transcript must load before another turn can start.
        page.locator("#historyDetails summary").click()
        page.locator("#savedChats").select_option(CHAT_ID)
        path = f"/api/chats/{CHAT_ID}"
        delays.add(path)
        page.locator("#openChat").click()
        page.wait_for_function("document.getElementById('send').disabled")
        expect(page.locator("#start")).to_be_disabled()
        expect(page.locator("#reviewChat")).to_be_disabled()
        held.pop(path).fulfill(json={"id": CHAT_ID, "messages": MESSAGES})
        delays.clear()
        expect(page.locator("#status")).to_contain_text("Saved chat reopened")
        # Stop before the first turn must still restore the reopened transcript.
        page.evaluate("stop()")
        page.locator("#text").fill("Hvordan går det?")
        page.locator("#send").click()
        page.wait_for_function("sent.length === 1")
        assert page.evaluate("sent[0].history") == MESSAGES
        page.evaluate("emit({type: 'transcript', text: 'Hvordan går det?'})")
        page.evaluate("emit({type: 'text', text: 'Det går bra.'})")
        page.evaluate("emit({type: 'done'})")
        expect(page.locator("#send")).to_be_enabled()

        # Restarting restores completed turns, but a new chat sends no history.
        page.locator("#stop").click()
        page.locator("#text").fill("Hva gjør du?")
        page.locator("#send").click()
        page.wait_for_function("sent.length === 2")
        assert page.evaluate("sent[1].history") == [
            *MESSAGES,
            {"role": "user", "content": "Hvordan går det?", "source": "text"},
            {"role": "assistant", "content": "Det går bra.", "source": "text"},
        ]
        page.locator("#newChat").click()
        page.locator("#text").fill("Hei")
        page.locator("#send").click()
        page.wait_for_function("sent.length === 3")
        assert page.evaluate("sent[2].history === undefined")

        # Starting voice must disable review while audio setup is pending.
        page.locator("#stop").click()
        page.evaluate("delayAudio = true")
        page.locator("#start").click()
        expect(page.locator("#reviewChat")).to_be_disabled()
        page.locator("#stop").click()
        page.evaluate("resumeAudio(); delayAudio = false")

        # Cancelled or failed archive loads must not replace the chat or leave controls busy.
        page.locator("#savedChats").select_option(CHAT_ID)
        delays.add(path)
        with page.expect_request(f"**{path}"):
            page.locator("#openChat").click()
        page.locator("#newChat").click()
        expect(page.locator("#text")).to_have_value("")
        held.pop(path).fulfill(json={"id": CHAT_ID, "messages": MESSAGES})
        page.wait_for_timeout(50)
        expect(page.locator(".message")).to_have_count(0)
        expect(page.locator("#send")).to_be_enabled()
        page.locator("#savedChats").select_option(CHAT_ID)
        with page.expect_request(f"**{path}"):
            page.locator("#openChat").click()
        held.pop(path).fulfill(status=404, json={"detail": "This saved chat no longer exists."})
        delays.clear()
        expect(page.locator("#historyStatus")).to_contain_text("no longer exists")
        expect(page.locator("#send")).to_be_enabled()
        expect(page.locator("#start")).to_be_enabled()

        # Conversation starters must remain reachable in short viewports.
        page.locator("#text").fill("Dette er et utkast.")
        page.locator("#newChat").click()
        expect(page.locator("#text")).to_have_value("")
        page.locator("#sidebarToggle").click()
        expect(page.locator("#sidebarToggle")).to_have_attribute("aria-expanded", "false")
        for width, height in ((1280, 600), (390, 700), (390, 560)):
            page.set_viewport_size({"width": width, "height": height})
            page.locator("#messages").evaluate(
                "element => element.scrollTo({top: 0, behavior: 'instant'})"
            )
            first = page.locator(".intro").bounding_box()
            messages = page.locator("#messages").bounding_box()
            assert first and messages and first["y"] >= messages["y"], (
                width,
                height,
                first,
                messages,
            )
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            if width < 768:
                assert page.locator("#settings").evaluate("element => element.clientWidth") == width
        assert not errors, errors
        page.evaluate("""
            localStorage.setItem('pratevenn.showInlineFeedback', 'false');
            localStorage.setItem('pratevenn.models', JSON.stringify({stt: 'stt'}));
        """)
        page.reload()
        expect(page.locator("#start")).to_be_enabled()
        expect(page.locator("#showInlineFeedback")).not_to_be_checked()
        expect(page.locator("#sttModel")).to_have_value("stt")

        check_hover_colors(page)
        check_theme(page)
        check_narrow_layout(page)
        check_text_contrast(page)
        check_hologram(page)
        assert not errors, errors
        browser.close()
    print(
        "Passed: initialization, startup cancellation, handshake failure, "
        "reopening, review, drafts, themes, hover colors, layout, contrast, and hologram response."
    )


if __name__ == "__main__":
    main()
