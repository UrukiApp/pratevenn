"""Exercise the real voice loop with a synthetic Norwegian microphone.

Run against ``pratevenn start --model-dir .models`` after installing Playwright Chromium:
    PLAYWRIGHT_BROWSERS_PATH=.cache/playwright .venv/bin/python scripts/check_browser.py
"""

import io
import json
import re
import tempfile
import wave
from pathlib import Path

from piper import PiperVoice
from playwright.sync_api import expect, sync_playwright
from pratevenn.models import SYSTEM_PROMPT, VOICE_FILE


def main() -> None:
    voice = PiperVoice.load(str(Path(".models/tts") / VOICE_FILE))
    spoken = io.BytesIO()
    with wave.open(spoken, "wb") as output:
        voice.synthesize_wav("Hei! Jeg lærer norsk. Kan vi snakke om været?", output)
    with wave.open(io.BytesIO(spoken.getvalue()), "rb") as audio:
        rate = audio.getframerate()
        speech = audio.readframes(audio.getnframes())

    with tempfile.TemporaryDirectory(prefix="pratevenn-browser-") as directory:
        microphone = Path(directory) / "microphone.wav"
        with wave.open(str(microphone), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(
                bytes(rate * 2) + speech + bytes(rate * 2 * 18) + speech + bytes(rate * 2 * 60)
            )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                args=[
                    "--use-fake-ui-for-media-stream",
                    "--use-fake-device-for-media-stream",
                    f"--use-file-for-fake-audio-capture={microphone}%noloop",
                ]
            )
            context = browser.new_context(viewport={"width": 1280, "height": 1000})
            context.add_init_script("""
                window.capturedTracks = [];
                const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
                navigator.mediaDevices.getUserMedia = async (...args) => {
                    const stream = await original(...args);
                    window.capturedTracks.push(...stream.getTracks());
                    return stream;
                };
            """)
            page = context.new_page()
            errors, events, requests, sent = [], [], [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.url))
            page.on(
                "websocket",
                lambda ws: ws.on("framereceived", lambda frame: events.append(json.loads(frame))),
            )
            page.on(
                "websocket",
                lambda ws: ws.on("framesent", lambda frame: sent.append(json.loads(frame))),
            )
            page.goto("http://127.0.0.1:8000")
            expect(page.locator("#start")).to_be_enabled()
            expect(page.locator("#hologram")).to_be_visible()
            expect(page.locator("#hologram")).to_have_attribute("aria-hidden", "true")
            expect(page.locator("#ready")).to_have_text(re.compile(r"^(CPU|GPU) · Models ready$"))
            expect(page.locator("#level, #topic")).to_have_count(0)
            page.locator("#historyDetails summary").click()
            expect(page.locator("#saveChats")).not_to_be_checked()
            page.locator("#saveChats").check()
            page.locator("#modelDetails summary").click()
            for kind in ("stt", "llm", "tts"):
                expect(page.locator(f"#{kind}Model")).to_be_enabled()
                assert page.locator(f"#{kind}Model option").count() >= 1
            page.locator("#promptDetails summary").click()
            expect(page.locator("#systemPrompt")).to_have_value(SYSTEM_PROMPT)
            custom_prompt = SYSTEM_PROMPT + " Still et spørsmål om været."
            page.locator("#systemPrompt").fill(custom_prompt)
            page.screenshot(path=".cache/pratevenn-desktop.png", full_page=True)
            page.locator("#start").click()
            expect(page.locator("body")).to_have_attribute("data-state", "listening")
            expect(page.locator(".intro")).to_be_hidden()
            expect(page.locator("#status")).to_be_visible()
            expect(page.locator("#stop")).to_be_visible()
            expect(page.locator("#llmModel")).to_be_disabled()
            expect(page.locator(".message.user")).to_have_count(1, timeout=45000)
            expect(page.locator("body")).to_have_attribute("data-state", "speaking", timeout=45000)
            page.wait_for_function(
                "Number(document.getElementById('hologram').style.getPropertyValue('--voice')) > 0"
            )
            expect(page.locator("#hologram")).to_be_visible()
            assert page.evaluate("capturedTracks.every(track => !track.enabled)")
            expect(page.locator("body")).to_have_attribute("data-state", "listening", timeout=45000)
            assert page.evaluate("capturedTracks.at(-1).enabled")
            expect(page.locator(".message.user")).to_have_count(2, timeout=45000)
            expect(page.locator("body")).to_have_attribute("data-state", "listening", timeout=45000)
            page.wait_for_timeout(1800)
            assert page.locator(".message.user").count() == 2, "Silence triggered a turn"
            print("Voice transcripts:", page.locator(".message.user .content").all_text_contents())
            print(
                "First-audio seconds:",
                [e.get("first_audio_seconds") for e in events if e["type"] == "done"],
            )
            assert len([e for e in events if e["type"] == "audio"]) >= 2
            expect(page.locator("#savedChats option")).to_have_count(2)
            expect(page.locator("#contextStatus")).to_contain_text("% used")
            assert any(e["type"] == "context" and e["tokens"] > 0 for e in events)
            assert all(e["system_prompt"] == custom_prompt for e in sent)
            assert all("level" not in e and "topic" not in e for e in sent)
            page.screenshot(path=".cache/pratevenn-conversation.png", full_page=True)

            page.locator("#stop").click()
            assert (
                page.locator("#hologram").evaluate(
                    "element => Number(element.style.getPropertyValue('--voice'))"
                )
                == 0
            )
            expect(page.locator("#messages")).to_be_visible()
            expect(page.locator("#llmModel")).to_be_enabled()
            assert page.evaluate("capturedTracks.every(track => track.readyState === 'ended')")
            edited_prompt = SYSTEM_PROMPT + " Spør gjerne om mat."
            page.locator("#systemPrompt").fill(edited_prompt)
            page.locator("#text").fill("Hei! Jeg vil snakke om mat.")
            page.locator("#send").click()
            expect(page.locator(".message.user")).to_have_count(3, timeout=10000)
            assert sent[-1]["system_prompt"] == edited_prompt
            expect(page.locator("#systemPrompt")).to_be_enabled()
            page.locator("#resetPrompt").click()
            expect(page.locator("#systemPrompt")).to_have_value(SYSTEM_PROMPT)
            page.locator("#stop").click()
            stopped_text = page.locator("#messages").inner_text()
            page.wait_for_timeout(1800)
            assert page.locator("#messages").inner_text() == stopped_text, "Late output after Stop"
            page.locator("#start").click()
            expect(page.locator("body")).to_have_attribute("data-state", "listening")
            page.locator("#stop").click()
            page.locator("#savedChats").select_option(index=1)
            page.locator("#openChat").click()
            expect(page.locator("#status")).to_contain_text("Saved chat reopened")
            reviewed_turns = page.locator(".message.user").count()
            assert reviewed_turns >= 2
            page.locator("#reviewDetails summary").click()
            page.locator("#reviewChat").click()
            expect(page.locator("#reviewStatus")).to_contain_text(
                f"{reviewed_turns} turns reviewed", timeout=60000
            )
            assert sent[-1]["type"] == "review"
            assert len(sent[-1]["history"]) == reviewed_turns * 2
            assert page.evaluate("capturedTracks.every(track => track.readyState === 'ended')")
            page.locator("#stop").click()
            page.locator("#newChat").click()
            expect(page.locator(".message")).to_have_count(0)
            page.on("dialog", lambda dialog: dialog.accept())
            page.locator("#deleteAllChats").click()
            expect(page.locator("#savedChats option")).to_have_count(1)
            expect(page.locator("#saveChats")).not_to_be_checked()
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("link", name="Settings", exact=True).click()
            page.get_by_text("Speech settings", exact=True).click()
            expect(page.locator("#pause")).to_be_visible()
            expect(page.locator("#threshold")).to_be_visible()
            page.locator("#pause").focus()
            page.keyboard.press("ArrowRight")
            expect(page.locator("#pauseValue")).to_have_text("1.3 s")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.emulate_media(reduced_motion="reduce")
            assert (
                page.locator(".holo-orbit").first.evaluate(
                    "element => getComputedStyle(element).animationName"
                )
                == "none"
            )
            assert (
                page.locator(".holo-core").evaluate(
                    "element => getComputedStyle(element).transform"
                )
                == "none"
            )
            page.screenshot(path=".cache/pratevenn-mobile.png", full_page=True)
            assert not errors, errors
            assert all(url.startswith("http://127.0.0.1:8000/") for url in requests), requests

            denied = context.new_page()
            denied.add_init_script("""
                navigator.mediaDevices.getUserMedia = async () => {
                    throw new DOMException('denied', 'NotAllowedError');
                };
            """)
            denied.goto("http://127.0.0.1:8000")
            denied.locator("#start").click()
            expect(denied.locator("#status")).to_contain_text("Microphone access was denied")
            expect(denied.locator("#send")).to_be_enabled()
            browser.close()
    print(
        "Passed: two automatic voice turns, playback gating, stop/restart, "
        "prompt editing and reset, permission denial, mobile layout, hologram audio response, "
        "reduced motion, saved chats, reopening, practice review, context usage, and deletion."
    )


if __name__ == "__main__":
    main()
