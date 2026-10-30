"""QC45 controls with validated replies, pre-write backups and readback.

No Bluetooth or AppKit imports: the caller supplies a main-thread exchange.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import uuid

from i18n import T


class SettingsError(RuntimeError):
    pass


class ReplyMissing(SettingsError):
    """An absent acknowledgement is an unknown outcome until GET resolves it."""
    pass


LANGUAGE_LABELS = {
    0: ("英语（英国）", "English (UK)"),
    1: ("英语（美国）", "English (US)"),
    2: ("法语", "French"),
    3: ("意大利语", "Italian"),
    4: ("德语", "German"),
    5: ("西班牙语（欧洲）", "Spanish (Europe)"),
    6: ("西班牙语（墨西哥）", "Spanish (Mexico)"),
    7: ("葡萄牙语（巴西）", "Portuguese (Brazil)"),
    8: ("普通话", "Mandarin"),
    9: ("韩语", "Korean"),
    10: ("俄语", "Russian"),
    11: ("波兰语", "Polish"),
    12: ("希伯来语", "Hebrew"),
    13: ("土耳其语", "Turkish"),
    14: ("荷兰语", "Dutch"),
    15: ("日语", "Japanese"),
    16: ("粤语", "Cantonese"),
    17: ("阿拉伯语", "Arabic"),
    18: ("瑞典语", "Swedish"),
    19: ("丹麦语", "Danish"),
    20: ("挪威语", "Norwegian"),
    21: ("芬兰语", "Finnish"),
    22: ("印地语", "Hindi"),
}
SIDETONE_OPTIONS = (
    (0, "关", "Off"), (3, "低", "Low"), (2, "中", "Medium"), (1, "高", "High"))
STANDBY_OPTIONS = (
    (0, "永不", "Never"), (5, "5 分钟", "5 minutes"),
    (20, "20 分钟", "20 minutes"), (40, "40 分钟", "40 minutes"),
    (60, "1 小时", "1 hour"), (180, "3 小时", "3 hours"),
    (1440, "24 小时", "24 hours"))


def voice_language_name(index):
    pair = LANGUAGE_LABELS.get(index)
    if pair:
        return T(pair[0], pair[1])
    return T("语言 %d" % index, "Language %d" % index)


def option_label(option):
    return T(option[1], option[2])


def parse_voice(p):
    if len(p) < 5:
        raise SettingsError(T("语音设置回复不完整", "Incomplete voice-prompt reply"))
    mask = int.from_bytes(p[1:5], "big")
    return {"enabled": bool(p[0] & 0x20), "language": p[0] & 0x1f,
            "can_toggle": bool(p[0] & 0x80),
            "languages": [i for i in range(32) if mask & (1 << i)],
            "battery_supported": len(p) >= 7 and bool(p[5] & 1),
            "battery_enabled": len(p) >= 7 and bool(p[6] & 1), "raw": p}


def parse_standby(p):
    if not p:
        raise SettingsError(T("待机设置回复不完整", "Incomplete standby reply"))
    wide = len(p) >= 3
    return {"minutes": p[0] | (p[2] << 8 if wide else 0),
            "wide": wide, "raw": p}


class DeviceSettings:
    def __init__(self, exchange, backup_dir=None):
        self.exchange = exchange
        self.backup_dir = Path(backup_dir or "~/.qc45/backups").expanduser()
        self.last_backup = None

    def _request(self, group, function, operator=1, payload=b""):
        packet = bytes([group, function, operator, len(payload)]) + payload
        data = self.exchange(packet)
        pos, matches = 0, []
        while pos + 4 <= len(data):
            g, f, flags, n = data[pos:pos + 4]
            end = pos + 4 + n
            if end > len(data):
                break
            if (g, f) == (group, function):
                matches.append((flags & 15, data[pos + 4:end]))
            pos = end
        if not matches:
            raise ReplyMissing(T("没有收到完整的耳机回复", "No complete headphone reply"))
        for op, p in matches:
            if op == 4:
                code = p[0] if p else -1
                names = {1: T("数据长度不符", "Wrong data length"), 4: T("耳机不支持此功能", "The headphones do not support this"),
                         5: T("耳机拒绝写入", "The headphones rejected the write"), 6: T("耳机不接受此值", "The headphones rejected this value"), 8: T("耳机暂时无法应用", "The headphones cannot apply this yet")}
                raise SettingsError(names.get(code, T("耳机返回错误 %s" % code, "Headphone error %s" % code)))
        accepted = (3, 6, 7) if operator == 5 else (3,)
        for op, p in reversed(matches):
            if op in accepted:
                return p
        raise SettingsError(T("耳机回复类型不正确", "Unexpected headphone reply"))

    def _change(self, label, group, function, payload, before, restore,
                verify, query=b"", operator=2):
        # A failed backup must prevent sending the mutation.
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone()
        path = self.backup_dir / ("menu-%s-%s.json" %
            (stamp.strftime("%Y%m%d-%H%M%S-%f"), uuid.uuid4().hex[:6]))
        record = {"format": "qc45-menu-change-v1", "created_at": stamp.isoformat(),
                  "setting": label, "address": [group, function],
                  "before_status_hex": before.hex(),
                  "request_hex": (bytes([group, function, operator, len(payload)]) + payload).hex(),
                  "restore_request_hex": (bytes([group, function, operator, len(restore)]) + restore).hex(),
                  "read_query_hex": (bytes([group, function, 1, len(query)]) + query).hex()}
        with path.open("x", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
            f.write("\n")
        self.last_backup = str(path)
        try:
            self._request(group, function, operator, payload)
        except ReplyMissing:
            # QC45 can apply VoicePrompts without returning its SETGET reply.
            # Resolve with GET; do not resend a potentially applied mutation.
            pass
        actual = self._request(group, function, 1, query)
        if not verify(actual):
            raise SettingsError(T("耳机回读与所选值不一致；已保存原设置", "Readback did not match the chosen value. The previous setting was saved."))
        return actual

    def battery(self):
        p = self._request(2, 2)
        if not p or p[0] > 100:
            raise SettingsError(T("电量回复无效", "Invalid battery reply"))
        return p[0]

    def current_slot(self):
        p = self._request(31, 3)
        if len(p) != 1 or p[0] not in range(4):
            raise SettingsError(T("模式回复无效", "Invalid mode reply"))
        return p[0]

    def read_slot(self, slot):
        if slot not in range(4):
            raise SettingsError(T("模式编号无效", "Invalid mode number"))
        p = self._request(31, 6, payload=bytes([slot]))
        if len(p) != 47 or p[0] != slot:
            raise SettingsError(T("模式配置回复无效", "Invalid mode-configuration reply"))
        return {"slot": slot, "cnc": p[42], "wind": bool(p[46]),
                "editable": bool(p[3]), "wind_supported": bool(p[41] & 8),
                "cnc_supported": bool(p[41] & 1), "raw": p}

    @staticmethod
    def _mode_payload(raw):
        return raw[:3] + raw[6:38] + raw[42:45] + raw[46:47]

    def _set_mode_field(self, slot, *, cnc=None, wind=None):
        info = self.read_slot(slot)
        if not info["editable"] or slot not in (2, 3):
            raise SettingsError(T("请先选择自定义模式", "Choose a custom mode first"))
        if wind is not None and not info["wind_supported"]:
            raise SettingsError(T("这个模式不支持抗风噪", "This mode does not support wind block"))
        if cnc is not None and not info["cnc_supported"]:
            raise SettingsError(T("这个模式不支持调节 CNC", "This mode does not support CNC"))
        before = info["raw"]
        restore = self._mode_payload(before)
        payload = bytearray(restore)
        if cnc is not None:
            payload[35], payload[38] = cnc, 0
        if wind is not None:
            payload[38] = int(wind)
            if wind:
                payload[35] = 0
        desired = bytes(payload)
        if desired == restore:
            return info
        self._change("mode_%d" % slot, 31, 6, desired, before, restore,
                     lambda p: len(p) == 47 and self._mode_payload(p) == desired,
                     query=bytes([slot]))
        return self.read_slot(slot)

    def set_wind(self, enabled, slot):
        if not isinstance(enabled, bool):
            raise SettingsError(T("抗风噪需要开关值", "Wind block needs an on or off value"))
        return self._set_mode_field(slot, wind=enabled)

    def set_cnc(self, level, slot):
        if type(level) is not int or not 0 <= level <= 10:
            raise SettingsError(T("CNC 范围为 0–10", "CNC must be from 0 to 10"))
        return self._set_mode_field(slot, cnc=level)

    def read_eq(self):
        p = self._request(1, 7)
        if len(p) < 12 or len(p) % 4:
            raise SettingsError(T("均衡器回复不完整", "Incomplete equalizer reply"))
        return {p[i+3]: int.from_bytes(p[i+2:i+3], "big", signed=True)
                for i in range(0, len(p), 4)}

    def set_eq(self, band_id, value):
        if band_id not in (0, 1, 2) or type(value) is not int or not -10 <= value <= 10:
            raise SettingsError(T("均衡器范围为 −10–10", "Equalizer values must be from -10 to 10"))
        p = self._request(1, 7)
        if len(p) < 12 or len(p) % 4:
            raise SettingsError(T("均衡器回复不完整", "Incomplete equalizer reply"))
        band = next((p[i:i+4] for i in range(0, len(p), 4) if p[i+3] == band_id), None)
        if band is None:
            raise SettingsError(T("耳机未返回该频段", "The headphones did not return that band"))
        if band[2] == value & 255:
            return
        self._change("eq_%d" % band_id, 1, 7, bytes([value & 255, band_id]), p,
                     bytes([band[2], band_id]),
                     lambda a: len(a) % 4 == 0 and any(a[i+3] == band_id and a[i+2] == value & 255
                         for i in range(0, len(a), 4)))

    def set_mode(self, slot):
        if slot not in range(4):
            raise SettingsError(T("模式编号无效", "Invalid mode number"))
        before = self.current_slot()
        if before == slot:
            return
        self._change("current_mode", 31, 3, bytes([slot, 0]), bytes([before]),
                     bytes([before, 0]), lambda a: a == bytes([slot]), operator=5)

    def read_voice(self):
        return parse_voice(self._request(1, 3))

    @staticmethod
    def _voice_payload(info, enabled, language):
        data = bytes([(int(enabled) << 5) | language])
        if info["battery_supported"]:
            data += bytes([int(info["battery_enabled"])])
        return data

    def set_voice(self, *, enabled=None, language=None):
        old = self.read_voice()
        if enabled is not None and (not isinstance(enabled, bool) or not old["can_toggle"]):
            raise SettingsError(T("耳机不支持切换语音提示", "The headphones cannot toggle voice prompts"))
        enabled = old["enabled"] if enabled is None else enabled
        language = old["language"] if language is None else language
        if language not in old["languages"]:
            raise SettingsError(T("耳机不支持这种提示语言", "The headphones do not support this prompt language"))
        new = self._voice_payload(old, enabled, language)
        restore = self._voice_payload(old, old["enabled"], old["language"])
        if new != restore:
            def verified(p):
                got = parse_voice(p)
                return (got["enabled"] == enabled and got["language"] == language
                        and got["battery_enabled"] == old["battery_enabled"])
            self._change("voice_prompts", 1, 3, new, old["raw"], restore, verified)
        return self.read_voice()

    def read_sidetone(self):
        p = self._request(1, 11)
        if len(p) < 3:
            raise SettingsError(T("自声设置回复不完整", "Incomplete sidetone reply"))
        return {"level": p[1], "supported": [i for i in range(4) if p[2] & (1 << i)], "raw": p}

    def set_sidetone(self, level):
        old = self.read_sidetone()
        if level not in old["supported"]:
            raise SettingsError(T("耳机不支持这个自声档位", "The headphones do not support this sidetone level"))
        if level != old["level"]:
            self._change("sidetone", 1, 11, bytes([1, level]), old["raw"],
                         old["raw"][:2], lambda p: len(p) >= 2 and p[1] == level)
        return self.read_sidetone()

    def read_standby(self):
        return parse_standby(self._request(1, 4))

    def set_standby(self, minutes):
        old = self.read_standby()
        if type(minutes) is not int or not 0 <= minutes <= (65535 if old["wide"] else 255):
            raise SettingsError(T("这个待机时间超出耳机支持范围", "That standby time is outside the supported range"))
        def payload(n):
            return n.to_bytes(1 if n <= 255 else 2, "little")
        if minutes != old["minutes"]:
            self._change("standby", 1, 4, payload(minutes), old["raw"], payload(old["minutes"]),
                         lambda p: parse_standby(p)["minutes"] == minutes)
        return self.read_standby()
