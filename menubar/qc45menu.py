#!/usr/bin/env python3
"""QC45 menu bar control.

Bluetooth lives on the main thread because IOBluetooth delivers its callbacks
to the main run loop; a worker thread cannot establish the baseband link.
Commands therefore pump the run loop while waiting for a reply, which keeps
the UI alive and takes about 75 ms per command instead of the 0.7 s the
library's fixed-sleep path costs.
"""

from __future__ import annotations

import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# pybmap ships one level up, next to the CLI tool.
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import objc  # noqa: E402
from Foundation import (  # noqa: E402
    NSObject, NSMakeRect, NSRunLoop, NSDate, NSDefaultRunLoopMode,
    NSTimer, NSMakeSize,
)
from AppKit import (  # noqa: E402
    NSApplication, NSApplicationActivationPolicyAccessory, NSStatusBar,
    NSVariableStatusItemLength, NSPopover, NSPopoverBehaviorApplicationDefined, NSEvent,
    NSLeftMouseDownMask, NSRightMouseDownMask,
    NSView, NSViewController, NSSlider, NSTextField, NSButton, NSColor,
    NSFont, NSImage, NSBezelStyleRounded, NSSliderTypeLinear, NSBox, NSButtonTypePushOnPushOff, NSMinYEdge,
    NSBoxSeparator, NSEventMaskLeftMouseUp, NSPopUpButton, NSButtonTypeSwitch,
)

import pybmap  # noqa: E402
from i18n import T, current_language, load_language, set_language  # noqa: E402
from qc45_settings import (  # noqa: E402
    DeviceSettings, SettingsError, SIDETONE_OPTIONS, STANDBY_OPTIONS,
    option_label, voice_language_name,
)

LOG_PATH = os.path.expanduser("~/.qc45/app.log")


