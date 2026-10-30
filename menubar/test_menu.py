"""Native callback regression tests. No Bluetooth connection is opened."""
import tempfile
import unittest

import os
import re
from pathlib import Path

from AppKit import NSApplication, NSMakeRect
from i18n import current_language, load_language, set_language
from qc45menu import AppController, ControlView, POP_W, POP_H, normalize_mac, select_qc45
from qc45_settings import DeviceSettings
from test_settings import Wire, SLOT


class MenuWire(Wire):
    def __init__(self):
        super().__init__()
        self.slots = {}
        for i in range(4):
            raw = bytearray(SLOT)
            raw[0], raw[3] = i, int(i >= 2)
            self.slots[i] = bytes(raw)

    def __call__(self, packet):
        if packet[:2] == b'\x1f\x06':
            slot = packet[4]
            self.state[31, 6] = self.slots[slot]
            response = super().__call__(packet)
            self.slots[slot] = self.state[31, 6]
            return response
        return super().__call__(packet)


class TestLink(DeviceSettings):
    connected = True


class MenuTests(unittest.TestCase):
    def setUp(self):
        set_language("zh-Hans")
        NSApplication.sharedApplication()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wire = MenuWire()
        self.wire.state[31, 3] = b'\3'
        self.ctrl = AppController.alloc().init()
        self.ctrl.link = TestLink(self.wire, backup_dir=self.tmp.name)
        self.view = ControlView.alloc().initWithFrame_(NSMakeRect(0, 0, POP_W, POP_H))
        self.view.setupWith_(self.ctrl)
        self.ctrl.view = self.view
        self.addCleanup(self.ctrl._cancel_pending)
        self.ctrl._refresh()

    def flush(self):
        if self.ctrl.timer:
            self.ctrl.timer.invalidate()
        self.ctrl._flush()

    def test_quick_edits_to_different_controls_all_reach_device(self):
        self.view.eq_sliders[0].setDoubleValue_(4)
        self.ctrl.eqChanged_(self.view.eq_sliders[0])
        self.view.eq_sliders[1].setDoubleValue_(-3)
        self.ctrl.eqChanged_(self.view.eq_sliders[1])
        self.view.eq_sliders[0].setDoubleValue_(6)
        self.ctrl.eqChanged_(self.view.eq_sliders[0])
        self.flush()
        self.assertEqual(self.ctrl.link.read_eq(), {0: 6, 1: -3, 2: 0})
        self.assertEqual(len(self.wire.writes), 2)

    def test_pending_cnc_keeps_its_origin_slot_after_mode_changes(self):
        self.view.cnc.setDoubleValue_(5)
        self.ctrl.cncChanged_(self.view.cnc)
        self.ctrl.slot = 0
        self.flush()
        mode_writes = [p for p in self.wire.writes if p[:2] == b'\x1f\x06']
        self.assertEqual(len(mode_writes), 1)
        self.assertEqual(mode_writes[0][4], 3)

    def test_edit_arriving_during_write_is_not_lost_or_reentrant(self):
        base = self.wire
        injected = []
        def exchange(packet):
            if packet[2] == 2 and not injected:
                injected.append(True)
                self.view.eq_sliders[1].setDoubleValue_(-2)
                self.ctrl.eqChanged_(self.view.eq_sliders[1])
                self.flush()  # Simulates a timer firing in the Bluetooth run loop.
                self.assertEqual(self.wire.writes, [])
            return base(packet)
        self.ctrl.link.exchange = exchange
        self.view.eq_sliders[0].setDoubleValue_(5)
        self.ctrl.eqChanged_(self.view.eq_sliders[0])
        self.flush()
        self.assertEqual(self.ctrl.link.read_eq(), {0: 5, 1: 0, 2: 0})
        self.flush()
        self.assertEqual(self.ctrl.link.read_eq(), {0: 5, 1: -2, 2: 0})

    def test_supported_choices_and_wind_cnc_lock(self):
        languages = [x.tag() for x in self.view.language.itemArray()]
        self.assertEqual(languages, [1, 2, 3, 4, 6, 8, 15, 16])
        self.assertNotIn(1440, [x.tag() for x in self.view.standby.itemArray()])
        self.view.wind.setState_(1)
        self.ctrl.windChanged_(self.view.wind)
        self.flush()
        self.assertFalse(self.view.cnc.isEnabled())
        self.assertTrue(self.view.wind.isEnabled())
        self.assertEqual(self.ctrl.link.read_slot(3)['cnc'], 0)

    def test_mode_transition_blocks_slot_edits_until_confirmed(self):
        base = self.wire
        def exchange(packet):
            if packet[:3] == b'\x1f\x03\x05':
                self.assertFalse(self.view.cnc.isEnabled())
                self.assertFalse(self.view.wind.isEnabled())
                self.view.cnc.setDoubleValue_(6)
                self.ctrl.cncChanged_(self.view.cnc)
            return base(packet)
        self.ctrl.link.exchange = exchange
        self.ctrl.modeClicked_(self.view.mode_buttons[2])
        self.flush()
        self.flush()
        self.assertEqual(self.ctrl.slot, 2)
        self.assertTrue(self.view.cnc.isEnabled())
        self.assertFalse(any(p[:2] == b'\x1f\x06' for p in self.wire.writes))

    def test_last_cnc_gesture_wins_over_intervening_wind_toggle(self):
        self.view.cnc.setDoubleValue_(4)
        self.ctrl.cncChanged_(self.view.cnc)
        self.view.wind.setState_(1)
        self.ctrl.windChanged_(self.view.wind)
        self.view.cnc.setDoubleValue_(6)
        self.ctrl.cncChanged_(self.view.cnc)
        self.flush()
        state = self.ctrl.link.read_slot(3)
        self.assertEqual((state['cnc'], state['wind']), (6, False))

    def test_refresh_does_not_reset_a_gesture_received_during_io(self):
        base = self.wire
        injected = []
        def exchange(packet):
            if packet[:2] == b'\x02\x02' and not injected:
                injected.append(True)
                self.view.prompts.setState_(0)
                self.ctrl.promptsChanged_(self.view.prompts)
            return base(packet)
        self.ctrl.link.exchange = exchange
        self.ctrl._refresh()
        self.assertEqual(self.view.prompts.state(), 0)
        self.flush()
        self.assertFalse(self.ctrl.link.read_voice()['enabled'])

    def test_disconnect_during_optional_read_disables_panel_and_retries(self):
        base = self.wire
        def exchange(packet):
            if packet[:2] == b'\x01\x03':
                self.ctrl.link.connected = False
                raise RuntimeError('connection closed')
            return base(packet)
        self.ctrl.link.exchange = exchange
        self.ctrl._refresh()
        self.assertFalse(self.view.eq_sliders[0].isEnabled())
        self.assertFalse(self.view.preset_buttons[0].isEnabled())
        self.assertFalse(self.view.mode_buttons[0].isEnabled())
        self.assertIn('未连接', self.view.status.stringValue())
        self.assertIsNotNone(self.ctrl.retry_timer)
        self.ctrl.retry_timer.invalidate()

    def test_refresh_does_not_write_an_eq_preset(self):
        self.assertEqual(self.wire.writes, [])
        self.assertEqual([b.state() for b in self.view.preset_buttons.values()], [0, 0, 0])
        self.assertEqual([b.title() for b in self.view.preset_buttons.values()], ["说唱", "流行", "摇滚"])

    def test_genre_presets_write_the_published_curves(self):
        expected = {
            0: {0: 6, 1: 1, 2: -2},
            1: {0: 4, 1: 2, 2: -2},
            2: {0: 2, 1: 3, 2: -1},
        }
        for index, curve in expected.items():
            self.ctrl.presetClicked_(self.view.preset_buttons[index])
            self.flush()
            self.assertEqual(self.ctrl.link.read_eq(), curve)
            self.assertEqual(
                [b.state() for b in self.view.preset_buttons.values()],
                [1 if i == index else 0 for i in range(3)])

    def test_preset_replaces_a_pending_slider_change(self):
        self.view.eq_sliders[0].setDoubleValue_(9)
        self.ctrl.eqChanged_(self.view.eq_sliders[0])
        self.ctrl.presetClicked_(self.view.preset_buttons[1])
        self.flush()
        self.assertEqual(self.ctrl.link.read_eq(), {0: 4, 1: 2, 2: -2})

    def test_slider_after_preset_keeps_only_that_nudge(self):
        self.ctrl.presetClicked_(self.view.preset_buttons[0])
        self.view.eq_sliders[2].setDoubleValue_(0)
        self.ctrl.eqChanged_(self.view.eq_sliders[2])
        self.flush()
        self.assertEqual(self.ctrl.link.read_eq(), {0: 6, 1: 1, 2: 0})
        self.assertEqual([b.state() for b in self.view.preset_buttons.values()], [0, 0, 0])

    def test_loaded_curve_highlights_the_matching_preset(self):
        self.wire.state[1, 7] = bytes.fromhex("f60a0400f60a0201f60afe02")
        self.ctrl._refresh()
        self.assertEqual([b.state() for b in self.view.preset_buttons.values()], [0, 1, 0])
        self.assertEqual(self.wire.writes, [])


    def test_english_labels_do_not_write_to_the_headphones(self):
        before = len(self.wire.writes)
        set_language("en")
        self.addCleanup(lambda: set_language("zh-Hans"))
        self.view.apply_language()
        self.assertEqual(self.view.preset_buttons[0].title(), "Hip-Hop")
        self.assertEqual(self.view.mode_buttons[0].title(), "Quiet")
        self.assertEqual(self.view.reconnect.title(), "Reconnect")
        self.assertEqual(self.view.ui_lang_label.stringValue(), "Language")
        self.assertEqual(len(self.wire.writes), before)

    def test_language_switch_updates_the_open_panel(self):
        os.environ["QC45_LANG_FILE"] = os.path.join(self.tmp.name, "ui-language")
        self.addCleanup(lambda: os.environ.pop("QC45_LANG_FILE", None))
        self.view.ui_lang.selectItemWithTag_(1)
        self.ctrl.uiLanguageChanged_(self.view.ui_lang)
        self.addCleanup(lambda: set_language("zh-Hans"))
        self.assertEqual(current_language(), "en")
        self.assertEqual(self.view.preset_buttons[2].title(), "Rock")
        self.assertEqual(self.view.language.itemAtIndex_(0).title(), "English (US)")
        self.assertEqual(self.wire.writes, [])

    def test_connected_qc45_is_chosen_without_a_personal_address(self):
        chosen = select_qc45([
            ("11-22-33-44-55-66", "qc35", True),
            ("aa:bb:cc:dd:ee:01", "qc45", False),
            ("aa:bb:cc:dd:ee:02", "qc45", True),
        ])
        self.assertEqual(chosen, "AA:BB:CC:DD:EE:02")
        self.assertEqual(normalize_mac("aa-bb-cc-dd-ee-03"), "AA:BB:CC:DD:EE:03")
        override = select_qc45([], override="10-20-30-40-50-60")
        self.assertEqual(override, "10:20:30:40:50:60")

    def test_saved_interface_language_is_not_a_headphone_write(self):
        path = os.path.join(self.tmp.name, "ui-language")
        os.environ["QC45_LANG_FILE"] = path
        self.addCleanup(lambda: os.environ.pop("QC45_LANG_FILE", None))
        self.addCleanup(lambda: set_language("zh-Hans"))
        set_language("en", persist=True)
        self.assertEqual(load_language(), "en")
        self.assertEqual(self.wire.writes, [])

    def test_menu_has_no_hardcoded_headphone_address(self):
        body = Path(os.path.dirname(os.path.abspath(__file__)), "qc45menu.py").read_text(encoding="utf-8")
        self.assertNotIn("DEFAULT_MAC", body)
        self.assertIsNone(re.search(r'QC45_MAC",\s*"[0-9A-Fa-f:-]{17}"', body))



if __name__ == '__main__':
    unittest.main()
