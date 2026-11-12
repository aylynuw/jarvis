#!/usr/bin/env python3
"""Runs Jarvis like Siri: it lives in the notch and the menu bar.

./run.sh               notch island + menu bar icon (dashboard opens from the menu bar)
./run.sh --dashboard   same, and open the dashboard window right away
./run.sh --browser     dashboard in your web browser (http://127.0.0.1:8765), no notch
"""

import argparse
import os
import threading
import time
import urllib.request
import webbrowser

import uvicorn

HOST, PORT = "127.0.0.1", 8765          # local only: nothing outside this Mac can reach it
URL = f"http://{HOST}:{PORT}/"
ISLAND_W, ISLAND_H = 640, 320          # the transparent window the drop-down HUD is drawn in


def serve() -> None:
    uvicorn.run("server:app", host=HOST, port=PORT, log_level="warning")


def wait_until_up(timeout: float = 30) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(URL + "api/state", timeout=1)
            return
        except Exception:
            time.sleep(0.2)
    raise SystemExit("The Jarvis server didn't start. Check the messages above.")


def post(path: str) -> None:
    def go():
        try:
            urllib.request.urlopen(urllib.request.Request(URL + path, method="POST"), timeout=5)
        except Exception:
            pass                                 # busy or voice unavailable: the island shows state anyway
    threading.Thread(target=go, daemon=True).start()


def notch_screen():
    """The screen with the notch (else the menu-bar screen), its frame, and the notch size."""
    import AppKit
    screens = list(AppKit.NSScreen.screens())
    screen = screens[0]
    for s in screens:
        if hasattr(s, "safeAreaInsets") and s.safeAreaInsets().top > 0:
            screen = s
            break
    frame = screen.frame()
    nw = nh = 0.0
    if hasattr(screen, "safeAreaInsets") and screen.safeAreaInsets().top > 0:
        nh = screen.safeAreaInsets().top
        nw = frame.size.width - screen.auxiliaryTopLeftArea().size.width - screen.auxiliaryTopRightArea().size.width
    if nw <= 0 or nh <= 0:                       # no notch: hang from the middle of the menu bar
        visible = screen.visibleFrame()
        nh = max(22.0, (frame.origin.y + frame.size.height) - (visible.origin.y + visible.size.height))
        nw = 160.0
    return screen, frame, int(round(nw)), int(round(nh))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true", help="open the dashboard in the web browser instead")
    parser.add_argument("--dashboard", action="store_true", help="open the dashboard window at start")
    args = parser.parse_args()

    threading.Thread(target=serve, daemon=True).start()
    wait_until_up()
    if args.browser:
        webbrowser.open(URL)
        print(f"JARVIS is running at {URL}  (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    import AppKit
    import webview
    from PyObjCTools import AppHelper

    _, frame, nw, nh = notch_screen()
    quitting = {"now": False}

    dashboard = webview.create_window(
        "JARVIS", URL, width=1360, height=860, min_size=(380, 600),
        background_color="#05080D", hidden=not args.dashboard)

    def keep_dashboard(*_):
        """Closing the dashboard hides it; Jarvis keeps listening from the notch."""
        if quitting["now"]:
            return True
        dashboard.hide()
        return False
    dashboard.events.closing += keep_dashboard

    def open_dashboard():
        dashboard.show()
        AppHelper.callAfter(lambda: AppKit.NSApp.activateIgnoringOtherApps_(True))

    class IslandApi:
        """Called from ui/island.js. Hidden means invisible and click-through, not closed,
        so the page keeps its socket and reacts the moment the wake word fires."""

        def show(self, interactive=False):
            def go():
                win = island.native
                win.setAlphaValue_(1.0)
                win.setIgnoresMouseEvents_(not interactive)
                win.orderFrontRegardless()
            AppHelper.callAfter(go)

        def hide(self):
            def go():
                win = island.native
                win.setAlphaValue_(0.0)
                win.setIgnoresMouseEvents_(True)
            AppHelper.callAfter(go)

        def open_dashboard(self):
            open_dashboard()

    island = webview.create_window(
        "JARVIS 노치", f"{URL}island.html?nw={nw}&nh={nh}", js_api=IslandApi(),
        width=ISLAND_W, height=ISLAND_H, frameless=True, easy_drag=False, transparent=True,
        on_top=True, focus=False, shadow=False, resizable=False, hidden=True,
        background_color="#000000")

    # ---- menu bar icon
    class MenuTarget(AppKit.NSObject):
        def openDashboard_(self, _):
            open_dashboard()

        def talk_(self, _):
            post("api/listen")

        def quit_(self, _):
            quitting["now"] = True
            post("api/stop")
            AppHelper.callLater(0.3, lambda: os._exit(0))

    keep = {}                                    # Objective-C objects must outlive this function

    def setup_native():
        app = AppKit.NSApp
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)  # no Dock icon, like Siri

        win = island.native
        win.setStyleMask_(AppKit.NSWindowStyleMaskBorderless)     # borderless may sit over the menu bar
        win.setLevel_(AppKit.NSStatusWindowLevel + 2)
        win.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle)
        win.setOpaque_(False)
        win.setHasShadow_(False)
        win.setBackgroundColor_(AppKit.NSColor.clearColor())
        x = frame.origin.x + frame.size.width / 2 - ISLAND_W / 2
        y = frame.origin.y + frame.size.height - ISLAND_H
        win.setFrame_display_(AppKit.NSMakeRect(x, y, ISLAND_W, ISLAND_H), True)
        win.setAlphaValue_(0.0)
        win.setIgnoresMouseEvents_(True)
        win.orderFrontRegardless()

        item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_("circle.hexagongrid", "JARVIS")
        if image is not None:
            image.setTemplate_(True)
            item.button().setImage_(image)
        else:
            item.button().setTitle_("J")
        target = MenuTarget.alloc().init()
        menu = AppKit.NSMenu.alloc().init()
        for title, action, key in (("대시보드 열기", "openDashboard:", "d"), ("지금 말하기", "talk:", "")):
            entry = menu.addItemWithTitle_action_keyEquivalent_(title, action, key)
            entry.setTarget_(target)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        entry = menu.addItemWithTitle_action_keyEquivalent_("JARVIS 종료", "quit:", "q")
        entry.setTarget_(target)
        item.setMenu_(menu)
        keep.update(item=item, target=target, menu=menu)
        if args.dashboard:
            open_dashboard()

    def on_start():
        island.events.loaded.wait(15)
        AppHelper.callAfter(setup_native)

    webview.start(on_start)


if __name__ == "__main__":
    main()