def log(msg: str) -> None:
    """Append a line to ~/.qc45/app.log so the bundled app can be diagnosed
    without a terminal attached."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


_MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


def normalize_mac(value: str) -> str:
    text = (value or "").strip().upper().replace("-", ":")
    if not _MAC_RE.match(text):
        raise SettingsError(T("耳机地址格式不正确", "The headphone address is not valid"))
    return text


def select_qc45(candidates, override=None):
    """Pick a QC45 address. A connected pair wins over a paired one."""
    if override:
        return normalize_mac(override)
    found = []
    for mac, device_type, connected in candidates:
        if device_type == "qc45":
            found.append((normalize_mac(mac), bool(connected)))
    for mac, connected in found:
        if connected:
            return mac
    return found[0][0] if found else None


def find_qc45_address():
    try:
        from IOBluetooth import IOBluetoothDevice
        from pybmap.catalog import lookup_device
        devices = IOBluetoothDevice.pairedDevices() or []
    except Exception:
        return None
    found = []
    for device in devices:
        try:
            entry = lookup_device(device.productID())
            if not entry or entry.config != "qc45":
                continue
            found.append((str(device.getAddressString()), "qc45", bool(device.isConnected())))
        except Exception:
            continue
    try:
        return select_qc45(found)
    except SettingsError:
        return None


def resolve_qc45_mac(explicit=None):
    chosen = explicit if explicit else os.environ.get("QC45_MAC")
    if chosen:
        return normalize_mac(chosen)
    return find_qc45_address()


FB_MODE_CONFIG, FN_MODE_CONFIG, FN_CURRENT_MODE = 31, 6, 3
OP_GET, OP_SETGET, OP_START = 1, 2, 5

EDITABLE_SLOTS = (2, 3)
CNC_MIN, CNC_MAX = 0, 10
EQ_MIN, EQ_MAX = -10, 10
EQ_BANDS = (("Bass", 0), ("Mid", 1), ("Treble", 2))
# Global 3-band curves. A click writes them; opening the panel never does.
PRESET_TEXT = {
    "hiphop": ("说唱", "Hip-Hop"),
    "pop": ("流行", "Pop"),
    "rock": ("摇滚", "Rock"),
}
EQ_PRESETS = (
    ("hiphop", (6, 1, -2)),
    ("pop", (4, 2, -2)),
    ("rock", (2, 3, -1)),
)
MODE_TEXT = {
    0: ("消噪", "Quiet"),
    1: ("通透", "Aware"),
    2: ("自定义 1", "Custom 1"),
    3: ("自定义 2", "Custom 2"),
}

POP_W, POP_H = 360, 628


class Link(DeviceSettings):
    """Thin BMAP link over one persistent RFCOMM channel."""

    def __init__(self, mac: str | None = None):
        self.mac = mac
        self.dev = None
        self.channel = None
        self.queue = None
        self._io_busy = False
        super().__init__(self._exchange)

    @property
    def connected(self) -> bool:
        return (self.channel is not None and self.dev is not None
                and not self.dev._transport.delegate.closed_event.is_set())

    def connect(self) -> None:
        self.close()
        mac = resolve_qc45_mac(self.mac)
        if not mac:
            raise SettingsError(T(
                "没有找到已配对的 QC45。请先在系统设置中连接耳机。",
                "No paired QC45 found. Connect the headphones in System Settings first."))
        self.mac = mac
        self.dev = pybmap.connect(mac=self.mac, device_type="qc45")
        tr = self.dev._transport
        self.channel = tr.channel
        self.queue = tr.delegate.received_queue

    def close(self) -> None:
        if self.dev is not None:
            try:
                self.dev.close()
            except Exception:
                pass
        self.dev = None
        self.channel = None
        self.queue = None

    def _drain(self) -> None:
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except Exception:
                return

    def _exchange(self, packet: bytes, timeout: float = 1.5,
                  settle: float = 0.02) -> bytes:
        if not self.connected:
            raise SettingsError(T("耳机未连接", "Headphones are not connected"))
        if self._io_busy:
            raise SettingsError(T("正在读取耳机，请稍候", "Reading the headphones, please wait"))
        self._io_busy = True
        try:
            self._drain()
            status = self.channel.writeSync_length_(packet, len(packet))
            if status != 0:
                raise SettingsError(T("蓝牙发送失败 (%d)" % status, "Bluetooth send failed (%d)" % status))
            data = b""
            last_at = None
            matched = False
            deadline = time.monotonic() + timeout
            rl = NSRunLoop.currentRunLoop()
            while time.monotonic() < deadline:
                rl.runMode_beforeDate_(NSDefaultRunLoopMode,
                                       NSDate.dateWithTimeIntervalSinceNow_(0.002))
                while not self.queue.empty():
                    data += bytes(self.queue.get_nowait())
                    last_at = time.monotonic()
                matched = any((f.fblock, f.func) == tuple(packet[:2])
                              for f in pybmap.parse_all_responses(data))
                if matched and last_at is not None and time.monotonic() - last_at > settle:
                    break
                if not self.connected:
                    raise SettingsError(T("耳机连接已断开", "Headphone connection closed"))
            return data
        finally:
            self._io_busy = False


def _label(text: str, x: float, y: float, w: float, h: float,
           size: float = 12, bold: bool = False, color=None):
    f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    f.setStringValue_(text)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setEditable_(False)
    f.setSelectable_(False)
    f.setFont_(NSFont.boldSystemFontOfSize_(size) if bold
               else NSFont.systemFontOfSize_(size))
    if color is not None:
        f.setTextColor_(color)
    return f


def _slider(x: float, y: float, w: float, lo: float, hi: float, ticks: int):
    s = NSSlider.alloc().initWithFrame_(NSMakeRect(x, y, w, 24))
    s.setSliderType_(NSSliderTypeLinear)
    s.setMinValue_(lo)
    s.setMaxValue_(hi)
    s.setNumberOfTickMarks_(ticks)
    s.setAllowsTickMarkValuesOnly_(True)
    s.setContinuous_(True)
    return s


class ControlView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(ControlView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.ctrl = None
        return self

    def setupWith_(self, ctrl):
        self.ctrl = ctrl
        W = POP_W
        y = POP_H

        y -= 34
        self.title = _label("Bose QC45", 16, y, 200, 20, 15, True)
        self.addSubview_(self.title)
        self.battery = _label("--", W - 78, y, 62, 20, 12, False, NSColor.secondaryLabelColor())
        self.battery.setAlignment_(2)
        self.addSubview_(self.battery)

        y -= 30
        self.cnc_caption = _label("", 16, y, 220, 16, 10, True, NSColor.secondaryLabelColor())
        self.addSubview_(self.cnc_caption)
        self.cnc_value = _label("--", W - 56, y - 6, 40, 28, 22, True)
        self.cnc_value.setAlignment_(2)
        self.addSubview_(self.cnc_value)

        y -= 26
        self.cnc = _slider(16, y, W - 32, CNC_MIN, CNC_MAX, 11)
        self.cnc.setTarget_(ctrl)
        self.cnc.setAction_(b"cncChanged:")
        self.addSubview_(self.cnc)

        y -= 16
        self.cnc_low = _label("", 16, y, 100, 12, 9, False, NSColor.tertiaryLabelColor())
        self.addSubview_(self.cnc_low)
        self.cnc_high = _label("", W - 116, y, 100, 12, 9, False, NSColor.tertiaryLabelColor())
        self.cnc_high.setAlignment_(2)
        self.addSubview_(self.cnc_high)

        y -= 34
        self.eq_caption = _label("", 16, y, 200, 16, 10, True, NSColor.secondaryLabelColor())
        self.addSubview_(self.eq_caption)
        self.eq_sliders = {}
        self.eq_values = {}
        self.band_labels = {}
        for _name, bid in EQ_BANDS:
            y -= 30
            label = _label("", 16, y + 4, 54, 16, 12)
            self.addSubview_(label)
            self.band_labels[bid] = label
            value = _label("0", 64, y + 2, 30, 16, 12, True)
            value.setAlignment_(2)
            self.addSubview_(value)
            self.eq_values[bid] = value
            slider = _slider(100, y, W - 116, EQ_MIN, EQ_MAX, 21)
            slider.setTag_(bid)
            slider.setTarget_(ctrl)
            slider.setAction_(b"eqChanged:")
            self.addSubview_(slider)
            self.eq_sliders[bid] = slider

        y -= 32
        self.preset_buttons = {}
        bw = (W - 32 - 2 * 6) / 3.0
        for i, (_key, _values) in enumerate(EQ_PRESETS):
            button = NSButton.alloc().initWithFrame_(
                NSMakeRect(16 + i * (bw + 6), y, bw, 26))
            button.setBezelStyle_(NSBezelStyleRounded)
            button.setButtonType_(NSButtonTypePushOnPushOff)
            button.setTag_(i)
            button.setTarget_(ctrl)
            button.setAction_(b"presetClicked:")
            self.addSubview_(button)
            self.preset_buttons[i] = button

        y -= 40
        self.mode_caption = _label("", 16, y, 100, 16, 10, True, NSColor.secondaryLabelColor())
        self.addSubview_(self.mode_caption)
        y -= 30
        self.mode_buttons = {}
        bw = (W - 32 - 3 * 6) / 4.0
        for i, slot in enumerate(MODE_TEXT):
            button = NSButton.alloc().initWithFrame_(
                NSMakeRect(16 + i * (bw + 6), y, bw, 26))
            button.setBezelStyle_(NSBezelStyleRounded)
            button.setButtonType_(NSButtonTypePushOnPushOff)
            button.setTag_(slot)
            button.setTarget_(ctrl)
            button.setAction_(b"modeClicked:")
            self.addSubview_(button)
            self.mode_buttons[slot] = button

        y -= 38
        self.settings_caption = _label("", 16, y, 180, 16, 10, True, NSColor.secondaryLabelColor())
        self.addSubview_(self.settings_caption)
        y -= 32
        self.wind_label = _label("", 16, y + 3, 100, 18)
        self.addSubview_(self.wind_label)
        self.wind = NSButton.alloc().initWithFrame_(NSMakeRect(122, y, 110, 24))
        self.wind.setButtonType_(NSButtonTypeSwitch)
        self.wind.setTarget_(ctrl)
        self.wind.setAction_(b"windChanged:")
        self.addSubview_(self.wind)
        y -= 18
        self.wind_note = _label("", 16, y, W - 32, 14, 10, color=NSColor.secondaryLabelColor())
        self.addSubview_(self.wind_note)

        y -= 30
        self.sidetone_label = _label("", 16, y + 3, 100, 18)
        self.addSubview_(self.sidetone_label)
        self.sidetone = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(122, y - 1, W - 138, 26), False)
        self.sidetone.setTarget_(ctrl)
        self.sidetone.setAction_(b"sidetoneChanged:")
        self.addSubview_(self.sidetone)

        y -= 30
        self.prompts_label = _label("", 16, y + 3, 100, 18)
        self.addSubview_(self.prompts_label)
        self.prompts = NSButton.alloc().initWithFrame_(NSMakeRect(122, y, 72, 24))
        self.prompts.setButtonType_(NSButtonTypeSwitch)
        self.prompts.setTarget_(ctrl)
        self.prompts.setAction_(b"promptsChanged:")
        self.addSubview_(self.prompts)
        self.language = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(196, y - 1, W - 212, 26), False)
        self.language.setTarget_(ctrl)
        self.language.setAction_(b"languageChanged:")
        self.addSubview_(self.language)

        y -= 30
        self.standby_label = _label("", 16, y + 3, 100, 18)
        self.addSubview_(self.standby_label)
        self.standby = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(122, y - 1, W - 138, 26), False)
        self.standby.setTarget_(ctrl)
        self.standby.setAction_(b"standbyChanged:")
        self.addSubview_(self.standby)

        y -= 30
        self.ui_lang_label = _label("", 16, y + 3, 100, 18)
        self.addSubview_(self.ui_lang_label)
        self.ui_lang = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(122, y - 1, W - 138, 26), False)
        self.ui_lang.addItemWithTitle_("中文")
        self.ui_lang.lastItem().setTag_(0)
        self.ui_lang.addItemWithTitle_("English")
        self.ui_lang.lastItem().setTag_(1)
        self.ui_lang.setTarget_(ctrl)
        self.ui_lang.setAction_(b"uiLanguageChanged:")
        self.addSubview_(self.ui_lang)

        y -= 28
        self.status = _label("", 16, y, W - 32, 18, 10, False, NSColor.secondaryLabelColor())
        self.addSubview_(self.status)
        y -= 34
        self.reconnect = NSButton.alloc().initWithFrame_(NSMakeRect(W - 210, y - 4, 110, 24))
        self.reconnect.setBezelStyle_(NSBezelStyleRounded)
        self.reconnect.setTarget_(ctrl)
        self.reconnect.setAction_(b"reconnectClicked:")
        self.addSubview_(self.reconnect)
        self.quit_button = NSButton.alloc().initWithFrame_(NSMakeRect(W - 94, y - 4, 78, 24))
        self.quit_button.setBezelStyle_(NSBezelStyleRounded)
        self.quit_button.setTarget_(ctrl)
        self.quit_button.setAction_(b"quitClicked:")
        self.addSubview_(self.quit_button)
        self.status.setStringValue_(T("正在连接…", "Connecting…"))
        self.apply_language()

    def apply_language(self):
        self.cnc_caption.setStringValue_(T("降噪 / 环境音 · CNC", "Noise control · CNC"))
        self.cnc_low.setStringValue_(T("最强降噪", "Most quiet"))
        self.cnc_high.setStringValue_(T("最大环境音", "Most aware"))
        self.eq_caption.setStringValue_(T("均衡器", "Equalizer"))
        for bid, pair in ((0, ("低频", "Bass")), (1, ("中频", "Mid")), (2, ("高频", "Treble"))):
            self.band_labels[bid].setStringValue_(T(*pair))
        for i, (key, values) in enumerate(EQ_PRESETS):
            button = self.preset_buttons[i]
            button.setTitle_(T(*PRESET_TEXT[key]))
            button.setToolTip_(T(
                "低音 %+d · 中频 %+d · 高频 %+d" % values,
                "Bass %+d · Mid %+d · Treble %+d" % values))
        self.mode_caption.setStringValue_(T("模式", "Mode"))
        for slot, pair in MODE_TEXT.items():
            self.mode_buttons[slot].setTitle_(T(*pair))
        self.settings_caption.setStringValue_(T("耳机设置", "Headphone settings"))
        self.wind_label.setStringValue_(T("抗风噪", "Wind block"))
        self.wind.setTitle_(T("开启", "On"))
        self.sidetone_label.setStringValue_(T("通话自声", "Sidetone"))
        self.sidetone.setToolTip_(T(
            "通话时，调整耳机中听见自己声音的程度",
            "How much of your own voice you hear during calls"))
        self.prompts_label.setStringValue_(T("语音提示", "Voice prompts"))
        self.prompts.setTitle_(T("开启", "On"))
        self.language.setToolTip_(T(
            "选择耳机自带语言；不会自动开启已关闭的语音提示",
            "Choose a built-in prompt language. This does not turn prompts back on."))
        self.standby_label.setStringValue_(T("自动待机", "Auto standby"))
        self.standby.setToolTip_(T(
            "耳机闲置多久后自动待机，以节省电量",
            "How long the headphones stay idle before standby"))
        self.ui_lang_label.setStringValue_(T("界面语言", "Language"))
        self.ui_lang.selectItemWithTag_(0 if current_language() == "zh-Hans" else 1)
        self.reconnect.setTitle_(T("重新连接", "Reconnect"))
        self.quit_button.setTitle_(T("退出", "Quit"))

    # keep the sliders from stealing keyboard focus
    def acceptsFirstResponder(self):
        return False


class AppController(NSObject):
    def init(self):
        self = objc.super(AppController, self).init()
        if self is None:
            return None
        self.link = Link()
        self.view = None
        self.popover = None
        self.status_item = None
        self.pending = {}
        self.timer = None
        self.retry_timer = None
        self.hb_timer = None
        self.outside_monitor = None
        self.slot = 2
        self.mode_target = None
        self.busy = False
        self.features = {}
        self.slot_info = None
        return self

    # ---------------------------------------------------------- bootstrap
    def applicationDidFinishLaunching_(self, note):
        load_language()
        bar = NSStatusBar.systemStatusBar()
        self.status_item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        button = self.status_item.button()
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "headphones", "QC45")
        if img is not None:
            img.setTemplate_(True)
            button.setImage_(img)
        else:
            button.setTitle_("QC45")
        button.setTarget_(self)
        button.setAction_(b"togglePopover:")

        vc = NSViewController.alloc().init()
        v = ControlView.alloc().initWithFrame_(NSMakeRect(0, 0, POP_W, POP_H))
        v.setupWith_(self)
        self.view = v
        vc.setView_(v)

        pop = NSPopover.alloc().init()
        pop.setContentViewController_(vc)
        pop.setBehavior_(NSPopoverBehaviorApplicationDefined)
        pop.setContentSize_(NSMakeSize(POP_W, POP_H))
        self.popover = pop
        self._install_outside_close()

        b = self.status_item.button()
        log("status ready target=%r action=%r visible=%s" % (
            b.target(), b.action(), self.status_item.isVisible()))
        self._connect_and_load()

    def togglePopover_(self, sender):
        log("toggle invoked")
        try:
            if self.popover.isShown():
                self.popover.performClose_(sender)
                log("popover closed")
                return
            try:
                self._refresh()
            except Exception as e:
                log("refresh in toggle failed: %r" % e)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            button = self.status_item.button()
            self.popover.showRelativeToRect_ofView_preferredEdge_(
                button.bounds(), button, NSMinYEdge)
            log("popover shown=%s" % self.popover.isShown())
        except Exception as e:
            log("toggle failed: %r" % e)

    # ------------------------------------------------------------ link ops
    @objc.python_method
    def _status(self, text: str):
        if self.view is not None:
            self.view.status.setStringValue_(text)
            self.view.status.setToolTip_(text)

    @objc.python_method
    def _connect_and_load(self):
        if self.busy:
            return
        self.busy = True
        self._status(T("正在连接…", "Connecting…"))
        self._set_controls_enabled(False)
        try:
            self.link.connect()
            log("connected")
            if self.retry_timer is not None:
                self.retry_timer.invalidate()
                self.retry_timer = None
            self._start_heartbeat()
        except Exception as e:
            log("connect failed: %s" % e)
            self._schedule_retry()
            self._status(T("耳机未连接，将自动重试", "Headphones not connected. Retrying automatically."))
            self.view.title.setStringValue_(T("Bose QC45 · 未连接", "Bose QC45 · Not connected"))
            return
        finally:
            self.busy = False
        self.view.title.setStringValue_("Bose QC45")
        self._refresh()

    @objc.python_method
    def _populate_popup(self, popup, options, selected):
        popup.removeAllItems()
        options = list(options)
        if selected not in [x[0] for x in options]:
            options.append((selected, T("当前值 %s" % selected, "Current value %s" % selected)))
        for value, title in options:
            popup.addItemWithTitle_(title)
            popup.lastItem().setTag_(value)
        popup.selectItemWithTag_(selected)

    @objc.python_method
    def _refresh(self):
        if self.busy or self.pending or not self.link.connected:
            return
        self.busy = True
        try:
            # Reads pump AppKit callbacks. Stage the snapshot, then discard it
            # if a new gesture arrived so an old value cannot reset its control.
            battery = self.link.battery()
            slot = self.link.current_slot()
            info = self.link.read_slot(slot)
            eq = self.link.read_eq()
            features = {}
            for name, read in (("voice", self.link.read_voice),
                               ("sidetone", self.link.read_sidetone),
                               ("standby", self.link.read_standby)):
                try:
                    features[name] = read()
                except Exception as e:
                    log("%s unavailable: %s" % (name, e))
                    if not self.link.connected:
                        raise SettingsError(T("耳机连接已断开", "Headphone connection closed")) from e
            if not self.link.connected:
                raise SettingsError(T("耳机连接已断开", "Headphone connection closed"))
            if self.pending:
                return
            self.view.battery.setStringValue_("%d%%" % battery)
            self.slot = slot
            self._sync_mode_buttons(slot)
            self.slot_info = info
            self.view.cnc.setDoubleValue_(float(info["cnc"]))
            self.view.cnc_value.setStringValue_(str(info["cnc"]))
            self.view.wind.setState_(int(info["wind"]))
            for bid, value in eq.items():
                if bid in self.view.eq_sliders:
                    self.view.eq_sliders[bid].setDoubleValue_(float(value))
                    self.view.eq_values[bid].setStringValue_("%+d" % value)
            self._sync_preset_buttons()
            self.features = features
            self._apply_feature_labels()
            self._set_controls_enabled(True)
            self._status(T("已连接 · 修改后自动保存", "Connected · changes save automatically") if len(self.features) == 3 else T("已连接 · 部分设置暂不可用", "Connected · some settings are unavailable"))
        except Exception as e:
            log("refresh failed: %s" % e)
            self._set_controls_enabled(False)
            self._status(T("读取失败：%s" % e, "Could not read settings: %s" % e))
            if not self.link.connected:
                self.features = {}
                self.slot_info = None
                self.view.title.setStringValue_(T("Bose QC45 · 未连接", "Bose QC45 · Not connected"))
                self.view.battery.setStringValue_("--")
                self._schedule_retry()
        finally:
            self.busy = False


    @objc.python_method
    def _apply_feature_labels(self):
        voice = self.features.get("voice")
        if voice:
            self.view.prompts.setState_(int(voice["enabled"]))
            self._populate_popup(self.view.language,
                [(i, voice_language_name(i)) for i in voice["languages"]],
                voice["language"])
        side = self.features.get("sidetone")
        if side:
            self._populate_popup(self.view.sidetone,
                [(item[0], option_label(item)) for item in SIDETONE_OPTIONS if item[0] in side["supported"]],
                side["level"])
        standby = self.features.get("standby")
        if standby:
            limit = 65535 if standby["wide"] else 255
            self._populate_popup(self.view.standby,
                [(item[0], option_label(item)) for item in STANDBY_OPTIONS if item[0] <= limit],
                standby["minutes"])

    @objc.python_method
    def _install_outside_close(self):
        """Dismiss the popover when the user clicks anywhere outside it.

        With NSPopoverBehaviorApplicationDefined AppKit stops doing this for
        us, which is what keeps the panel up even when the accessory app does
        not hold focus.
        """
        def handler(event):
            try:
                if not self.popover.isShown():
                    return
                loc = event.locationInWindow()  # screen coords for global monitors
                x, y = loc.x, loc.y
                button = self.status_item.button()
                bw = button.window()
                if bw is not None:
                    f = bw.frame()
                    if (f.origin.x <= x <= f.origin.x + f.size.width and
                            f.origin.y <= y <= f.origin.y + f.size.height):
                        return  # let the toggle handle its own click
                pw = self.popover.contentViewController().view().window()
                if pw is not None:
                    f = pw.frame()
                    if (f.origin.x <= x <= f.origin.x + f.size.width and
                            f.origin.y <= y <= f.origin.y + f.size.height):
                        return  # click inside the panel
                self.popover.performClose_(None)
            except Exception as e:
                log("outside-close error: %r" % e)

        self.outside_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSLeftMouseDownMask | NSRightMouseDownMask, handler)

    @objc.python_method
    def _schedule_retry(self):
        """Keep trying in the background so the app is usable as soon as the
        headphones are switched on."""
        if self.retry_timer is not None:
            return

        def tick(_timer):
            self.retry_timer = None
            if not self.link.connected:
                self._connect_and_load()

        self.retry_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            20.0, False, tick)
        self._status(T("耳机未连接，将自动重试", "Headphones not connected. Retrying automatically."))

    @objc.python_method
    def _start_heartbeat(self):
        if self.hb_timer is not None:
            return
        def beat(_timer):
            if self.busy or self.pending:
                return
            if not self.link.connected:
                self._connect_and_load()
                return
            self.busy = True
            try:
                self.view.battery.setStringValue_("%d%%" % self.link.battery())
            except Exception as e:
                log("heartbeat lost: %s" % e)
                self.link.close()
                self._set_controls_enabled(False)
                self._schedule_retry()
            finally:
                self.busy = False
        self.hb_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(60.0, True, beat)

    @objc.python_method
    def _set_cnc_enabled(self, on: bool):
        if self.view is not None:
            self.view.cnc.setEnabled_(on)

    @objc.python_method
    def _set_controls_enabled(self, on: bool):
        if self.view is None:
            return
        info = self.slot_info or {}
        editable = (on and self.mode_target is None
                    and info.get("editable", False) and self.slot in EDITABLE_SLOTS)
        self.view.cnc.setEnabled_(editable and info.get("cnc_supported", False) and not info.get("wind", False))
        self.view.wind.setEnabled_(editable and info.get("wind_supported", False))
        if not on:
            note = T("连接耳机后可修改设置", "Connect the headphones to change settings")
        elif self.mode_target is not None:
            note = T("正在切换模式，请稍候…", "Switching mode…")
        elif not editable:
            note = T("切到自定义模式后可设置抗风噪", "Wind block is available in a custom mode")
        elif info.get("wind"):
            note = T("抗风噪已开，CNC 固定为 0；关闭后可调节", "Wind block is on, so CNC stays at 0 until it is off")
        else:
            note = T("开启抗风噪会将当前自定义模式的 CNC 设为 0", "Turning on wind block sets this custom mode's CNC to 0")
        self.view.wind_note.setStringValue_(note)
        for item in self.view.eq_sliders.values():
            item.setEnabled_(on)
        for item in self.view.preset_buttons.values():
            item.setEnabled_(on)
        for item in self.view.mode_buttons.values():
            item.setEnabled_(on)
        self.view.sidetone.setEnabled_(on and "sidetone" in self.features)
        self.view.standby.setEnabled_(on and "standby" in self.features)
        voice = self.features.get("voice", {})
        self.view.prompts.setEnabled_(on and voice.get("can_toggle", False))
        self.view.language.setEnabled_(on and bool(voice.get("languages")))

    @objc.python_method
    def _sync_mode_buttons(self, slot: int):
        if not self.view:
            return
        for s, b in self.view.mode_buttons.items():
            b.setState_(1 if s == slot else 0)

    @objc.python_method
    def _sync_preset_buttons(self):
        if not self.view:
            return
        current = tuple(int(round(self.view.eq_sliders[bid].doubleValue()))
                        for _name, bid in EQ_BANDS)
        for i, (_name, values) in enumerate(EQ_PRESETS):
            self.view.preset_buttons[i].setState_(1 if values == current else 0)

    # ---------------------------------------------------------- serialized edits
    @objc.python_method
    def _schedule(self, key: str, fn, delay: float = 0.18):
        # A replacement is a new gesture: move it after intervening controls.
        self.pending.pop(key, None)
        self.pending[key] = fn
        if self.timer is not None:
            self.timer.invalidate()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            delay, False, lambda t: self._flush())

    @objc.python_method
    def _flush(self):
        self.timer = None
        if self.busy:
            self.timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                0.1, False, lambda t: self._flush())
            return
        work = list(self.pending.values())
        self.pending.clear()
        self.busy = True
        messages, errors = [], []
        try:
            for fn in work:
                try:
                    messages.append(fn() or T("设置已保存并确认", "Setting saved"))
                except Exception as e:
                    log("apply failed: %s" % e)
                    errors.append(str(e))
        finally:
            self.busy = False
        self._refresh()
        if not self.link.connected:
            return
        if errors:
            self._status(T("设置未确认：%s" % errors[-1], "Setting was not confirmed: %s" % errors[-1]))
        elif messages:
            self._status(messages[-1])

    @objc.python_method
    def _cancel_pending(self):
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        self.pending.clear()
        self.mode_target = None

    # ----------------------------------------------------------- actions
    def cncChanged_(self, sender):
        if self.mode_target is not None:
            return
        level, slot = int(round(sender.doubleValue())), self.slot
        self.view.cnc_value.setStringValue_(str(level))
        def apply():
            self.link.set_cnc(level, slot)
            return T("CNC %d · 已保存并确认" % level, "CNC %d saved" % level)
        self._schedule("cnc_%d" % slot, apply)

    def eqChanged_(self, sender):
        bid, value = sender.tag(), int(round(sender.doubleValue()))
        self.view.eq_values[bid].setStringValue_("%+d" % value)
        self._sync_preset_buttons()
        def apply():
            self.link.set_eq(bid, value)
            return T("均衡器已保存并确认", "Equalizer saved")
        self._schedule("eq%d" % bid, apply)

    def presetClicked_(self, sender):
        name, values = EQ_PRESETS[sender.tag()]
        name = T(*PRESET_TEXT[name])
        for bid, value in enumerate(values):
            self.view.eq_sliders[bid].setDoubleValue_(float(value))
            self.view.eq_values[bid].setStringValue_("%+d" % value)
        self._sync_preset_buttons()
        for bid in (0, 1, 2):
            self.pending.pop("eq%d" % bid, None)
        def apply():
            for bid, value in enumerate(values):
                self.link.set_eq(bid, value)
            return T("%s预设已保存并确认" % name, "%s preset saved" % name)
        self._schedule("eq_preset", apply, 0.05)

    def modeClicked_(self, sender):
        slot = sender.tag()
        # Stop slot-specific gestures until the new slot has been confirmed.
        self.mode_target = slot
        self.view.cnc.setEnabled_(False)
        self.view.wind.setEnabled_(False)
        self.view.wind_note.setStringValue_(T("正在切换模式，请稍候…", "Switching mode…"))
        self._sync_mode_buttons(slot)
        def apply():
            try:
                self.link.set_mode(slot)
                self.slot = slot
                return T("模式已切换", "Mode changed")
            finally:
                if self.mode_target == slot:
                    self.mode_target = None
        self._schedule("mode", apply, 0.05)

    def windChanged_(self, sender):
        if self.mode_target is not None:
            return
        enabled, slot = bool(sender.state()), self.slot
        def apply():
            self.link.set_wind(enabled, slot)
            return T("抗风噪已%s并确认" % ("开启" if enabled else "关闭"), "Wind block %s" % ("on" if enabled else "off"))
        self._schedule("wind_%d" % slot, apply, 0.05)

    def sidetoneChanged_(self, sender):
        value = sender.selectedItem().tag()
        def apply():
            self.link.set_sidetone(value)
            return T("通话自声已保存并确认", "Sidetone saved")
        self._schedule("sidetone", apply, 0.05)

    def promptsChanged_(self, sender):
        enabled = bool(sender.state())
        def apply():
            self.link.set_voice(enabled=enabled)
            return T("语音提示已%s并确认" % ("开启" if enabled else "关闭"), "Voice prompts %s" % ("on" if enabled else "off"))
        self._schedule("voice_enabled", apply, 0.05)

    def languageChanged_(self, sender):
        language = sender.selectedItem().tag()
        def apply():
            self.link.set_voice(language=language)
            return T("提示语言已保存并确认", "Prompt language saved")
        self._schedule("voice_language", apply, 0.05)

    def standbyChanged_(self, sender):
        minutes = sender.selectedItem().tag()
        def apply():
            self.link.set_standby(minutes)
            return T("自动待机已保存并确认", "Auto standby saved")
        self._schedule("standby", apply, 0.05)

    def uiLanguageChanged_(self, sender):
        lang = "zh-Hans" if sender.selectedItem().tag() == 0 else "en"
        set_language(lang, persist=True)
        self.view.apply_language()
        self._apply_feature_labels()
        connected = bool(self.link.connected)
        self._set_controls_enabled(connected)
        if connected:
            self.view.title.setStringValue_("Bose QC45")
            if len(self.features) == 3:
                self._status(T("已连接 · 修改后自动保存", "Connected · changes save automatically"))
            else:
                self._status(T("已连接 · 部分设置暂不可用", "Connected · some settings are unavailable"))
        else:
            self.view.title.setStringValue_(T("Bose QC45 · 未连接", "Bose QC45 · Not connected"))
            self._status(T("耳机未连接，将自动重试", "Headphones not connected. Retrying automatically."))

    def reconnectClicked_(self, sender):
        if self.busy:
            self._status(T("正在处理设置，请稍候", "Still saving a setting, please wait"))
            return
        self._cancel_pending()
        self.link.close()
        self._connect_and_load()

    def quitClicked_(self, sender):
        if self.busy:
            self._status(T("正在处理设置，请稍候再退出", "Still saving a setting. Quit in a moment."))
            return
        self._cancel_pending()
        self.link.close()
        NSApplication.sharedApplication().terminate_(None)


def self_test() -> int:
    """Connect, read state and render the view; used to verify the bundle
    can reach the headphones without needing to see the menu bar."""
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    # Inside an .app bundle NSApplication expects a launch to complete before
    # views are usable; without this the self-test blocks.
    try:
        app.finishLaunching()
    except Exception:
        pass
    ctrl = AppController.alloc().init()
    v = ControlView.alloc().initWithFrame_(NSMakeRect(0, 0, POP_W, POP_H))
    v.setupWith_(ctrl)
    ctrl.view = v
    ctrl._connect_and_load()
    print("connected:", ctrl.link.connected)
    print("battery  :", v.battery.stringValue())
    print("slot     :", ctrl.slot)
    print("cnc      :", v.cnc_value.stringValue())
    print("eq       :", {k: s.stringValue() for k, s in v.eq_values.items()})
    print("status   :", v.status.stringValue())
    print("settings :", {k: {n: value for n, value in v.items() if n != "raw"}
                          for k, v in ctrl.features.items()})
    ok = ctrl.link.connected and len(ctrl.features) == 3
    if ctrl.link.connected:
        ctrl.link.close()
    return 0 if ok else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    ctrl = AppController.alloc().init()
    app.setDelegate_(ctrl)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
